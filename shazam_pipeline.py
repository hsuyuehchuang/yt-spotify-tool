"""YouTube 播放清單 -> Shazam 辨識 -> 單一 Spotify 清單。

針對 DJ mix / 合輯這類「未標記長影片」：下載音軌 -> 滑動時間窗用 shazamio 辨識 ->
瀑布流（Spotify -> YT Music -> SoundCloud）確認 -> 命中全部匯進單一 Spotify 清單。

用法（互動模式為主，免引號免跳脫）：
    python3 shazam_pipeline.py                 直接跑，跳提示貼網址（網址含 & 也不用引號）
    python3 shazam_pipeline.py --no-spotify    互動 + 只辨識不寫 Spotify
    python3 shazam_pipeline.py URL [清單名] [--no-spotify] [--refresh]   給參數（自動化用）

旗標：
    --no-spotify   只跑辨識並印結果，完全不碰 Spotify（dry-run，不需登入）
    --refresh      忽略 .shazam-cache 既有 checkpoint，重新辨識

容錯：每支影片辨識完成即寫 checkpoint；疑似被 Shazam 限流時優雅停止，
已完成的影片不會白跑，重跑可從 checkpoint 接續。
"""

import asyncio
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
from waterfall import Waterfall


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


class Progress:
    """單支影片的掃描進度條（純文字，用 \\r 即時更新）。

    顯示：百分比、已掃秒數/總長、命中數、已做視窗數、估計剩餘視窗、ETA。
    """

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
        # 先清掉進度條那行，印出命中（下一次 update 會重畫進度條）
        print(f"\r{' ' * 92}\r    {_mmss(pos)} 命中: {song['song']} - {song['artist']}", flush=True)

    def done(self):
        print()  # 進度條換行收尾


async def _scan_videos(videos, tmpdir, refresh):
    """逐片辨識，回傳 (all_found, blocked)。"""
    all_found = []
    total = len(videos)
    for i, v in enumerate(videos, 1):
        vid, vtitle = v["id"], v["title"]

        cached = None if refresh else recognizer.load_checkpoint(vid)
        if cached is not None:
            print(f"[{i}/{total}] {vtitle}  [cache] {len(cached)} 首", flush=True)
            all_found.extend(cached)
            continue

        print(f"[{i}/{total}] {vtitle}  下載中...", flush=True)
        try:
            path = audio_source.download_audio(vid, tmpdir)
        except Exception as e:
            print(f"  [警告] 下載失敗，跳過: {e}", flush=True)
            continue

        try:
            duration = slicer.probe_duration(path)
            rec = _build_recognizer()
            prog = Progress(duration)

            def slice_fn(start, dur, _p=path):
                return slicer.cut(_p, start, dur)

            found = await scan_track(
                duration, slice_fn, rec.recognize,
                on_window=prog.update, on_found=prog.found,
            )
            prog.done()
        except RecognitionBlocked as e:
            print(f"  [中止] 辨識連續失敗，停止本次（本片暫得 {len(e.partial)} 首，不快取）", flush=True)
            if e.reason:
                print(f"         真實錯誤: {e.reason}（疑似限流或網路問題，稍等幾分鐘再重跑）", flush=True)
            all_found.extend(e.partial)
            _safe_unlink(path)
            return all_found, True
        except slicer.FfmpegError as e:
            print(f"  [警告] 切片失敗，跳過: {e}", flush=True)
            _safe_unlink(path)
            continue
        finally:
            _safe_unlink(path)

        recognizer.save_checkpoint(vid, found)
        all_found.extend(found)
        print(f"  影片 [{vtitle}] 辨識出 {len(found)} 首歌曲", flush=True)

    return all_found, False


def _safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _dedup(all_found):
    """跨影片去重：優先用 track_id，沒有才用 song+artist 文字。"""
    seen = set()
    songs = []
    for f in all_found:
        key = f.get("track_id") or (f["song"].lower(), f["artist"].lower())
        if key in seen:
            continue
        seen.add(key)
        songs.append(f)
    return songs


_STATUS_LABEL = {
    "spotify": "已加入",
    "spotify_via_ytmusic": "已加入(經 YT Music)",
    "spotify_via_soundcloud": "已加入(經 SoundCloud)",
    "not_found": "Spotify 無",
}


def _waterfall_resolve(sp, songs):
    """逐首跑瀑布流，回傳每首的完整結果 dict 清單（含 status/uri/exists_on）。"""
    wf = Waterfall(sp)
    results = []
    print("\n瀑布流搜尋中（Spotify -> YT Music -> SoundCloud）...", flush=True)
    for s in songs:
        res = wf.search(s["song"], s["artist"])
        results.append(res)
        if res["uri"]:
            print(f"  [{_STATUS_LABEL[res['status']]}] {s['song']} - {s['artist']}", flush=True)
        else:
            extra = f"（其他平台有: {', '.join(res['exists_on'])}）" if res["exists_on"] else ""
            print(f"  [找不到] {s['song']} - {s['artist']} {extra}", flush=True)
    return results


def _print_final_list(results):
    """結尾完整印出所有辨識到的歌 + 各自的歸戶狀態。"""
    print(f"\n{'=' * 50}")
    print(f"全部辨識到的歌（共 {len(results)} 首）")
    print(f"{'=' * 50}")
    for i, r in enumerate(results, 1):
        tag = _STATUS_LABEL[r["status"]]
        if r["status"] == "not_found" and r["exists_on"]:
            tag += f"，其他平台有: {', '.join(r['exists_on'])}"
        print(f"{i:>3}. {r['song']} - {r['artist']}  [{tag}]")


def _write_playlist(sp, playlist_name, uris):
    user_id = sp.me()["id"]
    print(f"\n登入成功，Spotify 帳號: {user_id}")
    playlist = spotify_client.find_or_create_playlist(sp, user_id, playlist_name)
    playlist_id = playlist["id"]
    existing = spotify_client.existing_track_uris(sp, playlist_id)
    new = [u for u in uris if u not in existing]
    print(f"  播放清單已有 {len(existing)} 首；本次新增 {len(new)} 首（{len(uris) - len(new)} 首已存在）")
    if new:
        spotify_client.add_uris(sp, playlist_id, new)
    print(f"連結: {playlist['external_urls']['spotify']}")


async def main():
    flags = {"--no-spotify", "--refresh"}
    args = [a for a in sys.argv[1:] if a not in flags]
    no_spotify = "--no-spotify" in sys.argv
    refresh = "--refresh" in sys.argv

    if args:
        url = args[0]
        name_arg = args[1] if len(args) > 1 else None
    else:
        # 互動模式：直接貼網址，免引號免跳脫（網址裡的 & 與空白都 OK）
        url = input("貼上 YouTube 播放清單或影片網址: ").strip()
        if not url:
            print("沒有輸入網址，結束。")
            return
        name_arg = input("Spotify 播放清單名稱（直接 Enter = 用影片標題）: ").strip() or None

    print(f"=== 解析 YouTube 來源 ==={' (--refresh)' if refresh else ''}")
    playlist_title, videos = audio_source.parse_playlist(url)
    if not videos:
        print("沒有解析到任何影片，結束。")
        return
    print(f"來源: {playlist_title}（{len(videos)} 部影片）\n")

    tmpdir = tempfile.mkdtemp(prefix="shazam_")
    try:
        all_found, blocked = await _scan_videos(videos, tmpdir, refresh)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    songs = _dedup(all_found)
    print(f"\n{'=' * 50}")
    print(f"辨識結果（去重後共 {len(songs)} 首）{'  [因連續失敗提早停止]' if blocked else ''}")
    print(f"{'=' * 50}")
    for i, s in enumerate(songs, 1):
        print(f"{i}. {s['song']} - {s['artist']}")

    if not songs:
        print("\n沒有辨識出任何歌曲，結束。")
        return

    if no_spotify:
        print(f"\n[dry-run] --no-spotify：只辨識，完全不碰 Spotify（共 {len(songs)} 首）。")
        return

    # 瀑布流需要 Spotify 搜尋（唯讀），故需登入（token 存 .cache）
    sp = spotify_client.get_client()
    results = _waterfall_resolve(sp, songs)

    uris = []
    for r in results:
        if r["uri"] and r["uri"] not in uris:
            uris.append(r["uri"])
    not_found = [r for r in results if not r["uri"]]

    playlist_name = name_arg or playlist_title
    print(f"\n{'=' * 50}")
    print(f"寫入 Spotify 播放清單: {playlist_name}")
    print(f"{'=' * 50}")
    _write_playlist(sp, playlist_name, uris)

    # 結尾：完整列出所有辨識到的歌 + 狀態
    _print_final_list(results)

    print(f"\n=== 完成 ===")
    print(f"加入清單: {len(uris)} 首 / Spotify 找不到: {len(not_found)} 首")


if __name__ == "__main__":
    asyncio.run(main())
    sys.stdout.flush()   # os._exit 不會 flush，先手動沖掉緩衝（被導向管線時尤其重要）
    sys.stderr.flush()
    os._exit(0)  # 強制結束，清掉 yt-dlp 的 thread pool（沿用 yt-to-mp3.py 做法）
