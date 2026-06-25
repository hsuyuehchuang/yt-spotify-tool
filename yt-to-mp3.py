"""
yt-to-mp3.py

兩種模式：

  scrape  - 爬 YouTube playlist 每支影片的音樂區塊，直接用 YouTube 提供的連結下載
            找不到直接連結的才做名稱搜尋（標記為「相似」）
  download - 直接把 YouTube playlist 或單一影片下載成 MP3，不爬蟲

用法:
    python yt-to-mp3.py scrape   <yt_playlist_url>
    python yt-to-mp3.py download <yt_playlist_or_video_url>
"""

import sys
import os
import re
import glob
import time
import random
import urllib.request
import urllib.parse
import json
import yt_dlp
from utils import title_matches, artist_matches, print_summary, make_output_dir

DELAY_MIN = 1.5
DELAY_MAX = 4.0
DURATION_TOLERANCE_SEC = 5


# ──────────────────────────────────────────────
# 共用：下載單一 YouTube 影片為 MP3
# ──────────────────────────────────────────────

def download_as_mp3(video_url: str, output_path: str) -> bool:
    """output_path 不含副檔名，yt-dlp 會自動加 .mp3"""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "writethumbnail": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            },
            {
                "key": "EmbedThumbnail",
            },
            {
                "key": "FFmpegMetadata",  # 寫入 artist / album / title 等 metadata
            },
        ],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
        return True
    except Exception as e:
        print(f"    [下載失敗] {e}")
        return False


def safe_filename(text: str) -> str:
    return "".join(c if c not in r'\/:*?"<>|' else "_" for c in text)


# ──────────────────────────────────────────────
# Mode 1：Scrape
# ──────────────────────────────────────────────

def _extract_music_from_video(video_id: str) -> list[dict]:
    """
    從 YouTube 頁面 ytInitialData 抓音樂區塊。
    每首歌回傳 {'song', 'artist', 'album', 'yt_video_id'}
    yt_video_id 是 YouTube 直接提供的連結（可能為 None）
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    req = urllib.request.Request(url, headers={
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")

    m = re.search(r"var ytInitialData\s*=\s*(\{.+?\});\s*</script>", html, re.DOTALL)
    if not m:
        return []

    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []

    songs = []

    def walk(node):
        if isinstance(node, dict):
            if "videoAttributeViewModel" in node:
                vm = node["videoAttributeViewModel"]
                title = vm.get("title", "").strip()
                artist = vm.get("subtitle", "").strip()
                album = (vm.get("secondarySubtitle") or {}).get("content", "").strip() or None

                # 抓 YouTube 直接提供的歌曲連結
                yt_video_id = None
                try:
                    yt_video_id = (
                        node.get("onTap", {})
                        .get("innertubeCommand", {})
                        .get("watchEndpoint", {})
                        .get("videoId")
                    )
                except Exception:
                    pass

                if title:
                    songs.append({
                        "song": title,
                        "artist": artist or "Unknown",
                        "album": album,
                        "yt_video_id": yt_video_id,
                    })
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return songs


def _normalize_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    if "list" in params and "v" in params:
        return f"https://www.youtube.com/playlist?list={params['list'][0]}"
    return url


def _search_youtube_fallback(song: str, artist: str, duration_ms: int | None) -> str | None:
    """名稱搜尋備援，要過 title + artist 比對才接受"""
    artists = [a.strip() for a in artist.split(",")]
    duration_sec = duration_ms / 1000 if duration_ms else None

    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}

    def matches(entry):
        vid_title = entry.get("title") or ""
        vid_channel = entry.get("channel") or ""
        if not title_matches(song, vid_title):
            return False
        if not artist_matches(artists, vid_title + " " + vid_channel):
            return False
        if duration_sec is not None:
            vid_dur = entry.get("duration") or 0
            if abs(vid_dur - duration_sec) > DURATION_TOLERANCE_SEC:
                return False
        return True

    for query in [f"{artist} {song} Topic", f"{artist} {song}"]:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch8:{query}", download=False)
        for entry in result.get("entries", []):
            if entry and matches(entry):
                return entry.get("url") or entry.get("id")

    return None


def mode_scrape(yt_url: str):
    yt_url = _normalize_url(yt_url)

    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "ignoreerrors": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(yt_url, download=False)

    playlist_title = info.get("title") or info.get("id") or "scrape"
    videos = info.get("entries") or [info]
    total = len(videos)

    output_dir = make_output_dir()
    print(f"播放清單: {playlist_title}")
    print(f"下載資料夾: {output_dir}\n")

    direct = []    # 100% 直接連結
    similar = []   # 名稱搜尋找到
    not_found = [] # 完全找不到

    for i, video in enumerate(videos, 1):
        if not video:
            continue
        vid_id = video.get("id", "")
        vid_title = video.get("title", vid_id)
        print(f"[{i}/{total}] {vid_title}", flush=True)

        try:
            songs = _extract_music_from_video(vid_id)
        except Exception as e:
            print(f"  [警告] 爬取失敗: {e}")
            songs = []

        for s in songs:
            label = f"{s['song']} - {s['artist']}"
            fname = safe_filename(label)
            mp3_path = os.path.join(output_dir, fname + ".mp3")

            if glob.glob(mp3_path):
                print(f"  [已存在] {label}")
                continue

            tmp = os.path.join(output_dir, fname)

            if s["yt_video_id"]:
                # 有直接連結 → 直接下載
                url = f"https://www.youtube.com/watch?v={s['yt_video_id']}"
                ok = download_as_mp3(url, tmp)
                if ok:
                    print(f"  [直接] {label}")
                    direct.append(label)
                else:
                    not_found.append(label)
            else:
                # 沒有直接連結 → 名稱搜尋
                vid_url = _search_youtube_fallback(s["song"], s["artist"], None)
                if vid_url:
                    vid_url_full = f"https://www.youtube.com/watch?v={vid_url}" if not vid_url.startswith("http") else vid_url
                    ok = download_as_mp3(vid_url_full, tmp)
                    if ok:
                        print(f"  [相似] {label}")
                        similar.append(label)
                    else:
                        not_found.append(label)
                else:
                    print(f"  [找不到] {label}")
                    not_found.append(label)

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    print(f"\n{'=' * 50}")
    print(f"直接下載: {len(direct)} 首")
    print(f"相似下載: {len(similar)} 首")
    if not_found:
        print(f"找不到:   {len(not_found)} 首")
        for n in not_found:
            print(f"  - {n}")


# ──────────────────────────────────────────────
# Mode 2：Direct Download
# ──────────────────────────────────────────────

def mode_download(yt_url: str):
    yt_url = _normalize_url(yt_url)

    ydl_info_opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "ignoreerrors": True}
    with yt_dlp.YoutubeDL(ydl_info_opts) as ydl:
        info = ydl.extract_info(yt_url, download=False)

    output_dir = make_output_dir()
    print(f"下載資料夾: {output_dir}\n")

    videos = info.get("entries") or [info]
    total = len(videos)
    success, skipped, failed = [], [], []

    def progress_hook(d):
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            title = d.get("info_dict", {}).get("title", "")
            print(f"\r  {title}  {pct}      ", end="", flush=True)
        elif d["status"] == "finished":
            print()

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "writethumbnail": True,
        "progress_hooks": [progress_hook],
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"},
            {"key": "EmbedThumbnail"},
            {"key": "FFmpegMetadata"},  # 寫入 artist / album / title 等 metadata
        ],
    }

    for i, video in enumerate(videos, 1):
        if not video:
            continue
        vid_id = video.get("id") or video.get("url", "")
        vid_title = video.get("title", vid_id)
        fname = safe_filename(vid_title)
        mp3_path = os.path.join(output_dir, fname + ".mp3")

        print(f"[{i}/{total}] {vid_title}")

        if glob.glob(mp3_path):
            print(f"  [已存在，跳過]")
            skipped.append(vid_title)
            continue

        vid_url = f"https://www.youtube.com/watch?v={vid_id}" if not vid_id.startswith("http") else vid_id
        ydl_opts["outtmpl"] = os.path.join(output_dir, fname + ".%(ext)s")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([vid_url])
            success.append(vid_title)
            print(f"  [完成] {vid_title}")
        except Exception as e:
            print(f"  [失敗] {e}")
            failed.append(vid_title)

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    print_summary(success, skipped, failed)


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

def print_usage():
    print("用法:")
    print("  python yt-to-mp3.py                              # 互動模式（先選模式再貼網址）")
    print("  python yt-to-mp3.py scrape   <yt_playlist_url>")
    print("  python yt-to-mp3.py download <yt_playlist_or_video_url>")


def run_mode(mode: str, url: str) -> bool:
    """執行單一 URL，回傳 mode 是否有效。"""
    if mode == "scrape":
        mode_scrape(url)
        return True
    if mode == "download":
        mode_download(url)
        return True
    print(f"未知模式: {mode}")
    return False


def ask_mode() -> str | None:
    print("選擇模式：")
    print("  1) download — 直接把影片 / playlist 下載成 MP3")
    print("  2) scrape   — 爬影片音樂區塊再下載")
    choice = input("輸入 1 或 2（直接 Enter = 1 download）: ").strip()
    if choice in ("", "1", "download"):
        return "download"
    if choice in ("2", "scrape"):
        return "scrape"
    print("無效選擇。")
    return None


def interactive_loop(mode: str):
    """貼一個跑一個（沿用 bandcamp-to-mp3.py 互動體驗），空白 Enter / Ctrl+C 結束。"""
    print(f"\n互動模式（{mode}）：貼上 YouTube 網址後 Enter（空白 Enter 或 Ctrl+C 結束）\n")
    try:
        while True:
            url = input(">>> ").strip()
            if not url:
                break
            run_mode(mode, url)
            print()
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) >= 2:
        if not run_mode(args[0].lower(), args[1]):
            print_usage()
            sys.exit(1)
    elif len(args) == 1 and args[0].lower() in ("scrape", "download"):
        interactive_loop(args[0].lower())   # 給了模式沒給網址 → 該模式進互動
    else:
        mode = ask_mode()
        if mode:
            interactive_loop(mode)

    # yt-dlp 內部 thread pool 不會自動清除，強制退出避免掛住
    os._exit(0)
