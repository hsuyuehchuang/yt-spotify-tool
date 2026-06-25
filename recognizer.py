"""密集掃描辨識 + 可插拔多辨識引擎 + 容錯/pacing + 每影片 checkpoint。

掃描（scan_track）與引擎解耦：它只吃 slice_fn / recognize_fn 兩個注入點，
所以可以用假的 recognizer（不連網、不裝任何引擎）做單元測試。

引擎（皆 async recognize(bytes) -> dict|None，連續失敗自行 raise RecognitionBlocked）：
  - ShazamRecognizer：shazamio（免授權）。
  - ACRCloudEngine：ACRCloud REST（需金鑰；對 DJ mix 較佳）。
  - EngineChain：依序嘗試多個引擎，前一個沒命中才打下一個（省呼叫、降被 ban 風險）。
共用的 retry / 禮貌間隔 / 連續失敗判定收斂在 _RetryingEngine 基底，避免每個引擎各寫一份。
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import random
import time

from config import SHAZAM_CACHE_DIR
from utils import normalize

# --- 掃描參數（皆可調）。整支從頭掃到尾，不快轉；步長自適應 ---
WINDOW = 12.0     # 每個取樣窗大小（秒）—— Shazam / ACRCloud 一段大約需要 10~15 秒
STEP_MISS = 6.0   # 沒命中 → 前進這麼多（重疊 50% 密掃，避免漏掉沒命中的段落）
STEP_HIT = 12.0   # 命中 → 前進這麼多（不重疊；剛辨識過的整段不必再掃，提速）

# --- 容錯參數（每個引擎共用）---
REQUEST_DELAY_MIN = 1.0  # 每次請求間的禮貌間隔（避免連發誘發限流）
REQUEST_DELAY_MAX = 2.5
RETRY_ATTEMPTS = 3       # 單一窗口的重試次數
RETRY_BASE_DELAY = 2.0   # 指數退避基底：2, 4, 8 秒
BLOCK_THRESHOLD = 4      # 連續幾個窗口「重試全敗」才放棄該引擎


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
    命中的歌依「正規化歌名+歌手」去重（跨引擎也能去重）。

    recognize_fn:(bytes) -> dict|None（async）。回 {'track_id','artist','title'} 或 None。
                 連續失敗時自行 raise RecognitionBlocked。
    on_window:   每個視窗回呼 (pos, track)，可用於進度條。
    on_found:    每找到「新」一首回呼 (pos, song)，可即時印出。
    回傳 found:  [{'song','artist','track_id'}, ...]（已去重）
    """
    pos = 0.0
    seen_keys = set()
    found = []

    while pos + window <= duration:
        chunk = slice_fn(pos, window)
        try:
            track = await recognize_fn(chunk)
        except RecognitionBlocked as e:
            raise RecognitionBlocked(partial=found, reason=e.reason)

        if on_window is not None:
            on_window(pos, track)

        if track:
            key = (normalize(track["title"]), normalize(track["artist"]))
            if key not in seen_keys:
                seen_keys.add(key)
                song = {
                    "song": track["title"],
                    "artist": track["artist"],
                    "track_id": track.get("track_id"),
                }
                found.append(song)
                if on_found is not None:
                    on_found(pos, song)
            pos += step_hit    # 命中：不重疊前進
        else:
            pos += step_miss   # 沒命中：重疊密掃

    return found


class _RetryingEngine:
    """共用的 recognize 包裝：禮貌間隔 + 指數退避重試 + 連續失敗判定。

    子類別實作 _call(data)（async，回原始回應）與 _parse(out)（回 dict|None）。
    """

    name = "engine"

    def __init__(self, attempts=RETRY_ATTEMPTS, base_delay=RETRY_BASE_DELAY,
                 block_after=BLOCK_THRESHOLD,
                 delay_min=REQUEST_DELAY_MIN, delay_max=REQUEST_DELAY_MAX):
        self.attempts = attempts
        self.base_delay = base_delay
        self.block_after = block_after
        self.delay_min = delay_min
        self.delay_max = delay_max
        self._consecutive_fail = 0
        self._disabled = False

    async def _call(self, data: bytes):
        raise NotImplementedError

    @staticmethod
    def _parse(out) -> dict | None:
        raise NotImplementedError

    async def recognize(self, data: bytes) -> dict | None:
        """成功（不論有無命中）回 dict|None；連續失敗丟 RecognitionBlocked。"""
        await asyncio.sleep(random.uniform(self.delay_min, self.delay_max))  # 禮貌間隔

        last_err = ""
        for attempt in range(self.attempts):
            try:
                out = await self._call(data)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                await asyncio.sleep(self.base_delay * (2 ** attempt))
                continue
            else:
                self._consecutive_fail = 0   # 只要呼叫成功就重置
                return self._parse(out)

        # 這個窗口重試全敗（多半是網路/限流）—— 把真實錯誤印出來，不要藏
        self._consecutive_fail += 1
        print(f"  [警告] {self.name} 辨識失敗 ({self._consecutive_fail}/{self.block_after}): {last_err}",
              flush=True)
        if self._consecutive_fail >= self.block_after:
            raise RecognitionBlocked(reason=f"{self.name}: {last_err}")
        return None  # 當作未命中


class ShazamRecognizer(_RetryingEngine):
    """shazamio（免授權）。lazy import，使得不裝 shazamio 也能 import 本模組（測試用）。"""

    name = "Shazam"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._shazam = None

    def _client(self):
        if self._shazam is None:
            from shazamio import Shazam  # lazy
            self._shazam = Shazam()
        return self._shazam

    async def _call(self, data: bytes):
        shazam = self._client()
        if hasattr(shazam, "recognize"):
            return await shazam.recognize(data)        # 新版吃 bytes
        import tempfile                                # 舊版只有 recognize_song(path)
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


class ACRCloudEngine(_RetryingEngine):
    """ACRCloud 辨識 REST API（需 host / access_key / access_secret）。對 DJ mix 較佳。"""

    name = "ACRCloud"

    def __init__(self, host, access_key, access_secret, **kwargs):
        super().__init__(**kwargs)
        self.host = host
        self.access_key = access_key
        self.access_secret = access_secret

    def _identify(self, data: bytes):
        import requests  # lazy
        ts = str(int(time.time()))
        string_to_sign = "\n".join(["POST", "/v1/identify", self.access_key, "audio", "1", ts])
        signature = base64.b64encode(
            hmac.new(self.access_secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
        ).decode()
        resp = requests.post(
            f"https://{self.host}/v1/identify",
            files={"sample": ("sample.wav", data, "audio/wav")},
            data={
                "access_key": self.access_key,
                "data_type": "audio",
                "signature_version": "1",
                "signature": signature,
                "sample_bytes": str(len(data)),
                "timestamp": ts,
            },
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    async def _call(self, data: bytes):
        # requests 是同步的，丟到執行緒避免卡住事件迴圈
        return await asyncio.to_thread(self._identify, data)

    @staticmethod
    def _parse(out) -> dict | None:
        if not out:
            return None
        if (out.get("status") or {}).get("code") != 0:  # 非 0 = 沒對到（不是錯誤）
            return None
        music = (out.get("metadata") or {}).get("music") or []
        if not music:
            return None
        m = music[0]
        title = (m.get("title") or "").strip()
        if not title:
            return None
        artist = ", ".join(a.get("name", "") for a in (m.get("artists") or [])).strip() or "Unknown"
        return {
            "track_id": m.get("acrid") or f"acr:{title}:{artist}",
            "title": title,
            "artist": artist,
        }


class EngineChain:
    """依序嘗試多個引擎：前一個沒命中才打下一個。

    某引擎連續失敗（RecognitionBlocked）就停用它、改用其他引擎；全部停用才往上拋。
    """

    def __init__(self, engines):
        self.engines = list(engines)

    async def recognize(self, data: bytes) -> dict | None:
        for eng in self.engines:
            if eng._disabled:
                continue
            try:
                res = await eng.recognize(data)
            except RecognitionBlocked as e:
                eng._disabled = True
                print(f"  [停用] {eng.name} 連續失敗，改用其他引擎：{e.reason}", flush=True)
                continue
            if res:
                return res
        if all(e._disabled for e in self.engines):
            raise RecognitionBlocked(reason="所有辨識引擎都連續失敗")
        return None


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
