"""Offline regression checks for the local/origin integration."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import music
import utils

spec = importlib.util.spec_from_file_location(
    "instagram_download", Path(__file__).with_name("instagram-to-mp3.py")
)
instagram = importlib.util.module_from_spec(spec)
spec.loader.exec_module(instagram)


class IntegrationTests(unittest.TestCase):
    def test_all_platforms_use_active_python(self):
        cases = [
            ("https://www.instagram.com/reel/example/", None, "instagram-to-mp3.py"),
            ("https://open.spotify.com/playlist/example", None, "spotify-to-mp3.py"),
            ("https://artist.bandcamp.com/album/example", None, "bandcamp-to-mp3.py"),
            ("https://soundcloud.com/artist/song", None, "soundcloud-to-mp3.py"),
            ("https://youtu.be/example", "1", "yt-to-mp3.py"),
            ("https://youtu.be/example", "2", "shazam_pipeline.py"),
            ("https://youtu.be/example", "3", "yt-to-spotify.py"),
        ]
        for url, choice, script in cases:
            with self.subTest(url=url, choice=choice):
                command = music.build_command(url, choice)
                self.assertEqual(command[0], sys.executable)
                self.assertEqual(Path(command[1]).name, script)
                self.assertIn(url, command)

    def test_instagram_hostname_boundary(self):
        for url in ("https://instagram.com.example.org/reel/x",
                    "https://example.org/?url=instagram.com", "https://[invalid"):
            self.assertIsNone(music.detect_platform(url))

    def test_output_is_under_home_music(self):
        with tempfile.TemporaryDirectory() as home:
            with patch.object(utils.Path, "home", return_value=Path(home)):
                output = Path(utils.make_output_dir())
                self.assertEqual(output.parent, Path(home) / "Music")
                self.assertTrue(output.is_dir())
                self.assertEqual(utils.make_output_dir(), str(output))

    @patch.object(instagram.yt_dlp, "YoutubeDL")
    def test_instagram_success_and_failure(self, ydl):
        download = ydl.return_value.__enter__.return_value.download
        download.return_value = 0
        self.assertTrue(instagram.download_one("https://instagram.com/reel/x", "/tmp"))
        download.return_value = 1
        self.assertFalse(instagram.download_one("https://instagram.com/reel/x", "/tmp"))
        download.side_effect = instagram.yt_dlp.utils.DownloadError("offline")
        self.assertFalse(instagram.download_one("https://instagram.com/reel/x", "/tmp"))


if __name__ == "__main__":
    unittest.main()
