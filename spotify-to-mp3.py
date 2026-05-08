"""
download-from-spotlist.py
給一個 Spotify 播放清單 URL，自動搜尋 YouTube 並下載為 MP3，並寫入完整 ID3 Tags。

用法:
    python download-from-spotlist.py <spotify_playlist_url> [輸出資料夾]

範例:
    python download-from-spotlist.py "https://open.spotify.com/playlist/xxx" ./music
"""

import sys
import os
import glob
import time
import random
import datetime
import requests
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError
from utils import word_overlap, title_matches, artist_matches

SPOTIFY_CLIENT_ID = "9e989e6f2e034ca695d116607ec0cca6"
SPOTIFY_CLIENT_SECRET = "35a77882d91343a7a45fb507e1bd82fc"
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"

# 時長誤差容許範圍（秒）
DURATION_TOLERANCE_SEC = 5
# 每首歌下載後的隨機延遲（秒）
DELAY_MIN = 2.0
DELAY_MAX = 5.0


def get_spotify_tracks(playlist_url: str) -> tuple[str, list[dict]]:
    sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope="playlist-read-private playlist-read-collaborative",
    ))

    playlist_id = playlist_url.split("/playlist/")[-1].split("?")[0]
    playlist_name = sp.playlist(playlist_id, fields="name")["name"]
    tracks = []
    offset = 0

    while True:
        resp = sp.playlist_items(playlist_id, offset=offset, limit=100)
        for item in resp["items"]:
            t = item.get("item") or item.get("track")
            if not t:
                continue
            tracks.append({
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "album": t["album"]["name"],
                "duration_ms": t["duration_ms"],
                "cover_url": t["album"]["images"][0]["url"] if t["album"]["images"] else None,
            })
        if not resp.get("next"):
            break
        offset += 100

    return playlist_name, tracks


def search_youtube(track_name: str, artist: str, duration_ms: int) -> str | None:
    """
    搜尋策略：
    1. 優先找 YouTube Music 官方 Topic 頻道（最乾淨）
    2. 比對時長、歌名、artist，全部通過才接受
    3. Topic 找不到再 fallback 一般搜尋（同樣嚴格比對）
    """
    duration_sec = duration_ms / 1000
    artists = [a.strip() for a in artist.split(",")]

    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True}

    def try_search(query, max_results=5):
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
        return result.get("entries", [])

    def matches(entry):
        vid_title = entry.get("title") or ""
        vid_channel = entry.get("channel") or ""
        vid_combined = vid_title + " " + vid_channel

        vid_duration = entry.get("duration") or 0
        if abs(vid_duration - duration_sec) > DURATION_TOLERANCE_SEC:
            return False
        if not title_matches(track_name, vid_title):
            return False
        if not artist_matches(artists, vid_combined):
            return False
        return True

    def pick_best(entries):
        for entry in entries:
            if entry and matches(entry):
                return entry.get("url") or entry.get("id")
        return None

    # 第一輪：Topic 官方頻道（最乾淨）
    entries = try_search(f"{artist} {track_name} Topic")
    best = pick_best(entries)
    if best:
        return best

    # 第二輪：一般搜尋，同樣要過嚴格的 title + artist + 時長比對
    entries = try_search(f"{artist} {track_name}", max_results=8)
    return pick_best(entries)


def download_track(video_id: str, output_path: str) -> bool:
    url = f"https://www.youtube.com/watch?v={video_id}" if not video_id.startswith("http") else video_id
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": output_path,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "0",  # 最高品質 VBR
        }],
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return True
    except Exception as e:
        print(f"    [下載失敗] {e}")
        return False


def write_id3_tags(mp3_path: str, track: dict):
    try:
        audio = MP3(mp3_path, ID3=ID3)
    except ID3NoHeaderError:
        audio = MP3(mp3_path)
        audio.add_tags()

    audio.tags["TIT2"] = TIT2(encoding=3, text=track["name"])
    audio.tags["TPE1"] = TPE1(encoding=3, text=track["artist"])
    audio.tags["TALB"] = TALB(encoding=3, text=track["album"])

    if track.get("cover_url"):
        try:
            cover_data = requests.get(track["cover_url"], timeout=10).content
            audio.tags["APIC"] = APIC(
                encoding=3,
                mime="image/jpeg",
                type=3,  # front cover
                desc="Cover",
                data=cover_data,
            )
        except Exception:
            pass

    audio.save()


def main():
    playlist_url = sys.argv[1] if len(sys.argv) > 1 else None

    if not playlist_url:
        print("用法: python download-from-spotlist.py <spotify_playlist_url>")
        sys.exit(1)

    print("=== 讀取 Spotify 播放清單 ===")
    playlist_name, tracks = get_spotify_tracks(playlist_url)

    today = datetime.date.today().strftime("%Y-%m-%d")
    safe_playlist_name = "".join(c if c not in r'\/:*?"<>|' else "_" for c in playlist_name)
    output_dir = f"./{today}_{safe_playlist_name}"
    os.makedirs(output_dir, exist_ok=True)

    print(f"播放清單: {playlist_name}")
    print(f"下載資料夾: {output_dir}")
    print(f"共 {len(tracks)} 首歌\n")

    failed = []

    for i, track in enumerate(tracks, 1):
        label = f"{track['name']} - {track['artist']}"
        print(f"[{i}/{len(tracks)}] {label}")

        # 安全檔名（去掉不合法字元）
        safe_name = "".join(c if c not in r'\/:*?"<>|' else "_" for c in label)
        # 用 safe_name 前綴在資料夾裡找看看有沒有已存在的 mp3
        existing = glob.glob(os.path.join(output_dir, safe_name + "*.mp3"))
        if existing:
            print("    [已存在，跳過]")
            continue

        video_id = search_youtube(track["name"], track["artist"], track["duration_ms"])
        if not video_id:
            print("    [找不到 YouTube 對應歌曲]")
            failed.append(label)
            continue

        # yt-dlp outtmpl 不含副檔名，post-processor 自動加 .mp3
        tmp_path = os.path.join(output_dir, safe_name)
        ok = download_track(video_id, tmp_path)
        if not ok:
            failed.append(label)
            continue

        # 找 yt-dlp 實際產生的 mp3 檔（檔名可能與 safe_name 略有不同）
        produced = glob.glob(os.path.join(output_dir, safe_name + "*.mp3"))
        if not produced:
            print("    [下載完成但找不到 mp3 檔]")
            failed.append(label)
            continue
        actual_mp3 = produced[0]

        write_id3_tags(actual_mp3, track)
        print(f"    [完成] {actual_mp3}")

        # 隨機延遲，避免觸發限流
        delay = random.uniform(DELAY_MIN, DELAY_MAX)
        time.sleep(delay)

    print(f"\n=== 完成 ===")
    print(f"成功: {len(tracks) - len(failed)} 首")
    if failed:
        print(f"失敗: {len(failed)} 首")
        for f in failed:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
