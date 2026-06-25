"""瀑布流搜尋。

決策（與使用者確認）：所有命中最終都匯進「單一 Spotify 清單」。因此本模組把
YT Music / SoundCloud 當成「搜尋信心升級」而非獨立寫入目標：
  Level 1  Spotify 直接搜到 -> 取 URI。
  Level 2  Spotify 沒中 -> 用 YT Music 拿較乾淨的 canonical「Artist - Title」回頭重搜 Spotify。
  Level 3  仍沒中 -> 用 SoundCloud 同樣回頭重搜 Spotify。
若各平台確認存在、但 Spotify 始終沒有 -> 回傳 not_found 並標記 exists_on（進報告，不另開清單）。

YT Music 走 ytmusicapi 免授權搜尋（唯讀，不需 OAuth）。
SoundCloud 官方 API 已關閉，client_id 於執行期從 web player 動態抓取（會隨改版失效）。
"""

import re

import requests

import spotify_client
from config import SOUNDCLOUD_ENABLED
from utils import title_matches, artist_matches

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class Waterfall:
    def __init__(self, sp, enable_ytmusic=True, enable_soundcloud=SOUNDCLOUD_ENABLED):
        self.sp = sp
        self.enable_ytmusic = enable_ytmusic
        self.enable_soundcloud = enable_soundcloud
        self._ytmusic = None
        self._sc_client_id = None
        self._sc_failed = False

    # ---------- Level 1: Spotify ----------
    def _spotify(self, song: str, artist: str) -> str | None:
        try:
            item = spotify_client.search_track(self.sp, song, artist)
        except Exception:
            return None
        return item["uri"] if item else None

    # ---------- Level 2: YT Music（拿乾淨 metadata 回頭搜 Spotify）----------
    def _ytmusic_client(self):
        if self._ytmusic is None:
            from ytmusicapi import YTMusic  # lazy import
            self._ytmusic = YTMusic()       # 免授權即可搜尋
        return self._ytmusic

    def _ytmusic_meta(self, song: str, artist: str) -> tuple[str, str] | None:
        try:
            results = self._ytmusic_client().search(f"{song} {artist}", filter="songs")
        except Exception:
            return None
        for r in results[:5]:
            r_title = r.get("title", "")
            r_artist = ", ".join(a.get("name", "") for a in (r.get("artists") or []))
            if title_matches(song, r_title) and artist_matches([artist], r_artist):
                return r_title, r_artist
        return None

    # ---------- Level 3: SoundCloud（同樣回頭搜 Spotify）----------
    def _soundcloud_client_id(self) -> str | None:
        if self._sc_client_id or self._sc_failed:
            return self._sc_client_id
        try:
            html = requests.get("https://soundcloud.com/", headers={"User-Agent": _UA}, timeout=15).text
            scripts = re.findall(r'<script[^>]+src="([^"]+)"', html)
            for src in reversed(scripts):  # client_id 通常在較後面的 bundle
                if "sndcdn.com" not in src:
                    continue
                js = requests.get(src, headers={"User-Agent": _UA}, timeout=15).text
                m = re.search(r'client_id\s*[:=]\s*"([0-9a-zA-Z]{20,40})"', js)
                if m:
                    self._sc_client_id = m.group(1)
                    return self._sc_client_id
        except Exception:
            pass
        self._sc_failed = True  # 抓不到就整段停用，不每首重試
        return None

    def _soundcloud_meta(self, song: str, artist: str) -> tuple[str, str] | None:
        cid = self._soundcloud_client_id()
        if not cid:
            return None
        try:
            resp = requests.get(
                "https://api-v2.soundcloud.com/search/tracks",
                params={"q": f"{song} {artist}", "client_id": cid, "limit": 5},
                headers={"User-Agent": _UA}, timeout=15,
            )
            collection = resp.json().get("collection", [])
        except Exception:
            return None
        for t in collection:
            r_title = t.get("title", "")
            r_artist = (t.get("user") or {}).get("username", "")
            if title_matches(song, r_title) and artist_matches([artist], r_artist):
                return r_title, r_artist
        return None

    # ---------- 串接 ----------
    def search(self, song: str, artist: str) -> dict:
        base = {"song": song, "artist": artist, "uri": None, "exists_on": []}

        uri = self._spotify(song, artist)
        if uri:
            return {**base, "status": "spotify", "uri": uri}

        if self.enable_ytmusic:
            meta = self._ytmusic_meta(song, artist)
            if meta:
                base["exists_on"].append("ytmusic")
                uri = self._spotify(*meta)
                if uri:
                    return {**base, "status": "spotify_via_ytmusic", "uri": uri}

        if self.enable_soundcloud:
            meta = self._soundcloud_meta(song, artist)
            if meta:
                base["exists_on"].append("soundcloud")
                uri = self._spotify(*meta)
                if uri:
                    return {**base, "status": "spotify_via_soundcloud", "uri": uri}

        return {**base, "status": "not_found"}
