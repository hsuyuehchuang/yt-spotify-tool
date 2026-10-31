"""Offline regression tests: no Spotify credentials, network or audio downloads."""
import importlib.util
from contextlib import redirect_stdout
from io import BytesIO
from io import StringIO
from pathlib import Path
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
import requests

import cover_match as cm
from utils import artist_matches

spec = importlib.util.spec_from_file_location("spotify_download", Path(__file__).with_name("spotify-to-mp3.py"))
spotify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(spotify)


def artwork():
    img = Image.new("RGB", (256, 256), (40, 70, 100))
    draw = ImageDraw.Draw(img)
    draw.rectangle((30, 45, 175, 220), fill=(220, 110, 60))
    draw.ellipse((90, 10, 240, 160), fill=(50, 200, 140))
    draw.line((0, 255, 200, 20), fill="white", width=10)
    return img


def candidate(**kwargs):
    entry = dict(id="one", url="https://www.youtube.com/watch?v=one",
                 title="Afterhour Drive", channel="Low Heat Beats", duration=131,
                 thumbnails=[dict(url="https://example.test/thumb", width=480, height=270)])
    entry.update(kwargs)
    return entry


class CoverTests(unittest.TestCase):
    def test_jpeg_and_pillarboxed_cover(self):
        cover = artwork()
        frame = Image.new("RGB", (456, 256), (30, 30, 30))
        frame.paste(cover, (100, 0))
        data = BytesIO()
        frame.save(data, format="JPEG", quality=75)
        thumb = Image.open(BytesIO(data.getvalue())).convert("RGB")
        self.assertGreaterEqual(cm.cover_similarity(cover, thumb), 90)

    def test_double_letterbox(self):
        cover = artwork()
        frame = Image.new("RGB", (480, 360), "black")
        frame.paste(cover.resize((270, 270)), (105, 45))
        self.assertGreaterEqual(cm.cover_similarity(cover, frame), 90)

    def test_unrelated_and_blank_images(self):
        cover = artwork()
        self.assertLess(cm.cover_similarity(cover, cover.transpose(Image.Transpose.FLIP_LEFT_RIGHT)), 90)
        blank = Image.new("RGB", (256, 256), "white")
        self.assertEqual(cm.cover_similarity(blank, blank), 0)

    def test_title_guard(self):
        self.assertTrue(cm.relaxed_title_matches("Afterhours Drive", "Low Heat - Afterhour Drive (Official Audio)"))
        self.assertTrue(cm.relaxed_title_matches("Afterhours Drive", "After Hours Drive"))
        for title in ("Another Track", "Afterhours Drive (Remix)", "Afterhours Drive Live"):
            self.assertFalse(cm.relaxed_title_matches("Afterhours Drive", title))
        self.assertFalse(cm.relaxed_title_matches("Track 1", "Track 2"))
        self.assertFalse(cm.relaxed_title_matches("Stay", "Say"))

    def match(self, entries):
        return cm.match_by_cover(entries, "Afterhours Drive", ["Low Heat"], 131,
                                 "https://example.test/cover", artist_matches)

    @patch.object(cm, "load_image", side_effect=lambda url: artwork())
    def test_typo_rescued_and_duplicate_id_ignored(self, load):
        self.assertEqual(self.match([candidate(), candidate()]), candidate()["url"])

    @patch.object(cm, "load_image", side_effect=lambda url: artwork())
    def test_same_album_wrong_title_artist_duration_version_rejected(self, load):
        for changes in (dict(title="Another Track"), dict(channel="Someone Else"),
                        dict(duration=140), dict(duration=None), dict(duration=float("nan")),
                        dict(title="Afterhour Drive (Remix)")):
            with self.subTest(changes=changes):
                self.assertIsNone(self.match([candidate(**changes)]))
        load.assert_not_called()

    @patch.object(cm, "load_image", side_effect=lambda url: artwork())
    def test_ambiguous_same_cover_does_not_download(self, load):
        self.assertIsNone(self.match([candidate(), candidate(id="two", url="https://example.test/two")]))

    @patch.object(cm, "load_image", return_value=None)
    def test_unavailable_cover(self, load):
        self.assertIsNone(self.match([candidate()]))

    @patch.object(cm, "load_image")
    def test_no_eligible_candidates_reports_why_before_comparing_images(self, load):
        output = StringIO()
        with redirect_stdout(output):
            self.assertIsNone(self.match([candidate(duration=400), candidate(id="two", channel="Wrong Artist")]))
        self.assertIn("時長不符 1", output.getvalue())
        self.assertIn("歌手不符 1", output.getvalue())
        self.assertIn("尚未比圖", output.getvalue())
        load.assert_not_called()

    @patch.object(cm, "load_image", side_effect=lambda url: artwork())
    @patch.object(cm, "cover_similarity", return_value=89.0)
    def test_below_threshold_score_is_reported(self, similarity, load):
        output = StringIO()
        with redirect_stdout(output):
            self.assertIsNone(self.match([candidate()]))
        self.assertIn("89.0/100", output.getvalue())

    @patch.object(cm, "load_image", side_effect=lambda url: artwork())
    def test_missing_thumbnail(self, load):
        self.assertIsNone(self.match([candidate(thumbnails=[])]))

    @patch.object(cm, "Image", None)
    def test_missing_pillow(self):
        self.assertIsNone(self.match([candidate()]))

    @patch.object(cm.requests, "get", side_effect=requests.Timeout)
    def test_image_timeout(self, get):
        self.assertIsNone(cm.load_image("https://example.test/timeout"))

    @patch.object(cm.requests, "get")
    def test_invalid_and_oversize_payloads(self, get):
        response = get.return_value.__enter__.return_value
        response.iter_content.return_value = [b"not an image"]
        self.assertIsNone(cm.load_image("https://example.test/bad"))
        with patch.object(cm, "MAX_IMAGE_BYTES", 10):
            response.iter_content.return_value = [b"x" * 11]
            self.assertIsNone(cm.load_image("https://example.test/big"))


class SearchTests(unittest.TestCase):
    @patch.object(spotify, "match_by_cover")
    @patch.object(spotify.yt_dlp, "YoutubeDL")
    def test_strict_success_never_downloads_images(self, ydl, match):
        ydl.return_value.__enter__.return_value.extract_info.return_value = {
            "entries": [candidate(title="Afterhours Drive")]}
        self.assertEqual(spotify.search_youtube("Afterhours Drive", "Low Heat", 131000, "cover"), candidate()["url"])
        match.assert_not_called()

    @patch.object(cm, "load_image", side_effect=lambda url: artwork())
    @patch.object(spotify.yt_dlp, "YoutubeDL")
    def test_full_fallback_path(self, ydl, load):
        extract = ydl.return_value.__enter__.return_value.extract_info
        extract.side_effect = [{"entries": [candidate()]}, {"entries": []},
                               {"entries": []}, {"entries": [candidate()]}]
        self.assertEqual(spotify.search_youtube("Afterhours Drive", "Low Heat", 131000, "cover"), candidate()["url"])
        self.assertEqual(extract.call_count, 4)

    @patch.object(spotify, "match_by_cover")
    @patch.object(spotify.yt_dlp, "YoutubeDL")
    def test_in_the_sun_recovered_by_quoted_search_without_cover(self, ydl, match):
        extract = ydl.return_value.__enter__.return_value.extract_info
        expected = candidate(id="SdygxLr5rJo", url="https://www.youtube.com/watch?v=SdygxLr5rJo",
                             title="In The Sun", channel="Low Heat - Topic", duration=160)
        unrelated = candidate(title="Sun Heat", channel="Release - Topic", duration=151)
        extract.side_effect = [{"entries": [unrelated]}, {"entries": []}, {"entries": [expected]}]
        self.assertEqual(spotify.search_youtube("In The Sun", "Low Heat", 160000), expected["url"])
        self.assertEqual(extract.call_args.args[0], 'ytsearch10:"In The Sun" "Low Heat"')
        match.assert_not_called()

    @patch.object(spotify, "match_by_cover")
    @patch.object(spotify.yt_dlp, "YoutubeDL")
    def test_no_spotify_cover_still_uses_quoted_search(self, ydl, match):
        extract = ydl.return_value.__enter__.return_value.extract_info
        extract.return_value = {"entries": [candidate()]}
        self.assertIsNone(spotify.search_youtube("Afterhours Drive", "Low Heat", 131000))
        self.assertEqual(extract.call_count, 3)
        match.assert_not_called()

    @patch.object(spotify.yt_dlp, "YoutubeDL")
    def test_search_failure_is_skipped(self, ydl):
        ydl.return_value.__enter__.return_value.extract_info.side_effect = spotify.yt_dlp.utils.DownloadError("offline")
        self.assertIsNone(spotify.search_youtube("Afterhours Drive", "Low Heat", 131000, "cover"))


class SpotifyArtworkTests(unittest.TestCase):
    @patch.object(spotify, "SpotifyOAuth")
    @patch.object(spotify.spotipy, "Spotify")
    def test_each_song_uses_its_album_image_not_playlist_image(self, client, oauth):
        sp = client.return_value
        sp.playlist.return_value = {"name": "My Playlist", "images": [{"url": "playlist-art"}]}
        def song(name, images):
            return {"name": name, "artists": [{"name": "Low Heat"}], "duration_ms": 160000,
                    "album": {"name": name, "images": images}}
        sp.playlist_items.return_value = {
            "items": [
                {"track": song("In The Sun", [{"url": "orange-single-art"}])},
                {"item": song("Afterhours Drive", [{"url": "car-single-art"}])},
                {"track": song("No Art", [])},
            ], "next": None}
        _, tracks = spotify.get_spotify_tracks("https://open.spotify.com/playlist/example")
        self.assertEqual([t["cover_url"] for t in tracks], ["orange-single-art", "car-single-art", None])


if __name__ == "__main__":
    unittest.main()
