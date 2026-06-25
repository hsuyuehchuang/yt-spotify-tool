"""yt-dlp 取得來源：解析 playlist、下載單支影片音軌。

沿用既有 script 的 yt-dlp 用法（extract_flat 解析、bestaudio 下載）。
下載不轉 mp3——只要拿到可被 ffmpeg 解碼的音軌即可（省時、無轉檔耗損），
切片交給 slicer.cut 處理。
"""

import os
import urllib.parse

import yt_dlp


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
