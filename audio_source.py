"""yt-dlp 取得來源：解析 playlist、下載單支影片音軌。

沿用既有 script 的 yt-dlp 用法（extract_flat 解析、bestaudio 下載）。
下載不轉 mp3——只要拿到可被 ffmpeg 解碼的音軌即可（省時、無轉檔耗損），
切片交給 slicer.cut 處理。
"""

import json
import os
import re
import urllib.parse
import urllib.request

import yt_dlp

_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")


def fetch_youtube_cards(video_id: str) -> list[dict]:
    """免費抓 YouTube 影片自帶的「音樂」卡片（YouTube 已認出的歌），回 [{'song','artist','album'}]。

    走一個一般 HTTP 請求解析 ytInitialData，不需 API、無 rate limit。battle / DJ 影片常常
    有 8-10 首卡片（Shazam 在混音裡反而抓不到），等於免費多撈、不耗 Shazam/ACRCloud 額度。
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    req = urllib.request.Request(url, headers={"User-Agent": _UA,
                                               "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

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
                    songs.append({"song": title, "artist": artist or "Unknown", "album": album})
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(data)
    return songs


def normalize_url(url: str) -> str:
    """watch?v=...&list=... -> playlist?list=...，讓 yt-dlp 抓整個 playlist。"""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    if "list" in params and "v" in params:
        return f"https://www.youtube.com/playlist?list={params['list'][0]}"
    return url


def parse_playlist(url: str) -> tuple[str, list[dict]]:
    """回傳 (playlist 標題, [{'id','title'}, ...])。單支影片也回傳長度 1 的清單。"""
    url = normalize_url(url)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:  # ignoreerrors 下，影片/清單不可用會回 None
        return "Shazam Import", []

    title = info.get("title") or info.get("id") or "Shazam Import"
    entries = info.get("entries") or [info]
    videos = []
    for e in entries:
        if not e:
            continue
        vid = e.get("id")
        if vid:
            videos.append({"id": vid, "title": e.get("title", vid)})
    return title, videos


def download_audio(video_id: str, dest_dir: str) -> str:
    """下載單支影片的 bestaudio 到 dest_dir，回傳實際檔案路徑。"""
    os.makedirs(dest_dir, exist_ok=True)
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "format": "bestaudio/best",
        "outtmpl": os.path.join(dest_dir, "%(id)s.%(ext)s"),
    }
    url = f"https://www.youtube.com/watch?v={video_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return ydl.prepare_filename(info)
