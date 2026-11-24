import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aiortc.codecs import CODECS

from src.backend.broadcast import prepare_media, stream_status
from src.backend.media.pipeline import FFmpegInputError, VideoReorderingError
from src.backend.rtc_negotiation import parse_offer
from src.backend.server import create_app
from src.backend.server_messages import Message
from src.backend.settings import PlaybackSettings, VideoQuality
from src.backend.media.video_encoding import (HARDWARE_ENCODINGS, VIDEO_ENCODINGS, available_video_encodings, encoded_short_edge,
                            h264_keyframe_codec, select_encoder, video_encode_args)
from src.backend.media.media_input import Input, InputMetadata, RetryableInputError, Source, SourceError, ffmpeg_command


class EncoderSelectionTest(unittest.TestCase):
    def test_windows_prefers_quick_sync_and_uses_the_next_available_encoder(self):
        hardware = ("h264_qsv", "h264_nvenc", "h264_amf")
        order = (*hardware, "libx264", "libvpx")
        listed = SimpleNamespace(stdout="\n".join(f" V..... {name}" for name in reversed(order)))
        for skipped in range(4):
            failures = hardware[:skipped]
            with (
                self.subTest(failures=failures),
                patch("src.backend.media.video_encoding.sys", SimpleNamespace(platform="win32")),
                patch("src.backend.media.video_encoding.subprocess.run", return_value=listed),
                patch("src.backend.media.video_encoding.probe_hardware",
                      side_effect=lambda entry: None if entry.encoder in failures else entry),
            ):
                encodings = available_video_encodings()
                self.assertEqual(tuple(entry.encoder for entry in encodings), order[skipped:])
                self.assertEqual(select_encoder(encodings, bool, VideoQuality()).encoder, order[skipped])
                quality = VideoQuality(height=1080, fps=60)
                expected = hardware[skipped] if skipped < len(hardware) else "libvpx"
                self.assertEqual(select_encoder(encodings, bool, quality, height=480).encoder, expected)
                self.assertEqual(select_encoder(encodings, bool, quality, height=1080).encoder, expected)

    def test_hardware_respects_1080p60_and_unlimited_quality_settings(self):
        for encoders in HARDWARE_ENCODINGS.values():
            for hardware in encoders:
                with self.subTest(encoder=hardware.encoder):
                    capped = video_encode_args(hardware, VideoQuality(height=1080, fps=60))
                    scale = capped[capped.index("-vf") + 1]
                    self.assertIn("w='min(iw,if(lt(iw,ih),1080,1920))'", scale)
                    self.assertIn("h='min(ih,if(lt(iw,ih),1920,1080))'", scale)
                    self.assertEqual(capped[capped.index("-fpsmax") + 1], "60")
                    self.assertNotIn("-level:v", capped)
                    unlimited = video_encode_args(hardware, VideoQuality(height=0, fps=0))
                    self.assertIn("scale=w=trunc(iw/2)*2:h=trunc(ih/2)*2", unlimited[unlimited.index("-vf") + 1])
                    self.assertNotIn("-fpsmax", unlimited)

    def test_considers_the_scaling_box_in_both_orientations(self):
        quality = VideoQuality(height=720)
        hardware = HARDWARE_ENCODINGS["darwin"][0]
        encodings = (hardware, *VIDEO_ENCODINGS)

        for width, height in ((1440, 720), (720, 1440)):
            with self.subTest(width=width, height=height):
                self.assertEqual(encoded_short_edge(hardware, quality, width=width, height=height), 640)
                self.assertEqual(select_encoder(encodings, bool, quality, width=width, height=height), hardware)

    def test_uses_software_when_the_browser_does_not_accept_the_hardware_codec(self):
        hardware = HARDWARE_ENCODINGS["darwin"][0]
        encodings = (hardware, *VIDEO_ENCODINGS)

        selected = select_encoder(encodings, lambda codec: codec == "vp8", VideoQuality())

        self.assertEqual(selected, VIDEO_ENCODINGS[1])

    def test_mac_uses_cpu_when_videotoolbox_probe_fails(self):
        listed = SimpleNamespace(stdout=" V..... h264_videotoolbox\n V..... libx264\n")
        with (
            patch("src.backend.media.video_encoding.sys", SimpleNamespace(platform="darwin")),
            patch("src.backend.media.video_encoding.subprocess.run", return_value=listed),
            patch("src.backend.media.video_encoding.probe_hardware", return_value=None),
        ):
            self.assertEqual(available_video_encodings(), (VIDEO_ENCODINGS[0],))

    def test_h264_keyframe_requires_parameter_sets_and_idr(self):
        prefix = b"\x00\x00\x00\x01"
        sps = prefix + b"\x67\x42\xc0\x1f"
        pps = prefix + b"\x68\xca"
        idr = prefix + b"\x65\xb4"
        self.assertEqual(h264_keyframe_codec(sps + pps + idr), "avc1.42c01f")
        self.assertIsNone(h264_keyframe_codec(sps + idr))


class VideoBitrateTest(unittest.TestCase):
    def test_bitrate_reflects_output_size_and_frame_rate(self):
        cases = (
            ("1080p30", VideoQuality(fps=60), InputMetadata(width=1920, height=1080, fps=30), 8000),
            ("portrait 1080p30", VideoQuality(fps=60), InputMetadata(width=1080, height=1920, fps=30), 8000),
            ("1080p60", VideoQuality(fps=60), InputMetadata(width=1920, height=1080, fps=59.94), 12000),
            ("fps cap", VideoQuality(fps=30), InputMetadata(width=1920, height=1080, fps=60), 8000),
            ("smaller input", VideoQuality(fps=60), InputMetadata(width=854, height=480, fps=60), 4000),
            ("wide input", VideoQuality(height=720), InputMetadata(width=1920, height=720, fps=30), 2500),
            ("known caps", VideoQuality(height=720, fps=60), InputMetadata(), 7500),
            ("unlimited fallback", VideoQuality(height=0, fps=0), InputMetadata(), 8000),
        )
        for caption, quality, metadata, expected in cases:
            with self.subTest(caption=caption):
                self.assert_target(VIDEO_ENCODINGS[1], quality, metadata, expected, auto=True)

    def test_encoder_limits_apply_only_to_auto_bitrate(self):
        metadata = InputMetadata(width=1920, height=1080, fps=60)
        self.assert_target(VIDEO_ENCODINGS[0], VideoQuality(fps=60), metadata, 5000, auto=True)
        self.assert_target(VIDEO_ENCODINGS[0], VideoQuality(fps=60, bitrate_kbps=2300), metadata, 2300, auto=False)

    def test_copy_has_no_target_bitrate(self):
        self.assert_target(None, VideoQuality(), InputMetadata(width=1920, height=1080, fps=60), None, auto=False)

    def assert_target(self, encoding, quality, metadata, expected, *, auto):
        source = Source("clip", "vp8", Input("clip.mkv"), video_encoding=encoding, quality=quality, metadata=metadata)
        settings = PlaybackSettings(video=quality)
        command = ffmpeg_command(source)
        video = stream_status(source, None, settings, [])["video"]
        if expected is None:
            self.assertEqual(command[command.index("-c:v") + 1], "copy")
            self.assertNotIn("-b:v", command)
        else:
            self.assertEqual(command[command.index("-b:v") + 1], f"{expected}k")
            self.assertEqual(command[command.index("-maxrate") + 1], f"{expected}k")
        self.assertEqual(video["target_kbps"], expected)
        self.assertIs(video["auto_bitrate"], auto)


class H264OfferTest(unittest.TestCase):
    def test_creating_another_app_keeps_the_registered_profiles_and_payload_types(self):
        extra_profiles = {"4d001f", "64001f", "640c1f"}
        original_codecs = [codec for codec in CODECS["video"]
                           if codec.parameters.get("profile-level-id") not in extra_profiles]
        with (patch.dict(CODECS, {"video": original_codecs}),
              patch("src.backend.server.available_video_encodings", return_value=())):
            create_app(None)
            first = [(codec.parameters.get("profile-level-id"), codec.payloadType) for codec in CODECS["video"]]
            create_app(None)
            second = [(codec.parameters.get("profile-level-id"), codec.payloadType) for codec in CODECS["video"]]

        self.assertEqual(second, first)
        self.assertCountEqual([profile for profile, _ in first if profile in extra_profiles], extra_profiles)
        self.assertEqual(len({payload for _, payload in first}), len(first))

    def test_receive_level_extension_requires_asymmetry_and_matching_profile(self):
        base = (
            "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n"
            "m=audio 9 UDP/TLS/RTP/SAVPF 96\r\na=recvonly\r\na=rtpmap:96 opus/48000/2\r\n"
            "m=video 9 UDP/TLS/RTP/SAVPF 97\r\na=recvonly\r\na=rtpmap:97 H264/90000\r\n"
            "a=fmtp:97 packetization-mode=1;profile-level-id=42e01f"
        )
        for parameters, accepts_high_level in (
            (";level-asymmetry-allowed=1;max-recv-level=e02a", True),
            (";level-asymmetry-allowed=1", False),
            (";level-asymmetry-allowed=0;max-recv-level=e02a", False),
            (";level-asymmetry-allowed=1;max-recv-level=002a", False),
            (";level-asymmetry-allowed=1;max-recv-level=unknown", False),
        ):
            with self.subTest(parameters=parameters):
                codec_for = parse_offer(base + parameters + "\r\n")
                self.assertIsNotNone(codec_for("avc1.42c01f"))
                self.assertEqual(bool(codec_for("avc1.42c02a")), accepts_high_level)


class EncoderStartupTest(unittest.IsolatedAsyncioTestCase):
    async def test_reordered_h264_copy_retries_same_input_with_hardware_then_cpu(self):
        hardware = HARDWARE_ENCODINGS["darwin"][0]
        software = VIDEO_ENCODINGS[0]
        source = Source("clip", "avc1.4d401f",
                        Input("https://example.test/video", "https"),
                        Input("https://example.test/audio", "https"))
        copy_error = VideoReorderingError()
        hardware_error = SourceError("encoder unavailable")
        attempts = []

        def create_media(attempt_source, isolator, settings, *, codec_for):
            failures = {None: copy_error, hardware: hardware_error}
            failure = failures.get(attempt_source.video_encoding)
            media = Mock(source=attempt_source, start=AsyncMock(), wait_ready=AsyncMock(side_effect=failure),
                         close=AsyncMock())
            media.finished.done.return_value = failure is not None
            attempts.append(media)
            return media

        with patch("src.backend.broadcast.ProcessedMedia", side_effect=create_media), self.assertLogs("src.backend.server", level="WARNING"):
            media, fallbacks = await prepare_media(source, Mock(), PlaybackSettings(), (hardware, software), bool)

        self.assertIs(media, attempts[-1])
        self.assertEqual([attempt.source.video_encoding for attempt in attempts], [None, hardware, software])
        for attempt in attempts:
            self.assertIs(attempt.source.video, source.video)
            self.assertIs(attempt.source.audio, source.audio)
        for attempt in attempts[:-1]:
            attempt.close.assert_awaited_once()
        self.assertEqual(fallbacks, [
            {"from": "copy", "to": hardware.encoder, "reason": Message.VIDEO_REORDERING.payload()},
            {"from": hardware.encoder, "to": software.encoder, "reason": {"text": str(hardware_error)}},
        ])

    async def test_software_startup_failure_can_fall_back_to_hardware(self):
        hardware = HARDWARE_ENCODINGS["darwin"][0]
        software = VIDEO_ENCODINGS[1]
        video = Input("https://example.test/video", "https")
        quality = VideoQuality(height=1080)
        source = Source("clip", "vp9", video, video_encoding=software, quality=quality,
                        metadata=InputMetadata(width=1920, height=1080))
        error = SourceError("encoder unavailable")
        failed = Mock(start=AsyncMock(), wait_ready=AsyncMock(side_effect=error), close=AsyncMock())
        failed.finished.done.return_value = True
        ready = Mock(start=AsyncMock(), wait_ready=AsyncMock(), close=AsyncMock())

        with patch("src.backend.broadcast.ProcessedMedia", side_effect=(failed, ready)) as create_media:
            with self.assertLogs("src.backend.server", level="WARNING"):
                media, fallbacks = await prepare_media(source, Mock(), PlaybackSettings(video=quality),
                                                       (hardware, *VIDEO_ENCODINGS), bool)

        self.assertIs(media, ready)
        retry_source = create_media.call_args.args[0]
        self.assertIs(retry_source.video_encoding, hardware)
        self.assertIs(retry_source.video, video)
        failed.close.assert_awaited_once()
        self.assertEqual(fallbacks, [{"from": software.encoder, "to": hardware.encoder, "reason": {"text": str(error)}}])

    async def test_input_failure_does_not_try_another_encoder(self):
        hardware = HARDWARE_ENCODINGS["darwin"][0]
        source = Source("clip", "vp9", Input("https://example.test/video", "https"), video_encoding=hardware)
        for error in (FFmpegInputError(403), RetryableInputError("network timeout")):
            with self.subTest(error=error):
                media = Mock(start=AsyncMock(), wait_ready=AsyncMock(side_effect=error), close=AsyncMock())
                media.finished.done.return_value = True
                with patch("src.backend.broadcast.ProcessedMedia", return_value=media) as create_media:
                    with self.assertRaises(type(error)):
                        await prepare_media(source, Mock(), PlaybackSettings(), (hardware, *VIDEO_ENCODINGS), bool)
                self.assertEqual(create_media.call_count, 1)
                media.close.assert_awaited_once()

    async def test_hardware_failure_before_first_video_retries_with_cpu(self):
        hardware = HARDWARE_ENCODINGS["darwin"][0]
        source = Source("clip", "vp9", Input("https://example.test/video"), video_encoding=hardware)
        attempts = []

        class MediaAttempt:
            def __init__(self, source, isolator, settings, *, codec_for):
                self.source = source
                self.ready = asyncio.Event()
                self.finished = asyncio.get_running_loop().create_future()
                self.closed = False
                attempts.append(self)

            async def start(self):
                if self.source.video_encoding.hardware:
                    self.finished.set_result("encoder unavailable")
                else:
                    self.ready.set()

            async def wait_ready(self, event):
                if not event.is_set():
                    raise SourceError(self.finished.result())

            async def close(self):
                self.closed = True

        with patch("src.backend.broadcast.ProcessedMedia", MediaAttempt), self.assertLogs("src.backend.server", level="WARNING"):
            media, fallbacks = await prepare_media(source, Mock(), PlaybackSettings(), (hardware, *VIDEO_ENCODINGS), bool)

        self.assertEqual(media.source.video_encoding, VIDEO_ENCODINGS[0])
        self.assertEqual(media.source.video, source.video)
        self.assertTrue(attempts[0].closed)
        status = stream_status(media.source, SimpleNamespace(device="mps"), PlaybackSettings(), fallbacks)
        self.assertEqual(status["video"]["encoder"], "libx264")
        self.assertFalse(status["video"]["hardware"])
        self.assertEqual(status["video"]["fallbacks"], [{
            "from": "h264_videotoolbox", "to": "libx264", "reason": {"text": "encoder unavailable"},
        }])
        self.assertEqual(status["audio"]["device"], "mps")
        self.assertNotIn(source.video.url, str(status))
