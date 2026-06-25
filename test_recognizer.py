"""scan_track 狀態機單元測試：不連網、不需 shazamio / ffmpeg。

用假的 slice_fn（直接回傳 pos）+ 假的 recognize_fn（依 pos 判定命中）+ 固定 rng，
驗證命中快進、未命中平移、連續 3 次未命中跳過、track_id 去重、被封例外。
直接執行：python3 test_recognizer.py
"""

import asyncio

import recognizer
from recognizer import scan_track, RecognitionBlocked


class FixedRng:
    """uniform 一律回傳下界，讓 pos 推進完全可預測。"""

    def uniform(self, a, b):
        return a


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


def test_hit_fastforward_dedup_two_songs():
    # A: [0,50)  gap  B: [100,300)   duration 320
    regions = [
        (0, 50, "A", "Song A", "Artist A"),
        (100, 300, "B", "Song B", "Artist B"),
    ]
    rec, calls = make_recognizer(regions)
    found = run(scan_track(320, slice_passthrough, rec, rng=FixedRng()))
    ids = [f["track_id"] for f in found]
    assert ids == ["A", "B"], ids                      # 兩首、各一次（去重）
    assert found[0]["song"] == "Song A"
    assert found[1]["artist"] == "Artist B"
    print(f"[OK] hit/fastforward/dedup: found={ids}, recognize_calls={calls['n']}")


def test_three_miss_triggers_skip():
    # 全程無歌，duration 60。連 3 次 miss 應改快進（FF=90）直接跳出，而非一路平移。
    rec, calls = make_recognizer([])
    found = run(scan_track(60, slice_passthrough, rec, rng=FixedRng()))
    assert found == [], found
    # SLIDE=5 平移：pos 0->5->10，第 3 次 miss 觸發 FF=90 -> pos=100 > 60 結束 => 共 3 次
    assert calls["n"] == 3, calls["n"]
    print(f"[OK] 3-miss skip: recognize_calls={calls['n']} (若退化成純平移會是 ~10+)")


def test_blocked_carries_partial():
    # 先命中 A，之後 recognize 丟 RecognitionBlocked，scan_track 應帶 partial 重拋
    state = {"n": 0}

    async def rec(pos):
        state["n"] += 1
        if state["n"] == 1:
            return {"track_id": "A", "title": "Song A", "artist": "Artist A"}
        raise RecognitionBlocked()

    try:
        run(scan_track(1000, slice_passthrough, rec, rng=FixedRng()))
    except RecognitionBlocked as e:
        assert [s["track_id"] for s in e.partial] == ["A"], e.partial
        print(f"[OK] blocked carries partial: {[s['song'] for s in e.partial]}")
    else:
        raise AssertionError("應丟出 RecognitionBlocked")


def test_shazam_parse():
    # _parse：有 track.key 才算命中
    p = recognizer.ShazamRecognizer._parse
    assert p({}) is None
    assert p({"matches": []}) is None
    assert p({"track": {"title": "T"}}) is None        # 無 key
    got = p({"track": {"key": "123", "title": " T ", "subtitle": " Artist "}})
    assert got == {"track_id": "123", "title": "T", "artist": "Artist"}, got
    print("[OK] ShazamRecognizer._parse")


if __name__ == "__main__":
    test_hit_fastforward_dedup_two_songs()
    test_three_miss_triggers_skip()
    test_blocked_carries_partial()
    test_shazam_parse()
    print("\n全部通過")
