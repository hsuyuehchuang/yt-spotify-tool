"""動態滑動時間窗辨識 + shazamio 容錯 + 每影片 checkpoint。

狀態機（scan_track）刻意與 shazamio 解耦：它只吃 slice_fn / recognize_fn 兩個注入點，
所以可以用假的 recognizer（不連網、不裝 shazamio）做單元測試。
真正打 Shazam 的 ShazamRecognizer 另外提供 recognize_fn。
"""

import asyncio
import json
import os
import random

from config import SHAZAM_CACHE_DIR

# --- 滑動窗參數（規格 Phase 2，皆可調）---
WINDOW = 12.0           # 每次取樣窗大小（秒）
FF_MIN, FF_MAX = 90.0, 120.0     # 命中後快進距離
SLIDE_MIN, SLIDE_MAX = 5.0, 10.0  # 未命中平移距離
MAX_MISS = 3            # 連續未命中幾次就判定無歌、改快進跳過

# --- shazamio 容錯參數 ---
RETRY_ATTEMPTS = 3      # 單一窗口的重試次數
RETRY_BASE_DELAY = 1.0  # 指數退避基底：1, 2, 4 秒
BLOCK_THRESHOLD = 3     # 連續幾個窗口「重試全敗」就判定被封鎖、優雅停止


class RecognitionBlocked(Exception):
    """連續辨識失敗，疑似被 Shazam 限流/封鎖。partial 帶當前影片已找到的歌。"""

    def __init__(self, partial=None):
        super().__init__("recognition blocked / rate-limited")
        self.partial = partial or []


async def scan_track(duration, slice_fn, recognize_fn, *, rng=None, on_window=None):
    """
    duration:    音軌長度（秒）
    slice_fn:    (start, dur) -> bytes，同步切片
    recognize_fn:(bytes) -> dict|None（async）。回 {'track_id','artist','title'} 或 None。
                 疑似被封時自行 raise RecognitionBlocked。
    rng:         需有 .uniform(a, b)，預設 random 模組（測試可注入固定值）。
    on_window:   除錯回呼 (pos, track)。
    回傳 found:  [{'song','artist','track_id'}, ...]（已依 track_id 去重）
    """
    if rng is None:
        rng = random

    pos = 0.0
    consecutive_miss = 0
    seen_ids = set()
    found = []

    while pos + WINDOW <= duration:
        chunk = slice_fn(pos, WINDOW)
        try:
            track = await recognize_fn(chunk)
        except RecognitionBlocked:
            # 把目前進度塞進例外交給上層；半截影片不快取（重跑會重做這支）
            raise RecognitionBlocked(found)

        if on_window is not None:
            on_window(pos, track)

        if track:
            tid = track["track_id"]
            if tid not in seen_ids:
                seen_ids.add(tid)
                found.append({
                    "song": track["title"],
                    "artist": track["artist"],
                    "track_id": tid,
                })
            pos += rng.uniform(FF_MIN, FF_MAX)   # 命中一律快進
            consecutive_miss = 0
        else:
            consecutive_miss += 1
            if consecutive_miss < MAX_MISS:
                pos += rng.uniform(SLIDE_MIN, SLIDE_MAX)   # 平移重試
            else:
                pos += rng.uniform(FF_MIN, FF_MAX)         # 判定無歌，跳過
                consecutive_miss = 0

    return found


class ShazamRecognizer:
    """包 shazamio：指數退避重試，連續多窗失敗判定被封鎖。

    shazamio 為 lazy import，使得不裝 shazamio 也能 import 本模組（測試用假 recognizer）。
    """

    def __init__(self, attempts=RETRY_ATTEMPTS, base_delay=RETRY_BASE_DELAY,
                 block_after=BLOCK_THRESHOLD):
        self.attempts = attempts
        self.base_delay = base_delay
        self.block_after = block_after
        self._shazam = None
        self._consecutive_fail = 0

    def _client(self):
        if self._shazam is None:
            from shazamio import Shazam  # lazy：只有真的要辨識才需要安裝
            self._shazam = Shazam()
        return self._shazam

    async def _call(self, data: bytes):
        shazam = self._client()
        if hasattr(shazam, "recognize"):
            return await shazam.recognize(data)        # 新版：直接吃 bytes
        # 舊版只有 recognize_song(path)：寫暫存檔再餵
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(data)
            tmp = tf.name
        try:
            return await shazam.recognize_song(tmp)
        finally:
            os.unlink(tmp)

    @staticmethod
    def _parse(out) -> dict | None:
        if not out:
            return None
        track = out.get("track")
        if not track or not track.get("key"):
            return None
        return {
            "track_id": track["key"],
            "title": (track.get("title") or "").strip(),
            "artist": (track.get("subtitle") or "Unknown").strip(),
        }

    async def recognize(self, data: bytes) -> dict | None:
        """單一窗口辨識。成功（不論有無命中）回 dict|None；疑似被封丟 RecognitionBlocked。"""
        for attempt in range(self.attempts):
            try:
                out = await self._call(data)
            except Exception:
                await asyncio.sleep(self.base_delay * (2 ** attempt))
                continue
            else:
                self._consecutive_fail = 0   # 只要 HTTP 成功就重置
                return self._parse(out)

        # 這個窗口重試全敗（多半是網路/限流）
        self._consecutive_fail += 1
        if self._consecutive_fail >= self.block_after:
            raise RecognitionBlocked()
        return None  # 當作未命中，讓狀態機繼續平移


# --- 每影片 checkpoint（沿用既有 .yt-music-cache 的每影片一個 JSON 模式）---

def _checkpoint_path(video_id: str) -> str:
    return os.path.join(SHAZAM_CACHE_DIR, f"{video_id}.json")


def load_checkpoint(video_id: str) -> list[dict] | None:
    path = _checkpoint_path(video_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save_checkpoint(video_id: str, songs: list[dict]) -> None:
    if not songs:
        return  # 不存空結果，避免被封/失敗時把空清單當成「這支沒歌」誤快取
    os.makedirs(SHAZAM_CACHE_DIR, exist_ok=True)
    with open(_checkpoint_path(video_id), "w", encoding="utf-8") as f:
        json.dump(songs, f, ensure_ascii=False, indent=2)
