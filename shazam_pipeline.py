"""YouTube 長影片 -> Shazam/ACRCloud 辨識 -> 單一 Spotify 清單（整晚安全模式）。

針對 DJ mix / 合輯這類「未標記長影片」。設計成可以丟幾個 30-50 分鐘的網址跑一整晚：

- 多網址：一次給多個 URL（或互動一行一個），每個可以是單片或 playlist。
- 安全優先（不被 ban）：保守 pacing（見 recognizer.py），序列不並行；某片被限流時
  「冷卻一下、跳過該片、繼續下一支」，連續多片都掛才整個放棄。
- 省 API：辨識引擎只建一次（某引擎額度用完被停用後就不再浪費呼叫它）；命中即快進。
- 每找到一首立刻 (1) 寫進 tracklist 檔 (2) 加進 Spotify 清單 —— 中途中斷也有成果。
- 一定留下歌名：找到的當下「先寫檔、再碰 Spotify」，就算 API 到頂或被 ban，
  tracklist 檔仍保有所有歌名，可手動去找/加。每片辨識完也寫 .shazam-cache checkpoint。

用法：
    python3 shazam_pipeline.py                         互動：一行一個網址貼，空白 Enter 結束
    python3 shazam_pipeline.py URL1 URL2 ... [清單名] [--no-spotify] [--refresh]

旗標：
    --no-spotify   只辨識並寫 tracklist 檔，完全不碰 Spotify（不需登入）
    --refresh      忽略 .shazam-cache 既有 checkpoint，重新辨識
"""

import asyncio
import datetime
import os
import shutil
import sys
import tempfile
import time

import audio_source
import config
import recognizer
import slicer
import spotify_client
from recognizer import (
    ACRCloudEngine,
    EngineChain,
    RecognitionBlocked,
    ShazamRecognizer,
    scan_track,
)
from utils import normalize
from waterfall import Waterfall

COOLDOWN_SEC = 90            # 某片被限流後，冷卻多久再繼續下一支
MAX_CONSECUTIVE_BLOCKS = 3   # 連續幾片都被限流才整個放棄


def _build_recognizer():
    """Shazam 一定有；config 三個 ACRCloud 金鑰都填了才加 ACRCloud 當第二引擎。"""
    engines = [ShazamRecognizer()]
    if config.ACRCLOUD_HOST and config.ACRCLOUD_ACCESS_KEY and config.ACRCLOUD_ACCESS_SECRET:
        engines.append(ACRCloudEngine(
            config.ACRCLOUD_HOST, config.ACRCLOUD_ACCESS_KEY, config.ACRCLOUD_ACCESS_SECRET))
        print("  第二引擎: ACRCloud 已啟用", flush=True)
    return EngineChain(engines)


def _mmss(sec):
    m, s = divmod(max(0, int(sec)), 60)
    return f"{m:02d}:{s:02d}"


def _safe_filename(text):
    return "".join(c if c not in r'\/:*?"<>|' else "_" for c in text)


_STATUS_LABEL = {
    "spotify_direct": "已加入(ACRCloud 直接給 Spotify ID)",
    "spotify": "已加入",
    "spotify_via_ytmusic": "已加入(經 YT Music)",
    "spotify_via_soundcloud": "已加入(經 SoundCloud)",
    "not_found": "Spotify 無",
    "recognized": "辨識到(未檢查 Spotify)",
    "spotify_error": "Spotify 失敗(已記名)",
}


class Progress:
    """單支影片的掃描進度條（純文字，用 \\r 即時更新）。"""

    def __init__(self, duration):
        self.duration = duration or 1.0
        self.start = time.monotonic()
        self.windows = 0
        self.hits = 0

    def update(self, pos, track):
        self.windows += 1
        pct = min(1.0, pos / self.duration)
        elapsed = time.monotonic() - self.start
        eta = (elapsed / pct - elapsed) if pct > 0.02 else 0
        avg_step = pos / self.windows if self.windows else 0
        remain = int((self.duration - pos) / avg_step) if avg_step > 0 else 0
        bar_n = 24
        filled = int(bar_n * pct)
        bar = "#" * filled + "-" * (bar_n - filled)
        print(f"\r  [{bar}] {pct * 100:3.0f}%  {_mmss(pos)}/{_mmss(self.duration)}  "
              f"命中 {self.hits}  視窗 {self.windows}(剩~{remain})  ETA {_mmss(eta)}  ",
              end="", flush=True)

    def found(self, pos, song):
        self.hits += 1
        print(f"\r{' ' * 92}\r    {_mmss(pos)} 命中: {song['song']} - {song['artist']}", flush=True)

    def done(self):
        print()


class TrackSink:
    """歸戶器：每找到一首就 (1) 先寫進 tracklist 檔 (2) 再加進 Spotify。

    『先寫檔、後 Spotify』+ Spotify 包 try/except，保證就算 API 到頂/被 ban，歌名一定留得下來。
    跨影片依「正規化 歌名+歌手」去重；累積 results 給最後總結。
    """

    def __init__(self, tracklist_path, sp=None, wf=None, playlist_id=None, existing=None):
        self.path = tracklist_path
        self.sp = sp
        self.wf = wf
        self.playlist_id = playlist_id
        self.existing = existing if existing is not None else set()
        self.seen = set()
        self.results = []

    def _append(self, song, status):
        ts = _mmss(song["pos"]) if song.get("pos") is not None else "--:--"
        label = _STATUS_LABEL.get(status, status)
        with open(self.path, "a", encoding="utf-8") as f:   # append + 立即關閉 = 馬上落地
            f.write(f"{ts}  {song['song']} - {song['artist']}  [{label}]\n")

    def _resolve_and_add(self, song):
        """回 (status, uri, exists_on)。可能丟例外（由 handle 接住，確保歌名已先寫檔）。"""
        if song.get("spotify_uri"):
            uri, status, exists_on = song["spotify_uri"], "spotify_direct", []
        else:
            res = self.wf.search(song["song"], song["artist"])
            uri, status, exists_on = res["uri"], res["status"], res["exists_on"]
        if uri and uri not in self.existing:
            spotify_client.add_uris(self.sp, self.playlist_id, [uri])
            self.existing.add(uri)
        return status, uri, exists_on

    def handle(self, song):
        key = (normalize(song["song"]), normalize(song["artist"]))
        if key in self.seen:
            return
        self.seen.add(key)

        status, uri, exists_on = "recognized", None, []
        if self.sp is not None:
            try:
                status, uri, exists_on = self._resolve_and_add(song)
            except Exception as e:
                status = "spotify_error"
                print(f"    [警告] Spotify 步驟失敗（歌名已記）: {e}", flush=True)

        self._append(song, status)   # 一定會寫到歌名
        self.results.append({"song": song["song"], "artist": song["artist"],
                             "pos": song.get("pos"), "status": status,
                             "uri": uri, "exists_on": exists_on})

    @property
    def added(self):
        return sum(1 for r in self.results if r["uri"])


def _reset_engines(rec):
    """冷卻後讓所有引擎重新啟用，給被限流的引擎再一次機會。"""
    for eng in rec.engines:
        eng._disabled = False
        eng._consecutive_fail = 0


async def _process_video(i, total, v, rec, sink, tmpdir, refresh):
    """處理單支：回 'ok' / 'skip' / 'blocked'。已找到的歌即時進 sink。"""
    vid, vtitle = v["id"], v["title"]

    cached = None if refresh else recognizer.load_checkpoint(vid)
    if cached is not None:
        print(f"[{i}/{total}] {vtitle}  [cache] {len(cached)} 首", flush=True)
        for s in cached:
            sink.handle(s)
        return "ok"

    print(f"[{i}/{total}] {vtitle}  下載中...", flush=True)
    try:
        path = audio_source.download_audio(vid, tmpdir)
    except Exception as e:
        print(f"  [警告] 下載失敗，跳過: {e}", flush=True)
        return "skip"

    try:
        duration = slicer.probe_duration(path)
        prog = Progress(duration)

        def slice_fn(start, dur, _p=path):
            return slicer.cut(_p, start, dur)

        def on_found(pos, song):
            prog.found(pos, song)
            sink.handle(song)     # 找到當下即時：寫檔 + 進 Spotify

        found = await scan_track(duration, slice_fn, rec.recognize,
                                 on_window=prog.update, on_found=on_found)
        prog.done()
        recognizer.save_checkpoint(vid, found)
        print(f"  影片 [{vtitle}] 辨識出 {len(found)} 首", flush=True)
        return "ok"
    except RecognitionBlocked as e:
        print(f"\n  [限流] {vtitle} 連續失敗，先跳過這支（已找到的歌都已記錄/加入）", flush=True)
        if e.reason:
            print(f"         真實錯誤: {e.reason}", flush=True)
        return "blocked"
    except slicer.FfmpegError as e:
        print(f"  [警告] 切片失敗，跳過: {e}", flush=True)
        return "skip"
    finally:
        _safe_unlink(path)


def _safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _print_final_list(results):
    print(f"\n{'=' * 50}")
    print(f"全部辨識到的歌（共 {len(results)} 首）")
    print(f"{'=' * 50}")
    for i, r in enumerate(results, 1):
        tag = _STATUS_LABEL.get(r["status"], r["status"])
        if r["status"] == "not_found" and r["exists_on"]:
            tag += f"，其他平台有: {', '.join(r['exists_on'])}"
        print(f"{i:>3}. {r['song']} - {r['artist']}  [{tag}]")


def _parse_args():
    flags = {"--no-spotify", "--refresh", "--save-list"}
    args = [a for a in sys.argv[1:] if a not in flags]
    no_spotify = "--no-spotify" in sys.argv
    refresh = "--refresh" in sys.argv

    urls = [a for a in args if a.startswith("http")]
    names = [a for a in args if not a.startswith("http")]
    name_arg = names[0] if names else None

    if not urls:
        print("貼上 YouTube 網址（可多個，一行一個，空白 Enter 結束）：")
        while True:
            u = input(">>> ").strip()
            if not u:
                break
            urls.append(u)
        if urls:
            name_arg = input("Spotify 清單名稱（Enter = 用第一支標題）: ").strip() or None
    return urls, name_arg, no_spotify, refresh


async def main():
    urls, name_arg, no_spotify, refresh = _parse_args()
    if not urls:
        print("沒有輸入網址，結束。")
        return

    print(f"=== 解析 {len(urls)} 個來源 ==={' (--refresh)' if refresh else ''}")
    videos, first_title = [], None
    for url in urls:
        title, vids = audio_source.parse_playlist(url)
        first_title = first_title or title
        videos.extend(vids)
    if not videos:
        print("沒有解析到任何影片，結束。")
        return
    playlist_name = name_arg or first_title or "Shazam Import"
    print(f"共 {len(videos)} 部影片 -> 清單「{playlist_name}」\n")

    # tracklist 檔一定建立、增量寫（最重要的安全網）
    today = datetime.date.today().strftime("%Y-%m-%d")
    tracklist_path = f"./{today}_{_safe_filename(playlist_name)}_tracklist.txt"
    with open(tracklist_path, "w", encoding="utf-8") as f:
        f.write(f"# {playlist_name}  ({today})\n\n")
    print(f"tracklist（即時寫入）: {tracklist_path}", flush=True)

    # Spotify 一次設定好（建清單、抓已存在 URI）
    sp = wf = playlist_id = None
    existing = set()
    if not no_spotify:
        sp = spotify_client.get_client()
        user_id = sp.me()["id"]
        print(f"Spotify 帳號: {user_id}", flush=True)
        playlist = spotify_client.find_or_create_playlist(sp, user_id, playlist_name)
        playlist_id = playlist["id"]
        existing = spotify_client.existing_track_uris(sp, playlist_id)
        wf = Waterfall(sp)
        print(f"清單連結: {playlist['external_urls']['spotify']}\n", flush=True)

    sink = TrackSink(tracklist_path, sp, wf, playlist_id, existing)
    rec = _build_recognizer()      # 只建一次（省 API：被停用的引擎不再被呼叫）

    tmpdir = tempfile.mkdtemp(prefix="shazam_")
    consecutive_blocks = 0
    try:
        for i, v in enumerate(videos, 1):
            outcome = await _process_video(i, len(videos), v, rec, sink, tmpdir, refresh)
            if outcome == "blocked":
                consecutive_blocks += 1
                if consecutive_blocks >= MAX_CONSECUTIVE_BLOCKS:
                    print(f"\n連續 {consecutive_blocks} 支被限流，先停。已找到的都已存檔/加入清單；"
                          f"稍後重跑會跳過已完成的影片。", flush=True)
                    break
                print(f"  冷卻 {COOLDOWN_SEC} 秒後繼續下一支...", flush=True)
                await asyncio.sleep(COOLDOWN_SEC)
                _reset_engines(rec)
            else:
                consecutive_blocks = 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    _print_final_list(sink.results)
    print(f"\n=== 完成 ===")
    print(f"辨識到 {len(sink.results)} 首；加入 Spotify {sink.added} 首"
          f"{'（--no-spotify，只記名）' if no_spotify else ''}")
    print(f"tracklist: {tracklist_path}")


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)   # 強制結束，清掉 yt-dlp 的 thread pool
