import datetime
import os
import re

_QUALIFIERS = r'feat|ft|with|prod|remix|edit|mix|version|ver|instrumental|radio'


def make_output_dir() -> str:
    """所有下載統一放到「當天日期」資料夾 ./YYYY-MM-DD（不再每次開新資料夾）。"""
    path = f"./{datetime.date.today().strftime('%Y-%m-%d')}"
    os.makedirs(path, exist_ok=True)
    return path
_STOPWORDS = {"the", "a", "an", "and", "or", "of", "in", "on", "at", "to", "is", "it"}


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(rf'\(({_QUALIFIERS})[\s\S]*?\)', '', text)
    text = re.sub(rf'\[({_QUALIFIERS})[\s\S]*?\]', '', text)
    text = re.sub(rf'\s*-\s*({_QUALIFIERS})\b[\s\S]*$', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def word_overlap(source: str, target: str) -> float:
    """source 的關鍵字詞有多少比例出現在 target 裡（0.0 ~ 1.0）"""
    words = set(normalize(source).split()) - _STOPWORDS
    if not words:
        return 0.0
    target_words = set(normalize(target).split())
    return len(words & target_words) / len(words)


def title_matches(query_name: str, result_name: str, threshold: float = 0.8) -> bool:
    return word_overlap(query_name, result_name) >= threshold


def artist_matches(query_artists: list[str], result_artist: str, threshold: float = 0.7) -> bool:
    """query_artists 裡任一個與 result_artist 的 overlap 達標就算符合"""
    return any(word_overlap(a, result_artist) >= threshold for a in query_artists)


def print_summary(success, skipped, failed, header: str = "完成"):
    """各下載 script 共用的結尾統計（對齊 bandcamp 的 成功/已存在/失敗 措辭）。"""
    print(f"\n{'=' * 50}")
    print(f"{header}：成功 {len(success)} | 已存在 {len(skipped)} | 失敗 {len(failed)}")
    if failed:
        for f in failed:
            print(f"  - 失敗: {f}")
