import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from src.backend.settings import PlaybackSettings, VideoQuality
from src.backend.media.video_encoding import HARDWARE_ENCODINGS, VIDEO_ENCODINGS
from src.backend.media.media_input import Input, Source, SourceError, ffmpeg_command, ffmpeg_keyframe_command
from src.backend.sources.registry import normalize_url, resolve_source
from src.backend.sources.yt_dlp_input import choose_source


class SourceUrlTest(unittest.TestCase):
    def test_normalizes_shared_source_url_cases(self):
        cases = Path(__file__).resolve().parents[1] / "source-urls.json"
        for case in json.loads(cases.read_text(encoding="utf-8")):
            with self.subTest(url=case["url"]):
                expected = case["backend"]
                if expected is None:
                    with self.assertRaises(SourceError):
                        normalize_url(case["url"])
                else:
                    self.assertEqual(normalize_url(case["url"]), expected)

    def test_normalizes_supported_youtube_urls(self):
        for url in (
            "https://youtu.be/aqz-KE-bpKQ?si=test",
            "https://m.youtube.com/shorts/aqz-KE-bpKQ",
            "https://www.youtube.com/live/aqz-KE-bpKQ#test",
            "https://youtube.com/watch?v=aqz-KE-bpKQ&t=30",
        ):
            with self.subTest(url=url):
                self.assertEqual(normalize_url(url), "https://www.youtube.com/watch?v=aqz-KE-bpKQ")

    def test_rejects_unsupported_hosts_and_invalid_youtube_urls(self):
        for url in (
            "https://example.test/watch?v=aqz-KE-bpKQ",
            "https://youtube.com.example.test/watch?v=aqz-KE-bpKQ",
            "https://user@youtu.be/aqz-KE-bpKQ",
            "https://youtu.be:443/aqz-KE-bpKQ",
            "http://youtu.be/aqz-KE-bpKQ",
            "https://youtu.be/invalid",
        ):
            with self.subTest(url=url), self.assertRaises(SourceError):
                normalize_url(url)


class ChooseSourceTest(unittest.TestCase):
    def test_pairs_video_with_hls_audio_whose_codec_is_unspecified(self):
        video = {
            "url": "https://example.test/video.m3u8",
            "protocol": "m3u8_native",
            "vcodec": "avc1.4D4020",
            "acodec": "none",
            "height": 720,
        }
        audio = {
            "url": "https://example.test/audio.m3u8",
            "protocol": "m3u8_native",
            "vcodec": "none",
            "acodec": None,
        }
        info = {"is_live": True, "duration": 120, "formats": [video, audio]}
        encoding = VIDEO_ENCODINGS[0]

        source = choose_source(info, lambda codec: codec == encoding.codec, (encoding,))

        self.assertEqual(source.video.url, video["url"])
        self.assertEqual(source.audio.url, audio["url"])
        self.assertEqual(source.output_codec, encoding.codec)
        self.assertTrue(source.metadata.is_live)
        self.assertEqual(source.metadata.height, 720)
        self.assertIsNone(source.metadata.audio_codec)
        self.assertIsNone(source.metadata.duration)

    def test_status_metadata_comes_from_the_selected_audio_and_video_formats(self):
        video = {"url": "https://example.test/video.webm", "protocol": "https", "vcodec": "vp8",
                 "acodec": "none", "width": 640, "height": 360, "fps": 29.97}
        audio = {"url": "https://example.test/audio.m4a", "protocol": "https", "vcodec": "none",
                 "acodec": "mp4a.40.2", "asr": 44100}
        info = {"duration": 120.5, "formats": [video, audio]}
        source = choose_source(info, lambda codec: codec == "vp8")
        self.assertEqual((source.metadata.width, source.metadata.height, source.metadata.fps), (640, 360, 29.97))
        self.assertEqual((source.metadata.audio_codec, source.metadata.audio_sample_rate), ("mp4a.40.2", 44100))
        self.assertEqual(source.metadata.duration, 120.5)

    def test_copies_best_compatible_video_within_both_limits(self):
        formats = [
            {"url": f"https://example.test/{height}-{fps}.webm", "protocol": "https",
             "vcodec": "vp8", "acodec": "opus", "height": height, "fps": fps}
            for height, fps in [(480, 24), (480, 60), (720, 24)]
        ]
        source = choose_source({"formats": formats}, lambda codec: codec == "vp8",
                               (VIDEO_ENCODINGS[1],), VideoQuality(height=480, fps=30))
        self.assertEqual(source.video.url, formats[0]["url"])
        self.assertIsNone(source.video_encoding)

    def test_picks_highest_input_within_limit_and_prefers_copy_on_a_tie(self):
        formats = [
            {"url": "https://example.test/720.webm", "protocol": "https",
             "vcodec": "vp8", "acodec": "opus", "height": 720, "fps": 30},
            {"url": "https://example.test/1080.webm", "protocol": "https",
             "vcodec": "vp9", "acodec": "opus", "height": 1080, "fps": 30},
        ]
        info = {"formats": formats}
        hardware = HARDWARE_ENCODINGS["darwin"][0]
        formats.append({**formats[0], "url": "https://example.test/720.vp9", "vcodec": "vp9"})
        for height, encodings, expected_format, expected_encoder in (
            (1080, (hardware, *VIDEO_ENCODINGS), formats[1], hardware),
            (0, (hardware, *VIDEO_ENCODINGS), formats[1], hardware),
            (720, (hardware, *VIDEO_ENCODINGS), formats[0], None),
            (1080, (hardware,), formats[1], hardware),
        ):
            with self.subTest(height=height, encodings=encodings):
                source = choose_source(info, lambda codec: codec in {"vp8", hardware.codec},
                                       encodings, VideoQuality(height=height))
                self.assertEqual(source.video.url, expected_format["url"])
                self.assertEqual(source.video_encoding, expected_encoder)

    def test_default_height_prefers_1080p_and_downscales_larger_input(self):
        formats = [
            {"url": f"https://example.test/{height}.mp4", "protocol": "https",
             "vcodec": "avc1.42c01f", "acodec": "aac", "height": height}
            for height in (480, 1080, 2160)
        ]
        hardware = HARDWARE_ENCODINGS["darwin"][0]

        source = choose_source({"formats": formats}, bool, (hardware, *VIDEO_ENCODINGS))
        self.assertEqual(source.video.url, formats[1]["url"])
        self.assertIsNone(source.video_encoding)

        source = choose_source({"formats": formats[2:]}, bool, (hardware, *VIDEO_ENCODINGS))
        self.assertEqual(source.video.url, formats[2]["url"])
        self.assertEqual(source.video_encoding, hardware)
        command = ffmpeg_command(source)
        self.assertIn("h='min(ih,if(lt(iw,ih),1920,1080))'", command[command.index("-vf") + 1])

    def test_portrait_input_and_copy_use_width_for_the_resolution_limit(self):
        formats = [
            {"url": f"https://example.test/{width}.mp4", "protocol": "https",
             "vcodec": "avc1.42c01f", "acodec": "aac", "width": width, "height": height}
            for width, height in ((540, 960), (720, 1280), (1080, 1920), (1440, 2560))
        ]
        hardware = HARDWARE_ENCODINGS["darwin"][0]
        for limit, candidates, expected, encoding in (
            (720, formats, formats[1], None),
            (1080, formats, formats[2], None),
            (0, formats, formats[3], None),
            (1080, formats[3:], formats[3], hardware),
        ):
            with self.subTest(limit=limit, candidates=candidates):
                source = choose_source({"formats": candidates}, bool, (hardware,), VideoQuality(height=limit))
                self.assertEqual(source.video.url, expected["url"])
                self.assertEqual(source.video_encoding, encoding)

    def test_quality_message_defaults_missing_height_and_preserves_explicit_height(self):
        for quality, expected_height in (({}, 1080), ({"height": 0}, 0), ({"height": 720}, 720)):
            with self.subTest(quality=quality):
                settings = PlaybackSettings.from_message({"quality": quality})
                self.assertEqual(settings.video.height, expected_height)

    def test_quality_message_rejects_null_height(self):
        with self.assertRaisesRegex(ValueError, "Choose a resolution limit"):
            PlaybackSettings.from_message({"quality": {"height": None}})

    def test_uses_the_nearest_larger_input_for_downscaling_only_if_no_input_is_in_range(self):
        formats = [
            {"url": f"https://example.test/{height}.webm", "protocol": "https",
             "vcodec": "vp9", "acodec": "opus", "height": height, "fps": 30}
            for height in [360, 1080, 2160]
        ]
        encoding = VIDEO_ENCODINGS[1]
        for candidates, expected_height in ((formats, 360), (formats[1:], 1080)):
            with self.subTest(expected_height=expected_height):
                source = choose_source({"formats": candidates}, lambda codec: codec == "vp8",
                                       (encoding,), VideoQuality(height=720))
                self.assertEqual(source.metadata.height, expected_height)
                self.assertEqual(source.video_encoding, encoding)

    def test_explicit_bitrate_reencodes_even_compatible_video(self):
        info = {"formats": [{"url": "https://example.test/video.webm", "protocol": "https",
                             "vcodec": "vp8", "acodec": "opus", "height": 480, "fps": 30}]}
        quality = VideoQuality(bitrate_kbps=500)
        encoding = VIDEO_ENCODINGS[1]
        source = choose_source(info, lambda codec: codec == "vp8", (encoding,), quality)
        self.assertEqual(source.video_encoding, encoding)
        self.assertEqual(source.quality.bitrate_kbps, 500)

    def test_retry_prefers_the_other_protocol_when_it_is_available(self):
        direct = {"url": "https://example.test/video.mp4", "protocol": "https",
                  "vcodec": "avc1.42c01e", "acodec": "aac", "height": 720}
        hls = {**direct, "url": "https://example.test/video.m3u8", "protocol": "m3u8_native", "height": 480}
        info = {"formats": [direct, hls]}
        quality = VideoQuality(height=720)
        self.assertEqual(choose_source(info, bool, quality=quality).video.url, direct["url"])
        self.assertEqual(choose_source(info, bool, quality=quality, prefer_hls=True).video.url, hls["url"])
        self.assertEqual(choose_source({"formats": [direct]}, bool, prefer_hls=True).video.url, direct["url"])


class BrowserLoginTest(unittest.IsolatedAsyncioTestCase):
    def test_browser_login_is_off_by_default_and_requires_a_boolean(self):
        self.assertFalse(PlaybackSettings.from_message({}).use_browser_login)
        self.assertTrue(PlaybackSettings.from_message({"use_browser_login": True}).use_browser_login)
        for invalid in (None, 1, "true", {}):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, "Firefox sign-in"):
                PlaybackSettings.from_message({"use_browser_login": invalid})

    async def test_uses_firefox_for_extraction_only_when_enabled(self):
        video_format = {"url": "https://example.test/video.mp4", "protocol": "https",
                        "vcodec": "avc1.42c01f", "acodec": "aac", "height": 720,
                        "cookies": "sid=scoped-fixture; Domain=example.test; Path=/",
                        "http_headers": {"Cookie": "sid=raw-fixture", "User-Agent": "fixture-agent"}}
        output = json.dumps({"formats": [video_format]}).encode()
        for enabled in (False, True):
            process = Mock(returncode=0, communicate=AsyncMock(return_value=(output, None)))
            with self.subTest(enabled=enabled), patch("src.backend.sources.yt_dlp_input.asyncio.create_subprocess_exec",
                                                      return_value=process) as launch:
                source = await resolve_source("https://youtu.be/aqz-KE-bpKQ", bool, use_browser_login=enabled)
                args = launch.await_args.args
                if enabled:
                    option = args.index("--cookies-from-browser")
                    self.assertEqual(args[option + 1], "firefox")
                else:
                    self.assertNotIn("--cookies-from-browser", args)
                command = ffmpeg_command(source)
                headers = command[command.index("-headers") + 1]
                self.assertEqual(headers, "User-Agent: fixture-agent\r\n")
                self.assertFalse(any("-fixture" in arg for arg in command))

    async def test_reports_sanitized_firefox_extraction_failure(self):
        process = Mock(returncode=1, communicate=AsyncMock(return_value=(b"synthetic-account-secret", None)))
        with patch("src.backend.sources.yt_dlp_input.asyncio.create_subprocess_exec", return_value=process):
            with self.assertRaises(SourceError) as failure:
                await resolve_source("https://youtu.be/aqz-KE-bpKQ", bool, use_browser_login=True)
        self.assertEqual(failure.exception.message["key"], "firefox_lookup_failed")
        self.assertNotIn("synthetic-account-secret", str(failure.exception))


class FfmpegCommandTest(unittest.TestCase):
    def test_h264_stream_copy_repeats_decoder_configuration_at_keyframes(self):
        for codec in ("avc1.42c01e", "avc3.42c01e"):
            with self.subTest(codec=codec):
                source = Source("H.264 stream", codec, Input("video.nut"))
                command = ffmpeg_command(source)
                self.assertEqual(command[command.index("-c:v") + 1], "copy")
                self.assertEqual(command[command.index("-bsf:v") + 1],
                                 "h264_mp4toannexb,dump_extra=freq=keyframe")

    def test_direct_http_inputs_seek_with_a_new_range_request(self):
        video = Input("https://example.test/video.mp4", "https")
        audio = Input("https://example.test/audio.m4a", "https")
        source = Source("Direct", "avc1.42c01e", video, audio)

        def input_options(command, media):
            end = command.index(media.url)
            start = max((index for index, arg in enumerate(command[:end - 1]) if arg == "-i"), default=0)
            return command[start:end]

        for command, inputs in ((ffmpeg_command(source, 120.0), (video, audio)),
                                (ffmpeg_keyframe_command(source, 120.0), (video,))):
            for media in inputs:
                with self.subTest(url=media.url, keyframe="-frames:v" in command):
                    options = input_options(command, media)
                    self.assertEqual(options[options.index("-short_seek_size") + 1], "32768")

        for media in (Input("video.nut"), Input("https://example.test/video.m3u8", "m3u8_native")):
            with self.subTest(protocol=media.protocol):
                self.assertNotIn("-short_seek_size", ffmpeg_command(Source("Other", "avc1.42c01e", media), 120.0))

    def test_hls_h264_stream_copy_fills_missing_presentation_timestamps(self):
        source = Source("HLS stream", "avc1.4d401f", Input("video.m3u8", "m3u8_native"))
        command = ffmpeg_command(source)

        self.assertEqual(command[command.index("-c:v") + 1], "copy")
        self.assertEqual(command[command.index("-bsf:v") + 1],
                         r"h264_mp4toannexb,dump_extra=freq=keyframe,setts=pts=if(eq(PTS\,NOPTS)\,DTS\,PTS)")
