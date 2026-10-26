"""Conservative artwork fallback. Scores measure image agreement, not song identity."""

from difflib import SequenceMatcher
from collections import Counter
from io import BytesIO
import math
import re
import unicodedata
import warnings

import requests

try:
    from PIL import Image, ImageOps, ImageStat
except ImportError:
    Image = ImageOps = ImageStat = None

COVER_THRESHOLD = 90.0
TITLE_THRESHOLD = 0.80
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
_VERSIONS = re.compile(
    r"\b(remix|mix|live|instrumental|karaoke|cover|acoustic|edit|remaster(?:ed)?|"
    r"sped|slowed|nightcore|extended|radio)\b", re.I
)


def _words(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.findall(r"\w+", text)


def relaxed_title_matches(name, video_title):
    """Permit small typos, but retain version labels and numeric distinctions."""
    if set(_VERSIONS.findall(name.casefold())) != set(_VERSIONS.findall(video_title.casefold())):
        return False
    source, target = _words(name), _words(video_title)
    if not source or not target:
        return False
    # Compare against title windows to allow an artist prefix / 'Official Audio'.
    for length in range(max(1, len(source) - 1), len(source) + 2):
        for start in range(len(target) - length + 1):
            window = " ".join(target[start:start + length])
            query = " ".join(source)
            if re.findall(r"\d+", query) != re.findall(r"\d+", window):
                continue
            # Short titles need exact words; one letter can identify another song.
            if len(query) < 6:
                if query == window:
                    return True
            elif SequenceMatcher(None, query, window).ratio() >= TITLE_THRESHOLD:
                return True
    return False


def load_image(url):
    """Bound downloads and decoding; unavailable/invalid artwork fails closed."""
    if Image is None or not url or not url.startswith(("https://", "http://")):
        return None
    try:
        with requests.get(url, timeout=(5, 10), stream=True) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > MAX_IMAGE_BYTES:
                    return None
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as img:
                if img.width * img.height > MAX_IMAGE_PIXELS or min(img.size) < 64:
                    return None
                return ImageOps.exif_transpose(img).convert("RGB")
    except (requests.RequestException, OSError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


def _square(img, fraction=1.0):
    side = round(min(img.size) * fraction)
    left, top = (img.width - side) // 2, (img.height - side) // 2
    return img.crop((left, top, left + side, top + side)).resize(
        (64, 64), Image.Resampling.LANCZOS
    )


def _agreement(a, b):
    # Reject featureless covers: their colour alone cannot identify artwork.
    if min(max(ImageStat.Stat(img).stddev) for img in (a, b)) < 8:
        return 0.0
    channel_scores = []
    for ca, cb in zip(a.split(), b.split()):
        xs, ys = ca.tobytes(), cb.tobytes()
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        vx = sum((x - mx) ** 2 for x in xs)
        vy = sum((y - my) ** 2 for y in ys)
        rmse = math.sqrt(sum((x - y) ** 2 for x, y in zip(xs, ys)) / len(xs))
        colour = max(0.0, 1 - rmse / 255)
        if min(vx, vy) < len(xs) * 4:
            # A flat channel carries colour but no reliable structural evidence.
            structure = colour if max(vx, vy) < len(xs) * 4 else 0.0
        else:
            structure = max(0.0, sum((x - mx) * (y - my) for x, y in zip(xs, ys))
                            / math.sqrt(vx * vy))
        channel_scores.append(min(colour, structure))
    return 100 * sum(channel_scores) / len(channel_scores)


def cover_similarity(cover, thumbnail):
    """0–100 heuristic: RGB agreement + spatial correlation, not a probability.

    YouTube often centres square album art in 16:9, or letterboxes that frame
    again into 4:3. Try a few centred crops; never rotate or mirror artwork.
    """
    reference = _square(cover)
    return max(_agreement(reference, _square(thumbnail, fraction))
               for fraction in (1.0, 0.9, 0.8, 0.75))


def match_by_cover(entries, name, artists, duration_sec, cover_url,
                   artist_matches, duration_tolerance=5):
    if Image is None:
        print("    [封面比對未啟用] 請用目前的 Python 安裝 requirements.txt（缺少 Pillow）")
        return None
    eligible = []
    seen = set()
    rejected = Counter()
    for entry in entries:
        if not entry:
            continue
        key = entry.get("id") or entry.get("url")
        duration = entry.get("duration")
        title = entry.get("title") or ""
        channel = entry.get("channel") or entry.get("uploader") or ""
        if not key:
            rejected["缺少影片 ID"] += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        if not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            rejected["缺少有效時長"] += 1
            continue
        if abs(duration - duration_sec) > duration_tolerance:
            rejected["時長不符"] += 1
            continue
        if not artist_matches(artists, title + " " + channel):
            rejected["歌手不符"] += 1
            continue
        if not relaxed_title_matches(name, title):
            rejected["歌名／版本不符"] += 1
            continue
        eligible.append(entry)
    print(f"    [封面候選] 去重後 {len(seen)} 個；符合歌名、歌手與時長 {len(eligible)} 個"
          + ("；排除：" + "、".join(f"{reason} {count}" for reason, count in rejected.items())
             if rejected else ""))
    if not cover_url:
        print("    [封面比對略過] 缺少這首歌曲的專輯／單曲封面（不使用播放清單封面）")
        return None
    if not eligible:
        print("    [尚未比圖] 搜尋結果沒有符合基本條件的歌曲，不是圖片分數不足")
        return None
    print(f"    [封面來源] Spotify 歌曲的專輯／單曲封面：{name}")
    cover = load_image(cover_url)
    if cover is None:
        print("    [封面比對略過] Spotify 封面無法讀取")
        return None
    matches = []
    cache = {}
    for entry in eligible:
        thumbs = entry.get("thumbnails") or []
        thumbs = sorted(thumbs, key=lambda t: (t.get("width") or 0) * (t.get("height") or 0), reverse=True)
        urls = [t.get("url") for t in thumbs]
        urls.append(entry.get("thumbnail"))
        urls = list(dict.fromkeys(u for u in urls if u))[:2]
        score = 0.0
        readable = False
        for url in urls:
            if url not in cache:
                thumb = load_image(url)
                cache[url] = cover_similarity(cover, thumb) if thumb is not None else None
            if cache[url] is not None:
                readable = True
                score = max(score, cache[url])
        label = f"{entry.get('title')} | {entry.get('channel') or entry.get('uploader')}"
        if not readable:
            print(f"    [縮圖無法讀取] {label}")
            continue
        print(f"    [封面分數] {score:.1f}/100（門檻 {COVER_THRESHOLD:.0f}）| {label}")
        if score >= COVER_THRESHOLD:
            matches.append((score, entry))
    matches.sort(key=lambda pair: pair[0], reverse=True)
    if not matches:
        print("    [封面比對未通過] 無可讀且相似度達 90 分的候選縮圖")
        return None
    if len(matches) > 1 and matches[0][0] - matches[1][0] < 2:
        print("    [封面比對有歧義] 多個候選分數接近，跳過以免下載錯歌")
        return None
    score, entry = matches[0]
    print(f"    [封面配對] {score:.1f}/100 | {entry.get('title')} | {entry.get('channel') or entry.get('uploader')}"
          f" | 時長差 {abs(entry['duration'] - duration_sec):.1f} 秒")
    return entry.get("url") or entry.get("id")
