"""可被 import 的 Spotify 模組。

從 yt-to-spotify.py 抽出（該檔名含 '-' 無法被 import），讓 shazam_pipeline 與
waterfall 也能共用同一套搜尋 / 建立清單 / URI 去重邏輯，不必複製。
行為與原本 yt-to-spotify.py 內的版本一致。
"""

import spotipy
from spotipy.oauth2 import SpotifyOAuth

from config import (
    SPOTIFY_CLIENT_ID,
    SPOTIFY_CLIENT_SECRET,
    SPOTIFY_REDIRECT_URI,
    SPOTIFY_SCOPE_WRITE,
)
from utils import title_matches, artist_matches, normalize


def get_client(scope: str = SPOTIFY_SCOPE_WRITE) -> spotipy.Spotify:
    """建立帶 OAuth 的 Spotify client。token 會存在 .cache。"""
    return spotipy.Spotify(auth_manager=SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=scope,
    ))


def search_track(sp, song: str, artist: str) -> dict | None:
    """多輪搜尋，由精準到寬鬆。每輪結果都過 title_matches + artist_matches 驗證。"""
    artists = [a.strip() for a in artist.split(",")]
    main_artist = artists[0]

    def pick(items):
        for item in items:
            r_name = item["name"]
            r_artist = ", ".join(a["name"] for a in item["artists"])
            if title_matches(song, r_name) and artist_matches(artists, r_artist):
                return item
        return None

    # 收集多種查詢策略
    queries = [
        f"track:{song} artist:{artist}",                # 結構化
        f"{song} {artist}",                             # 純文字（完整）
        f"{normalize(song)} {main_artist}",             # 移掉 (Radio Edit) 等補充資訊
        f"{normalize(song)} {normalize(main_artist)}",  # 雙邊 normalize
    ]

    seen = set()
    for q in queries:
        if q in seen:
            continue
        seen.add(q)
        r = sp.search(q=q, type="track", limit=5)
        matched = pick(r["tracks"]["items"])
        if matched:
            return matched

    return None


def find_or_create_playlist(sp, user_id: str, playlist_name: str) -> dict:
    """找同名的既有播放清單，找不到才新建。"""
    offset = 0
    while True:
        resp = sp.current_user_playlists(limit=50, offset=offset)
        for pl in resp["items"]:
            if pl["name"] == playlist_name and pl["owner"]["id"] == user_id:
                print(f"  使用已存在的播放清單: {playlist_name}")
                return pl
        if not resp["next"]:
            break
        offset += 50

    print(f"  建立新播放清單: {playlist_name}")
    return sp._post("me/playlists", payload={"name": playlist_name, "public": False})


def existing_track_uris(sp, playlist_id: str) -> set:
    """取得播放清單裡已有的 track URI，用於去重。"""
    # 注意：Spotify API 的 key 是 "item" 不是 "track"，舊版相容兩種
    uris = set()
    offset = 0
    while True:
        resp = sp.playlist_items(playlist_id, offset=offset, limit=100)
        for entry in resp.get("items", []):
            t = entry.get("item") or entry.get("track")
            if t and t.get("uri"):
                uris.add(t["uri"])
        if not resp.get("next"):
            break
        offset += 100
    return uris


def add_uris(sp, playlist_id: str, uris: list[str]) -> None:
    """分批加入（Spotify playlist_add_items 單次上限 100）。"""
    for i in range(0, len(uris), 100):
        sp.playlist_add_items(playlist_id, uris[i:i + 100])


def build_spotify_playlist(songs: list[dict], playlist_name: str, sp=None):
    """songs: [{'song','artist'}, ...]，搜尋後寫入（去重）指定播放清單。"""
    if sp is None:
        sp = get_client()

    user_id = sp.me()["id"]
    print(f"\n登入成功，Spotify 帳號: {user_id}")

    playlist = find_or_create_playlist(sp, user_id, playlist_name)
    playlist_id = playlist["id"]

    existing_uris = existing_track_uris(sp, playlist_id)
    print(f"  播放清單已有 {len(existing_uris)} 首歌")

    track_uris = []
    not_found = []

    print("\n搜尋 Spotify 歌曲中...")
    for s in songs:
        matched = search_track(sp, s["song"], s["artist"])

        if matched:
            uri = matched["uri"]
            if uri in existing_uris:
                print(f"  [已在清單] {s['song']} - {s['artist']}")
            else:
                track_uris.append(uri)
                existing_uris.add(uri)
                print(f"  [找到] {s['song']} - {s['artist']}")
        else:
            not_found.append(f"{s['song']} - {s['artist']}")
            print(f"  [找不到] {s['song']} - {s['artist']}")

    if track_uris:
        add_uris(sp, playlist_id, track_uris)

    print(f"\n播放清單更新完成！")
    print(f"名稱: {playlist_name}")
    print(f"新增: {len(track_uris)} 首")
    if not_found:
        print(f"找不到: {len(not_found)} 首")
        for name in not_found:
            print(f"  - {name}")
    print(f"連結: {playlist['external_urls']['spotify']}")
