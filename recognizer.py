"""密集掃描辨識 + shazamio 容錯 + 每影片 checkpoint。

掃描（scan_track）刻意與 shazamio 解耦：它只吃 slice_fn / recognize_fn 兩個注入點，
所以可以用假的 recognizer（不連網、不裝 shazamio）做單元測試。
真正打 Shazam 的 ShazamRecognizer 另外提供 recognize_fn。
"""

import asyncio
import json
import os
import random

from config import SHAZAM_CACHE_DIR

# --- 掃描參數（皆可調）。整支從頭掃到尾，不快轉；步長自適應 ---
WINDOW = 12.0     # 每個取樣窗大小（秒）—— Shazam 一段大約需要 12 秒
STEP_MISS = 6.0   # 沒命中 → 前進這麼多（重疊 50% 密掃，避免漏掉沒命中的段落）
STEP_HIT = 12.0   # 命中 → 前進這麼多（不重疊；剛辨識過的整段不必再掃，提速）

# --- shazamio 容錯參數 ---
REQUEST_DELAY_MIN = 1.0  # 每次 Shazam 請求間的禮貌間隔（避免連發誘發限流）
REQUEST_DELAY_MAX = 2.5
RETRY_ATTEMPTS = 3       # 單一窗口的重試次數
RETRY_BASE_DELAY = 2.0   # 指數退避基底：2, 4, 8 秒（給限流足夠冷卻時間）
BLOCK_THRESHOLD = 4      # 連續幾個窗口「重試全敗」才放棄、優雅停止


class RecognitionBlocked(Exception):
    """連續辨識失敗（疑似限流或網路問題）。partial 帶當前影片已找到的歌，reason 帶真實錯誤。"""

    def __init__(self, partial=None, reason=""):
        super().__init__(reason or "recognition repeatedly failed")
        self.partial = partial or []
        self.reason = reason


async def scan_track(duration, slice_fn, recognize_fn, *, step_miss=STEP_MISS,
                     step_hit=STEP_HIT, window=WINDOW, on_window=None, on_found=None):
    """
    從頭到尾掃描（不快轉），步長自適應：
      沒命中 → 前進 step_miss（重疊密掃，每個時間點被多視窗涵蓋，避免漏）
      命中   → 前進 step_hit（不重疊；剛辨識過的整段不必再掃）
    命中的歌依 track_id 去重。

    duration:    音軌長度（秒）
    slice_fn:    (start, dur) -> bytes，同步切片
    recognize_fn:(bytes) -> dict|None（async）。回 {'track_id','artist','title'} 或 None。
                 連續失敗時自行 raise RecognitionBlocked。
    on_window:   每個視窗回呼 (pos, track)，可用於進度條。
    on_found:    每找到「新」一首回呼 (pos, song)，可即時印出。
    回傳 found:  [{'song','artist','track_id'}, ...]（已依 track_id 去重）
    """
    pos = 0.0
    seen_ids = set()
    found = []

    while pos + window <= duration:
        chunk = slice_fn(pos, window)
        try:
            track = await recognize_fn(chunk)
        except RecognitionBlocked as e:
            # 把目前進度塞進例外交給上層；半截影片不快取（重跑會重做這支）
            raise RecognitionBlocked(partial=found, reason=e.reason)

        if on_window is not None:
            on_window(pos, track)

        if track:
            tid = track["track_id"]
            if tid not in seen_ids:
                seen_ids.add(tid)
                song = {
                    "song": track["title"],
                    "artist": track["artist"],
                    "track_id": tid,
                }
                found.append(song)
                if on_found is not None:
                    on_found(pos, song)
            pos += step_hit    # 命中：不重疊前進
        else:
            pos += step_miss   # 沒命中：重疊密掃

    return found


class ShazamRecognizer:
    """包 shazamio：指數退避重試，連續多窗失敗判定被封鎖。

    shazamio 為 lazy import，使得不裝 shazamio 也能 import 本模組（測試用假 recognizer）。
    """

    def __init__(self, attempts=RETRY_ATTEMPTS, base_delay=RETRY_BASE_DELAY,
                 block_after=BLOCK_THRESHOLD,
                 delay_min=REQUEST_DELAY_MIN, delay_max=REQUEST_DELAY_MAX):
        self.attempts = attempts
        self.base_delay = base_delay
        self.block_after = block_after
        self.delay_min = delay_min
        self.delay_max = delay_max
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
        """單一窗口辨識。成功（不論有無命中）回 dict|None；連續失敗丟 RecognitionBlocked。"""
        # 禮貌間隔：每次請求前小睡，避免連發誘發 Shazam 限流
        await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))

        last_err = ""
        for attempt in range(self.attempts):
            try:
                out = await self._call(data)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                await asyncio.sleep(self.base_delay * (2 ** attempt))
                continue
            else:
                self._consecutive_fail = 0   # 只要 HTTP 成功就重置
                return self._parse(out)

        # 這個窗口重試全敗（多半是網路/限流）—— 把真實錯誤印出來，不要藏
        self._consecutive_fail += 1
        print(f"  [警告] 辨識失敗 ({self._consecutive_fail}/{self.block_after}): {last_err}", flush=True)
        if self._consecutive_fail >= self.block_after:
            raise RecognitionBlocked(reason=last_err)
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
