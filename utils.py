import re

_QUALIFIERS = r'feat|ft|with|prod|remix|edit|mix|version|ver|instrumental|radio'
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
