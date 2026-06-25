"""scan_track 掃描邏輯單元測試：不連網、不需 shazamio / ffmpeg。

驗證：整段都掃不快轉、步長自適應（命中跳一窗、沒命中重疊密掃）、track_id 去重、
窄窗短歌也找得到、被封例外帶 partial + reason。
直接執行：python3 test_recognizer.py
"""

import asyncio

import recognizer
from recognizer import scan_track, RecognitionBlocked


def slice_passthrough(start, dur):
    """假切片：把 start 當成 chunk 傳給 recognize_fn。"""
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


def test_adaptive_step_hit_vs_miss():
    # A: [0,40) 命中區；gap [40,80)；duration 80；window=12, step_hit=12, step_miss=6
    regions = [(0, 40, "A", "Song A", "Artist A")]
    rec, calls = make_recognizer(regions)
    found = run(scan_track(80, slice_passthrough, rec, step_miss=6, step_hit=12, window=12))
    assert [f["track_id"] for f in found] == ["A"], found
    # 命中區用 12 步：pos 0,12,24,36；之後進 gap 用 6 步：48,54,60,66（72+12>80 停）
    assert calls["n"] == 8, calls["n"]
    print(f"[OK] 自適應步長：命中跳 12、沒命中跳 6，共 {calls['n']} 視窗")


def test_no_fastforward_finds_narrow_song():
    # 一首只出現在 [90,102) 的窄窗短歌；舊的快進(90~120)會跳過，密集掃描要找得到
    regions = [(90, 102, "C", "Blip", "Artist C")]
    rec, _ = make_recognizer(regions)
    found = run(scan_track(200, slice_passthrough, rec, step_miss=6, step_hit=12, window=12))
    assert [f["track_id"] for f in found] == ["C"], found
    print("[OK] 不快轉：窄窗短歌也找得到")


def test_dedup_across_windows():
    # 同一首橫跨多個視窗，只算一次
    regions = [(0, 60, "A", "Song A", "Artist A")]
    rec, _ = make_recognizer(regions)
    found = run(scan_track(70, slice_passthrough, rec, step_miss=6, step_hit=12, window=12))
    assert [f["track_id"] for f in found] == ["A"], found
    print("[OK] 跨視窗去重")


def test_on_callbacks():
    regions = [(0, 20, "A", "Song A", "Artist A")]
    rec, _ = make_recognizer(regions)
    windows, founds = [], []
    run(scan_track(60, slice_passthrough, rec, step_miss=6, step_hit=12, window=12,
                   on_window=lambda pos, tr: windows.append(pos),
                   on_found=lambda pos, s: founds.append(s["track_id"])))
    assert windows, "on_window 應每視窗被呼叫"
    assert founds == ["A"], founds   # on_found 只在「新」歌觸發一次
    print(f"[OK] on_window({len(windows)} 次) / on_found({founds})")


def test_blocked_carries_partial_and_reason():
    state = {"n": 0}

    async def rec(pos):
        state["n"] += 1
        if state["n"] == 1:
            return {"track_id": "A", "title": "Song A", "artist": "Artist A"}
        raise RecognitionBlocked(reason="HTTPError: 429")

    try:
        run(scan_track(1000, slice_passthrough, rec, step_miss=6, step_hit=12, window=12))
    except RecognitionBlocked as e:
        assert [s["track_id"] for s in e.partial] == ["A"], e.partial
        assert "429" in e.reason, e.reason
        print(f"[OK] blocked 帶 partial + reason: {e.reason}")
    else:
        raise AssertionError("應丟出 RecognitionBlocked")


def test_shazam_parse():
    p = recognizer.ShazamRecognizer._parse
    assert p({}) is None
    assert p({"matches": []}) is None
    assert p({"track": {"title": "T"}}) is None        # 無 key
    got = p({"track": {"key": "123", "title": " T ", "subtitle": " Artist "}})
    assert got == {"track_id": "123", "title": "T", "artist": "Artist"}, got
    print("[OK] ShazamRecognizer._parse")


if __name__ == "__main__":
    test_adaptive_step_hit_vs_miss()
    test_no_fastforward_finds_narrow_song()
    test_dedup_across_windows()
    test_on_callbacks()
    test_blocked_carries_partial_and_reason()
    test_shazam_parse()
    print("\n全部通過")
