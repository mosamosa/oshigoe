import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.backend.media.media_input import SourceError
from src.backend.settings import VideoQuality
from src.backend.sources.registry import normalize_url, resolve_source


class TwitchTest(unittest.IsolatedAsyncioTestCase):
    def test_normalizes_live_and_archive_urls(self):
        for original, path in (
            ("https://m.twitch.tv/Example_Channel/?tt_content=share", "example_channel"),
            ("https://twitch.tv/videos/123456789?t=1h#share", "videos/123456789"),
        ):
            with self.subTest(original=original):
                self.assertEqual(normalize_url(original), f"https://www.twitch.tv/{path}")

    async def test_rejects_invalid_urls_before_invoking_extractor(self):
        with patch("src.backend.sources.yt_dlp_input.asyncio.create_subprocess_exec") as launch:
            for url in (
                "http://twitch.tv/example",
                "https://twitch.tv.example.test/example",
                "https://user@twitch.tv/example",
                "https://twitch.tv:443/example",
                "https://twitch.tv/directory",
                "https://twitch.tv/videos",
                "https://twitch.tv/videos/a123",
                "https://twitch.tv/example/videos",
            ):
                with self.subTest(url=url), self.assertRaises(SourceError):
                    await resolve_source(url, bool)
            launch.assert_not_called()

    async def test_resolves_twitch_hls_using_shared_quality_and_login_settings(self):
        formats = [
            {"protocol": "m3u8_native", "vcodec": "avc1.640028", "acodec": "mp4a.40.2",
             "height": height, "fps": 30, "url": f"https://example.test/{height}.m3u8"}
            for height in (1080, 720)
        ]
        for path, live in (("example", True), ("videos/123456789", False)):
            info = {"formats": formats, "is_live": live, "duration": 120}
            process = Mock(returncode=0, communicate=AsyncMock(return_value=(json.dumps(info).encode(), None)))
            with self.subTest(path=path), patch("src.backend.sources.yt_dlp_input.asyncio.create_subprocess_exec",
                                               return_value=process) as launch:
                source = await resolve_source(f"https://m.twitch.tv/{path}?share=1", bool,
                                              quality=VideoQuality(height=720), use_browser_login=True)
                self.assertEqual(launch.await_args.args[-4:],
                                 ("--cookies-from-browser", "firefox", "--", f"https://www.twitch.tv/{path}"))
                self.assertEqual(source.title, "Twitch")
                self.assertEqual(source.video.url, "https://example.test/720.m3u8")
                self.assertIs(source.metadata.is_live, live)
                self.assertEqual(source.metadata.duration, None if live else 120)
