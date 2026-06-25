"""recognizer 單元測試：不連網、不需 shazamio / ffmpeg / 金鑰。

涵蓋：自適應掃描（命中跳一窗、沒命中重疊密掃、不快轉）、跨引擎文字去重、
EngineChain 順序與失敗停用、各引擎 _parse、被封例外帶 partial + reason。
直接執行：python3 test_recognizer.py
"""

import asyncio

import recognizer
from recognizer import EngineChain, RecognitionBlocked, scan_track


def slice_passthrough(start, dur):
    return start


def make_recognizer(regions):
    """regions: [(lo, hi, track_id, title, artist), ...]，pos 落在區間就命中。"""
    calls = {"n": 0}

    async def recognize(pos):
        calls["n"] += 1
        for lo, hi, tid, title, artist in regions:
            if lo <= pos < hi:
                return {"track_id": tid, "title": title, "artist": artist}
        return None

    return recognize, calls


def run(coro):
    return asyncio.run(coro)


# ---------------- scan_track ----------------

def test_adaptive_step_hit_vs_miss():
    regions = [(0, 40, "A", "Song A", "Artist A")]   # 命中區 [0,40)，其餘為 gap
    rec, calls = make_recognizer(regions)
    found = run(scan_track(80, slice_passthrough, rec, step_miss=6, step_hit=12, window=12))
    assert [f["track_id"] for f in found] == ["A"], found
    # 命中區跳 12：0,12,24,36；gap 跳 6：48,54,60,66（72+12>80 停）= 8 視窗
    assert calls["n"] == 8, calls["n"]
    print(f"[OK] 自適應步長：命中跳 12、沒命中跳 6，共 {calls['n']} 視窗")


def test_no_fastforward_finds_narrow_song():
    regions = [(90, 102, "C", "Blip", "Artist C")]   # 只出現 12 秒的窄窗短歌
    rec, _ = make_recognizer(regions)
    found = run(scan_track(200, slice_passthrough, rec, step_miss=6, step_hit=12, window=12))
    assert [f["track_id"] for f in found] == ["C"], found
    print("[OK] 不快轉：窄窗短歌也找得到")


def test_text_dedup_across_engines():
    # 同一首歌每次回不同 track_id（模擬不同引擎的不同 ID 空間）→ 仍應依歌名去重成 1 首
    async def rec(pos):
        if 0 <= pos < 40:
            return {"track_id": f"id-{int(pos)}", "title": "Song A (Club Mix)", "artist": "Artist A"}
        return None

    found = run(scan_track(60, slice_passthrough, rec, step_miss=6, step_hit=12, window=12))
    assert [f["song"] for f in found] == ["Song A (Club Mix)"], found
    print("[OK] 跨引擎文字去重（不同 track_id 仍去重）")


def test_blocked_carries_partial_and_reason():
    state = {"n": 0}

    async def rec(pos):
        state["n"] += 1
        if state["n"] == 1:
            return {"track_id": "A", "title": "Song A", "artist": "Artist A"}
        raise RecognitionBlocked(reason="Shazam: HTTPError 429")

    try:
        run(scan_track(1000, slice_passthrough, rec, step_miss=6, step_hit=12, window=12))
    except RecognitionBlocked as e:
        assert [s["track_id"] for s in e.partial] == ["A"], e.partial
        assert "429" in e.reason, e.reason
        print(f"[OK] blocked 帶 partial + reason: {e.reason}")
    else:
        raise AssertionError("應丟出 RecognitionBlocked")


# ---------------- EngineChain ----------------

class FakeEngine:
    def __init__(self, name, behavior):
        self.name = name
        self._disabled = False
        self.behavior = behavior
        self.calls = 0

    async def recognize(self, data):
        self.calls += 1
        return self.behavior(data)   # 可回 dict/None 或 raise


def test_engine_chain_fallback_order():
    e1 = FakeEngine("E1", lambda d: None)
    e2 = FakeEngine("E2", lambda d: {"track_id": "x", "title": "T", "artist": "A"})
    chain = EngineChain([e1, e2])
    res = run(chain.recognize(b""))
    assert res["title"] == "T" and e1.calls == 1 and e2.calls == 1   # E1 沒中 → 才打 E2
    # E1 命中時不應再打 E2
    e1.behavior = lambda d: {"track_id": "y", "title": "H", "artist": "A"}
    res = run(chain.recognize(b""))
    assert res["title"] == "H" and e2.calls == 1, (res, e2.calls)
    print("[OK] EngineChain：前者沒中才打後者；前者命中就短路")


def test_engine_chain_disable_on_block():
    def boom(d):
        raise RecognitionBlocked(reason="429")

    e1 = FakeEngine("E1", boom)
    e2 = FakeEngine("E2", lambda d: {"track_id": "x", "title": "T", "artist": "A"})
    chain = EngineChain([e1, e2])
    res = run(chain.recognize(b""))
    assert res["title"] == "T" and e1._disabled is True
    run(chain.recognize(b""))
    assert e1.calls == 1, e1.calls   # 停用後不再呼叫 E1
    print("[OK] EngineChain：引擎連續失敗被停用、改用其他引擎")


# ---------------- _parse ----------------

def test_shazam_parse():
    p = recognizer.ShazamRecognizer._parse
    assert p({}) is None
    assert p({"track": {"title": "T"}}) is None        # 無 key
    got = p({"track": {"key": "123", "title": " T ", "subtitle": " Artist "}})
    assert got == {"track_id": "123", "title": "T", "artist": "Artist"}, got
    print("[OK] ShazamRecognizer._parse")


def test_acrcloud_parse():
    p = recognizer.ACRCloudEngine._parse
    assert p({}) is None
    assert p({"status": {"code": 1001}, "metadata": {}}) is None       # 沒對到
    assert p({"status": {"code": 0}, "metadata": {"music": []}}) is None
    got = p({"status": {"code": 0}, "metadata": {"music": [
        {"title": "T", "artists": [{"name": "A1"}, {"name": "A2"}], "acrid": "xyz"}]}})
    assert got == {"track_id": "xyz", "title": "T", "artist": "A1, A2"}, got
    print("[OK] ACRCloudEngine._parse")


if __name__ == "__main__":
    test_adaptive_step_hit_vs_miss()
    test_no_fastforward_finds_narrow_song()
    test_text_dedup_across_engines()
    test_blocked_carries_partial_and_reason()
    test_engine_chain_fallback_order()
    test_engine_chain_disable_on_block()
    test_shazam_parse()
    test_acrcloud_parse()
    print("\n全部通過")
