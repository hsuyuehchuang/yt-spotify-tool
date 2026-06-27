"""統一互動入口：貼一個網址，自動判斷該走哪支 script。

不用記也不用切換 script。Spotify / Bandcamp 直接自動分流；YouTube 因為同一個
網址可能有多種意圖（下載 / 辨識 / 爬卡片），會問一句要做什麼。

底層是用 subprocess 呼叫既有的 script（檔名含 '-' 無法 import），所以現有程式
完全不動、零風險。

用法:
    python3 music.py                      # 互動模式，貼一個跑一個
    python3 music.py <url> [<url> ...]    # 直接給網址
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def detect_platform(url: str) -> str | None:
    """純判斷網址屬於哪個平台（spotify / bandcamp / youtube / None）。"""
    u = url.lower()
    if "spotify.com" in u:
        return "spotify"
    if "bandcamp.com" in u:
        return "bandcamp"
    if "soundcloud.com" in u:
        return "soundcloud"
    if "youtube.com" in u or "youtu.be" in u:
        return "youtube"
    return None


def _script(name: str) -> str:
    return os.path.join(HERE, name)


# YouTube 意圖 -> 對應的 script 與參數前綴
_YT_ACTIONS = {
    "1": ("download MP3（影片/playlist 直接下載）", "yt-to-mp3.py", ["download"]),
    "2": ("Shazam 聲紋辨識 → Spotify 清單（DJ mix / 未標記長影片）", "shazam_pipeline.py", []),
    "3": ("爬 YT 音樂卡片 → Spotify 清單", "yt-to-spotify.py", []),
    "4": ("爬 YT 音樂卡片 → 下載 MP3", "yt-to-mp3.py", ["scrape"]),
}
_YT_WANTS_NAME = {"2", "3"}   # 這兩個可帶 Spotify 清單名稱


def build_command(url: str, yt_choice: str | None = None, playlist_name: str | None = None):
    """純函式：依平台 / YouTube 選項組出要執行的指令 list；無法判斷回 None。"""
    plat = detect_platform(url)
    if plat == "spotify":
        return ["python3", _script("spotify-to-mp3.py"), url]
    if plat == "bandcamp":
        return ["python3", _script("bandcamp-to-mp3.py"), url]
    if plat == "soundcloud":
        return ["python3", _script("soundcloud-to-mp3.py"), url]
    if plat == "youtube":
        action = _YT_ACTIONS.get(yt_choice or "1")
        if not action:
            return None
        _, script, prefix = action
        cmd = ["python3", _script(script), *prefix, url]
        if yt_choice in _YT_WANTS_NAME and playlist_name:
            cmd.append(playlist_name)
        return cmd
    return None


def _ask_youtube(url: str):
    print("\nYouTube 網址，你要做什麼？")
    for key in sorted(_YT_ACTIONS):
        print(f"  {key}) {_YT_ACTIONS[key][0]}")
    choice = input("選 1-4（Enter = 1）: ").strip() or "1"
    if choice not in _YT_ACTIONS:
        print("無效選擇，跳過。")
        return None
    name = None
    if choice in _YT_WANTS_NAME:
        name = input("Spotify 清單名稱（Enter = 用影片標題）: ").strip() or None
    return build_command(url, choice, name)


def run_one(url: str):
    plat = detect_platform(url)
    if plat is None:
        print(f"[跳過] 認不出這是哪個平台（支援 Spotify / YouTube / Bandcamp / SoundCloud）: {url}")
        return
    cmd = _ask_youtube(url) if plat == "youtube" else build_command(url)
    if not cmd:
        return
    print(f"\n=== 執行: {' '.join(os.path.basename(c) for c in cmd[1:])} ===", flush=True)
    subprocess.run(cmd)


def main():
    urls = sys.argv[1:]
    if urls:
        for url in urls:
            run_one(url)
        return

    print("統一入口：貼上 Spotify / YouTube / Bandcamp / SoundCloud 網址（空白 Enter 或 Ctrl+C 結束）")
    try:
        while True:
            url = input("\n>>> ").strip()
            if not url:
                break
            run_one(url)
    except (KeyboardInterrupt, EOFError):
        print()


if __name__ == "__main__":
    main()
