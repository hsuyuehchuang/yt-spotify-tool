"""將 Instagram 影片／Reels 的音軌下載成 MP3，由 music.py 自動呼叫。"""

import os
import sys

import yt_dlp

from utils import make_output_dir


def download_one(url: str, output_dir: str) -> bool:
    options = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(
            output_dir, "%(title).100s [%(id)s].%(ext)s"
        ),
        "overwrites": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            },
            {"key": "FFmpegMetadata"},
        ],
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            result = ydl.download([url])
        if result:
            print("[失敗] Instagram 音訊下載未完成。")
            return False
    except yt_dlp.utils.DownloadError as exc:
        print(f"[失敗] {exc}")
        print("若 IG 要求登入或限制存取，這個網址可能無法直接下載。")
        return False
    print(f"[完成] MP3 儲存於 {output_dir}")
    return True


def main() -> int:
    if len(sys.argv) < 2:
        print('用法：python3 instagram-to-mp3.py "IG影片網址"')
        return 1
    output_dir = make_output_dir()
    print(f"下載資料夾: {output_dir}", flush=True)
    failed = False
    for url in sys.argv[1:]:
        if not download_one(url, output_dir):
            failed = True
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
