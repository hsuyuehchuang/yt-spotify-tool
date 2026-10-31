"""
spotify-to-mp3.py
給一個 Spotify 播放清單 URL，自動搜尋 YouTube 並下載為 MP3，並寫入完整 ID3 Tags。

用法:
    python spotify-to-mp3.py                                   # 互動模式，貼一個下一個
    python spotify-to-mp3.py <spotify_playlist_url> [輸出資料夾]

範例:
    python spotify-to-mp3.py "https://open.spotify.com/playlist/xxx"
    python spotify-to-mp3.py "https://open.spotify.com/playlist/xxx" ./music
"""

import sys
import os
import glob
import time
import random
import requests
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, TIT2, TPE1, TALB, APIC, ID3NoHeaderError
from utils import word_overlap, title_matches, artist_matches, print_summary, make_output_dir
from cover_match import match_by_cover

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
            # Always use this song's album/single artwork, never playlist.images.
            images = t["album"].get("images") or []
            tracks.append({
                "name": t["name"],
                "artist": ", ".join(a["name"] for a in t["artists"]),
                "album": t["album"]["name"],
                "duration_ms": t["duration_ms"],
                "cover_url": images[0]["url"] if images else None,
            })
        if not resp.get("next"):
            break
        offset += 100

    return playlist_name, tracks


def search_youtube(track_name: str, artist: str, duration_ms: int,
                   cover_url: str | None = None) -> str | None:
    """
    搜尋策略：
    1. 優先找 YouTube Music 官方 Topic 頻道（最乾淨）
    2. 比對時長、歌名、artist，全部通過才接受
    3. Topic 找不到再 fallback 一般搜尋（同樣嚴格比對）
    4. 加引號保留完整歌名與歌手再搜尋，避免常見單字被搜尋引擎拆散
    5. 仍找不到時，封面 >=90 分 + 接近歌名 + 歌手 + 時長做保守補救
    """
    duration_sec = duration_ms / 1000
    artists = [a.strip() for a in artist.split(",")]

    ydl_opts = {"quiet": True, "no_warnings": True, "extract_flat": True,
                "socket_timeout": 15, "retries": 1}

    def try_search(query, max_results=5):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
            entries = list((result or {}).get("entries") or [])
            print(f"    [搜尋] {query} → {sum(bool(e) for e in entries)} 個候選")
            return entries
        except yt_dlp.utils.DownloadError as exc:
            print(f"    [搜尋失敗] {exc}")
            return []

    def matches(entry):
        vid_title = entry.get("title") or ""
        vid_channel = entry.get("channel") or entry.get("uploader") or ""
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
    candidates = list(entries)
    best = pick_best(entries)
    if best:
        return best

    # 第二輪：一般搜尋，同樣要過嚴格的 title + artist + 時長比對
    entries = try_search(f"{artist} {track_name}", max_results=8)
    candidates.extend(entries)
    best = pick_best(entries)
    if best:
        return best

    # In The Sun / Low Heat otherwise returns weather/cooking videos. Quoting
    # each phrase retrieves the correct Topic upload without loosening matching.
    quoted_title = track_name.replace('"', ' ').strip()
    quoted_artist = artists[0].replace('"', ' ').strip()
    entries = try_search(f'"{quoted_title}" "{quoted_artist}"', max_results=10)
    candidates.extend(entries)
    best = pick_best(entries)
    if best:
        print("    [精確搜尋配對成功] 歌名、歌手、時長均符合")
        return best
    if not cover_url:
        print("    [封面比對略過] 這首歌曲的專輯／單曲封面不存在（不使用播放清單封面）")
        return None

    print("    [文字配對未命中，嘗試封面比對]")
    # A title-first query can surface tracks hidden by the Topic/artist query.
    candidates.extend(try_search(f"{track_name} {artists[0]}", max_results=12))
    return match_by_cover(candidates, track_name, artists, duration_sec,
                          cover_url, artist_matches, DURATION_TOLERANCE_SEC)


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


def download_playlist(playlist_url: str, output_base: str | None = None):
    print("=== 讀取 Spotify 播放清單 ===")
    playlist_name, tracks = get_spotify_tracks(playlist_url)

    output_dir = output_base or make_output_dir()
    os.makedirs(output_dir, exist_ok=True)

    print(f"播放清單: {playlist_name}")
    print(f"下載資料夾: {output_dir}")
    print(f"共 {len(tracks)} 首歌\n")

    success, skipped, failed = [], [], []

    for i, track in enumerate(tracks, 1):
        label = f"{track['name']} - {track['artist']}"
        print(f"[{i}/{len(tracks)}] {label}")

        # 安全檔名（去掉不合法字元）
        safe_name = "".join(c if c not in r'\/:*?"<>|' else "_" for c in label)
        # 用 safe_name 前綴在資料夾裡找看看有沒有已存在的 mp3
        existing = glob.glob(os.path.join(output_dir, safe_name + "*.mp3"))
        if existing:
            print("    [已存在，跳過]")
            skipped.append(label)
            continue

        video_id = search_youtube(track["name"], track["artist"], track["duration_ms"],
                                  cover_url=track.get("cover_url"))
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
        success.append(label)

        # 隨機延遲，避免觸發限流
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    print_summary(success, skipped, failed)


def main():
    args = sys.argv[1:]
    if args:
        # 直接給 URL（可選第二個參數指定輸出資料夾）
        download_playlist(args[0], args[1] if len(args) > 1 else None)
    else:
        # 沒給參數 → 互動模式，貼一個下一個（沿用 bandcamp-to-mp3.py 體驗）
        print("互動模式：貼上 Spotify 播放清單 URL 後 Enter（空白 Enter 或 Ctrl+C 結束）\n")
        try:
            while True:
                url = input(">>> ").strip()
                if not url:
                    break
                download_playlist(url)
                print()
        except (KeyboardInterrupt, EOFError):
            print()

    os._exit(0)


if __name__ == "__main__":
    main()
