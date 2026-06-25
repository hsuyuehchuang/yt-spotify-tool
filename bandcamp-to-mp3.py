"""
bandcamp-to-mp3.py

快速下載 MP3，支援 Bandcamp 與 YouTube 單曲。
- Bandcamp：單曲 / 專輯 / 藝術家頁面
- YouTube：只支援單一影片，不支援 playlist（要下載 playlist 請用 yt-to-mp3.py）

用法:
    python bandcamp-to-mp3.py                  # 互動模式
    python bandcamp-to-mp3.py <url> [<url>...] # 直接給網址

範例:
    python bandcamp-to-mp3.py "https://artist.bandcamp.com/album/album-name"
    python bandcamp-to-mp3.py "https://www.youtube.com/watch?v=xxx"
"""

import sys
import os
import glob
import time
import random
import yt_dlp

from utils import make_output_dir

DELAY_MIN = 1.5
DELAY_MAX = 4.0


def safe_filename(text: str) -> str:
    return "".join(c if c not in r'\/:*?"<>|' else "_" for c in text)


def _validate_url(url: str) -> str | None:
    """檢查 URL 是否支援，回傳錯誤訊息（None 表示通過）"""
    if "bandcamp.com" in url:
        return None
    if "youtube.com" in url or "youtu.be" in url:
        # YouTube 不接受 playlist
        if "list=" in url or "/playlist" in url:
            return "不支援 YouTube playlist，請用 yt-to-mp3.py"
        return None
    return "只支援 Bandcamp 或 YouTube 單一影片 URL"


def download_one(url: str, output_dir: str):
    err = _validate_url(url)
    if err:
        print(f"  [跳過] {err}: {url}")
        return

    info_opts = {"quiet": True, "no_warnings": True, "ignoreerrors": True, "noplaylist": True}
    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    # 父層的 artist（album / discography 共用）
    parent_artist = info.get("uploader") or info.get("artist") or info.get("channel") or ""

    videos = info.get("entries") or [info]
    total = len(videos)
    success, skipped, failed = [], [], []

    def progress_hook(d):
        if d["status"] == "downloading":
            pct = d.get("_percent_str", "").strip()
            t = d.get("info_dict", {}).get("title", "")
            print(f"\r  {t}  {pct}      ", end="", flush=True)
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

        track_title = video.get("track") or video.get("title") or video.get("id") or "track"
        track_artist = video.get("artist") or video.get("uploader") or parent_artist or "Unknown"
        track_url = video.get("webpage_url") or video.get("url") or url

        label = f"{track_title} - {track_artist}"
        fname = safe_filename(label)
        mp3_path = os.path.join(output_dir, fname + ".mp3")

        print(f"[{i}/{total}] {label}")

        if glob.glob(mp3_path):
            print(f"  [已存在，跳過]")
            skipped.append(label)
            continue

        ydl_opts["outtmpl"] = os.path.join(output_dir, fname + ".%(ext)s")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([track_url])
            success.append(label)
            print(f"  [完成] {label}")
        except Exception as e:
            print(f"  [失敗] {e}")
            failed.append(label)

        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    print(f"  → 成功: {len(success)} | 已存在: {len(skipped)} | 失敗: {len(failed)}")
    if failed:
        for f in failed:
            print(f"    - 失敗: {f}")


def main():
    output_dir = make_output_dir()
    print(f"下載資料夾: {output_dir}")

    # 命令列直接給 URL → 下載完就結束
    if len(sys.argv) >= 2:
        for url in sys.argv[1:]:
            print(f"\n=== 下載: {url} ===")
            download_one(url, output_dir)
        os._exit(0)

    # 沒給參數 → 進互動模式，貼一個下載一個，Enter 空白或 Ctrl+C 結束
    print("互動模式：貼上 Bandcamp / YouTube 單曲 URL 後按 Enter（空白 Enter 或 Ctrl+C 結束）\n")
    try:
        while True:
            url = input(">>> ").strip()
            if not url:
                break
            print(f"=== 下載: {url} ===")
            download_one(url, output_dir)
            print()
    except (KeyboardInterrupt, EOFError):
        print()

    os._exit(0)


if __name__ == "__main__":
    main()
