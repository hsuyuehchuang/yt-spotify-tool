import sys
import os
import time
import random
import re
import urllib.request
import urllib.parse
import json
import yt_dlp
from spotify_client import build_spotify_playlist


def _extract_music_from_ytInitialData(video_id: str) -> list[dict]:
    """
    從 YouTube 頁面的 ytInitialData 解析「音樂」區塊，
    返回 [{'song': ..., 'artist': ..., 'album': ...}, ...] 或空 list。
    YouTube 目前用 horizontalCardListRenderer + videoAttributeViewModel 呈現音樂卡片。
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
                if title:
                    songs.append({
                        "song": title,
                        "artist": artist or "Unknown",
                        "album": album,
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
    """
    watch?v=...&list=... 這種 URL yt-dlp 只抓單支影片。
    如果同時有 list= 參數，自動轉成 playlist?list=... 讓它抓整個 playlist。
    """
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    if "list" in params and "v" in params:
        list_id = params["list"][0]
        return f"https://www.youtube.com/playlist?list={list_id}"
    return url


CACHE_DIR = ".yt-music-cache"


def _load_video_cache(video_id: str) -> list[dict] | None:
    path = os.path.join(CACHE_DIR, f"{video_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_video_cache(video_id: str, songs: list[dict]) -> None:
    if not songs:
        return  # 沒找到歌的不存，避免被封鎖時誤抓
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{video_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)


def fetch_songs_from_youtube(url, refresh: bool = False) -> tuple[str, list[dict], list[tuple]]:
    url = _normalize_url(url)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "ignoreerrors": True,
    }

    all_songs = []
    video_results = []

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(url, download=False)

    yt_playlist_title = info_dict.get("title") or info_dict.get("id") or "YT Music Import"

    videos = info_dict.get("entries") or [info_dict]
    total = len(videos)

    for i, video in enumerate(videos, 1):
        if not video:
            continue

        video_id = video.get("id", "")
        vid_title = video.get("title", video_id)

        # 先讀 cache（除非 --refresh）
        cached = None if refresh else _load_video_cache(video_id)
        if cached is not None:
            print(f"[{i}/{total}] {vid_title}  [cache]", flush=True)
            all_songs.extend(cached)
            video_results.append((vid_title, cached))
            continue

        print(f"[{i}/{total}] {vid_title}", flush=True)

        try:
            found = _extract_music_from_ytInitialData(video_id)
        except Exception as e:
            print(f"  [警告] 解析失敗: {e}", flush=True)
            found = []

        _save_video_cache(video_id, found)
        all_songs.extend(found)
        video_results.append((vid_title, found))

        time.sleep(random.uniform(1.5, 3.0))

    return yt_playlist_title, all_songs, video_results


def main():
    flags = {"--refresh", "--no-spotify"}
    args = [a for a in sys.argv[1:] if a not in flags]
    refresh = "--refresh" in sys.argv
    no_spotify = "--no-spotify" in sys.argv

    yt_url = args[0] if len(args) > 0 else "https://www.youtube.com/watch?v=BNduoxSKw7Q&list=PLSdbWKBxNJL_3Sc4ffon7dMsO4G9vlKLI"
    playlist_name_arg = args[1] if len(args) > 1 else None

    print(f"=== 爬取 YouTube 歌曲 ==={' (--refresh)' if refresh else ''}")
    try:
        yt_playlist_title, all_songs, video_results = fetch_songs_from_youtube(yt_url, refresh=refresh)
    except Exception as e:
        print(f"執行過程中發生錯誤: {e}")
        return

    # --- 歌曲列表 ---
    print(f"\n{'=' * 50}")
    print(f"歌曲列表（共 {len(all_songs)} 首）")
    print(f"{'=' * 50}")
    for i, s in enumerate(all_songs, 1):
        print(f"{i}. {s['song']} - {s['artist']}")

    # --- 影片對應 ---
    print(f"\n{'=' * 50}")
    print("各影片找到的歌曲")
    print(f"{'=' * 50}")
    for title, songs in video_results:
        if songs:
            print(f"{title}")
            for s in songs:
                print(f"  - {s['song']} - {s['artist']}")

    if not all_songs:
        print("\n沒有找到任何歌曲，結束。")
        return

    if no_spotify:
        return

    playlist_name = playlist_name_arg or yt_playlist_title
    print(f"\n{'=' * 50}")
    print(f"Spotify 播放清單: {playlist_name}")
    print(f"{'=' * 50}")
    build_spotify_playlist(all_songs, playlist_name)


if __name__ == "__main__":
    main()
