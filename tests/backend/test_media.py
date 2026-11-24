import asyncio
import os
import platform
import shutil
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import av
import numpy as np
from aiortc.mediastreams import MediaStreamError

from src.backend.bootstrap_runtime import TOOLS, tool_directory
from src.backend.media.audio_isolation import PACKET_SAMPLES, AudioIsolation, IsolationUnavailable, _receive, _send
from src.backend.media.pipeline import AUDIO_TIME_BASE, OPUS_FRAME, FFmpegInputError, IsolatedAudio, MediaRelay, MediaSubscription, ProcessedMedia
from src.backend.settings import Mix, PlaybackSettings
from src.backend.media.video_encoding import HARDWARE_ENCODINGS
from src.backend.media.media_input import Input, RetryableInputError, Source, SourceError


def ffmpeg_directory():
    if found := shutil.which("ffmpeg"):
        return str(Path(found).parent)
    for tool in TOOLS.get((platform.system(), platform.machine().lower()), ()):
        if tool.name == "FFmpeg" and (tool_directory(tool) / Path(tool.member).name).is_file():
            return str(tool_directory(tool))
    return None


FFMPEG_DIRECTORY = ffmpeg_directory()


class DelayedIsolator:
    def __init__(self):
        self.pending = np.empty(0, dtype=np.float32)

    def stream(self):
        return self

    def push(self, samples):
        available = self.pending * 0.25
        self.pending = samples
        return available

    def flush(self, *, as_numpy):
        return self.pending * 0.25


ECHO_GPU_MEMORY = 4096


def echo_isolation_worker(connection, model, device):
    with connection:
        _send(connection, b"R", b"cpu")
        try:
            while True:
                kind, samples = _receive(connection)
                if kind != b"F":
                    first_sample = np.frombuffer(samples, dtype="<f4")[0]
                    if first_sample == -1:
                        time.sleep(10)
                    elif first_sample == -2:
                        time.sleep(0.18)
                _send(connection, b"A", ECHO_GPU_MEMORY.to_bytes(8, "big") + samples)
        except (EOFError, OSError):
            pass


class AudioIsolationTest(unittest.TestCase):
    def wait_for_audio(self, stream, audio):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                return stream.push(audio)
            except IsolationUnavailable:
                time.sleep(0.02)
        self.fail("Voice Isolator worker did not produce audio")

    def test_slow_inference_within_the_packet_duration_keeps_the_worker(self):
        isolation = AudioIsolation("fixture", None, worker_target=echo_isolation_worker)
        self.addCleanup(isolation.close)
        stream = isolation.stream()
        fast = np.full(PACKET_SAMPLES, 0.5, dtype="<f4")
        slow = np.full(PACKET_SAMPLES, -2, dtype="<f4")

        self.wait_for_audio(stream, fast)
        original_pid = isolation.worker.process.pid
        self.assertLess(isolation.processing_load, 1)

        np.testing.assert_array_equal(stream.push(slow), slow)
        self.assertEqual(isolation.mode, "isolated")
        self.assertEqual(isolation.worker.process.pid, original_pid)

    def test_repeated_slow_replies_still_trigger_the_processing_lag_limit(self):
        isolation = AudioIsolation("fixture", None, worker_target=echo_isolation_worker)
        self.addCleanup(isolation.close)
        stream = isolation.stream()
        audio = np.full(PACKET_SAMPLES, 0.5, dtype="<f4")
        self.wait_for_audio(stream, audio)

        with (patch("src.backend.media.audio_isolation.time.perf_counter", side_effect=(0, 0.28, 1, 1.28)),
              self.assertLogs("src.backend.media.audio_isolation", level="WARNING") as warnings):
            stream.push(audio)
            self.assertEqual(isolation.mode, "isolated")
            with self.assertRaises(IsolationUnavailable):
                stream.push(audio)

        self.assertEqual(isolation.mode, "raw")
        self.assertIn("could not keep up with playback", warnings.output[0])

    def test_hung_worker_is_replaced_and_audio_processing_resumes(self):
        isolation = AudioIsolation("fixture", None, worker_target=echo_isolation_worker)
        self.addCleanup(isolation.close)
        modes = []
        isolation.on_mode_change = lambda: modes.append(isolation.mode)
        stream = isolation.stream()
        audio = np.full(OPUS_FRAME, 0.5, dtype="<f4")

        np.testing.assert_array_equal(self.wait_for_audio(stream, audio), audio)
        self.assertEqual(isolation.gpu_memory, ECHO_GPU_MEMORY)
        self.assertGreater(isolation.processing_load, 0)
        original_pid = isolation.worker.process.pid
        with self.assertLogs("src.backend.media.audio_isolation", level="WARNING"):
            with self.assertRaises(IsolationUnavailable):
                stream.push(np.full(OPUS_FRAME, -1, dtype="<f4"))
        self.assertEqual(isolation.mode, "raw")
        self.assertIsNone(isolation.gpu_memory)
        self.assertIsNone(isolation.processing_load)
        np.testing.assert_array_equal(self.wait_for_audio(stream, audio), audio)
        self.assertNotEqual(isolation.worker.process.pid, original_pid)
        self.assertEqual(modes, ["isolated", "raw", "isolated"])


class AudioMixTest(unittest.TestCase):
    def test_split_aligns_delayed_voice_and_background_through_end(self):
        isolator = IsolatedAudio(DelayedIsolator())
        original = np.linspace(-1, 1, 1140, dtype="<f4")
        chunks = []
        for offset, samples in [(0, original[:600]), (600, original[600:])]:
            packet = av.Packet(samples.tobytes())
            packet.pts = 2400 + offset
            packet.time_base = AUDIO_TIME_BASE
            chunks.extend(isolator.push(packet))
        chunks.extend(isolator.flush())

        padded = np.pad(original, (0, 2 * OPUS_FRAME - len(original)))
        self.assertEqual([chunk.pts for chunk in chunks], [2400, 2400 + OPUS_FRAME])
        np.testing.assert_allclose(np.concatenate([chunk.voice for chunk in chunks]), padded * 0.25)
        np.testing.assert_allclose(np.concatenate([chunk.background for chunk in chunks]), padded * 0.75)

    def test_fallback_keeps_original_samples_and_pts_through_model_recovery(self):
        class RestartedIsolator(DelayedIsolator):
            calls = 0

            def push(self, samples):
                self.calls += 1
                if self.calls == 1:
                    return np.empty(0, dtype=np.float32)
                if self.calls == 2:
                    raise IsolationUnavailable
                return samples * 0.25

            def flush(self, *, as_numpy):
                return np.empty(0, dtype=np.float32)

        original = np.linspace(-0.5, 0.5, 2100, dtype="<f4")
        isolator = IsolatedAudio(RestartedIsolator())
        chunks = []
        for begin, end in ((0, 700), (700, 1100), (1100, 2100)):
            packet = av.Packet(original[begin:end].tobytes())
            packet.pts = 2400 + begin
            packet.time_base = AUDIO_TIME_BASE
            chunks.extend(isolator.push(packet))
        chunks.extend(isolator.flush())

        passthrough = np.pad(original[:1100], (0, 3 * OPUS_FRAME - 1100))
        np.testing.assert_array_equal(np.concatenate([chunk.passthrough for chunk in chunks]), passthrough)
        mixed = np.concatenate([chunk.voice + chunk.background + chunk.passthrough for chunk in chunks])
        np.testing.assert_allclose(mixed, np.pad(original, (0, 3 * OPUS_FRAME - len(original))), atol=1e-7)
        self.assertEqual([chunk.pts for chunk in chunks], [2400, 2400 + OPUS_FRAME, 2400 + 2 * OPUS_FRAME])

    def test_audio_settings_default_to_original_voice_and_minus_six_db_background(self):
        settings = PlaybackSettings.from_message({})
        self.assertEqual(settings.mix.voice, 1.0)
        self.assertAlmostEqual(settings.mix.background, 0.501187, places=6)
        self.assertEqual(settings.audio_kbps, 96)
        mix = Mix.from_message({"voice_db": None, "background_db": 0})
        self.assertEqual((mix.voice, mix.background), (0.0, 1.0))

    def test_video_delay_is_a_nonnegative_bounded_integer(self):
        self.assertEqual(PlaybackSettings.from_message({}).video_delay_ms, 0)
        self.assertEqual(PlaybackSettings.from_message({"video_delay_ms": 120}).video_delay_ms, 120)
        for invalid in (-1, 501, 120.0, True, None):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                PlaybackSettings.from_message({"video_delay_ms": invalid})


class MediaSubscriptionTest(unittest.IsolatedAsyncioTestCase):
    async def test_late_viewer_waits_for_keyframe_before_receiving_video_and_audio(self):
        viewer = MediaSubscription()
        video, audio = viewer.tracks["video"], viewer.tracks["audio"]
        keyframe, delta, sound = av.Packet(b"key"), av.Packet(b"delta"), av.Packet(b"audio")
        keyframe.is_keyframe = True

        viewer.push("video", delta)
        viewer.push("audio", sound)
        self.assertTrue(video.queue.empty())
        self.assertTrue(audio.queue.empty())

        viewer.push("video", keyframe)
        viewer.push("audio", sound)
        viewer.push("video", delta)
        self.assertIs(await video.recv(), keyframe)
        self.assertIs(await audio.recv(), sound)
        self.assertIs(await video.recv(), delta)
        viewer.close()
        with self.assertRaises(MediaStreamError):
            await video.recv()

    async def test_slow_viewer_resumes_on_the_next_keyframe(self):
        viewer = MediaSubscription()
        video, audio = viewer.tracks["video"], viewer.tracks["audio"]
        keyframe, delta, sound = av.Packet(b"key"), av.Packet(b"delta"), av.Packet(b"audio")
        keyframe.is_keyframe = True

        viewer.push("video", keyframe)
        for _ in range(audio.queue.maxsize + 1):
            viewer.push("audio", sound)
        viewer.push("video", delta)
        self.assertTrue(video.queue.empty())
        self.assertTrue(audio.queue.empty())

        viewer.push("video", keyframe)
        viewer.push("audio", sound)
        self.assertIs(await video.recv(), keyframe)
        self.assertIs(await audio.recv(), sound)

    async def test_shared_video_delay_holds_video_and_preserves_source_position(self):
        relay = MediaRelay(video_delay_ms=40)
        viewers = (MediaSubscription(), MediaSubscription())
        relay.subscribers.update(viewers)

        video, audio = av.Packet(b"key"), av.Packet(b"audio")
        video.is_keyframe = True
        video.pts = audio.pts = 48000
        video.time_base = audio.time_base = AUDIO_TIME_BASE

        async def forward(kind, packet):
            track = SimpleNamespace(kind=kind, recv=AsyncMock(side_effect=[packet, MediaStreamError()]))
            await relay.forward(track, 0.25)

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        await forward("video", video)
        await forward("audio", audio)
        self.assertEqual(relay.position, 1.0)
        self.assertEqual(video.pts, 61920)
        self.assertEqual(audio.pts, 60000)
        for viewer in viewers:
            self.assertIs(await viewer.tracks["audio"].recv(), audio)

        for viewer in viewers:
            self.assertIs(await viewer.tracks["video"].recv(), video)
        self.assertGreaterEqual(loop.time() - started_at, .04)

    async def test_reset_discards_a_video_packet_already_waiting_for_its_deadline(self):
        viewer = MediaSubscription()
        previous, next_frame = av.Packet(b"previous"), av.Packet(b"next")
        previous.is_keyframe = next_frame.is_keyframe = True
        video = viewer.tracks["video"]

        viewer.push("video", previous, asyncio.get_running_loop().time() + .04)
        async with asyncio.TaskGroup() as tasks:
            pending = tasks.create_task(video.recv())
            await asyncio.sleep(0)
            self.assertTrue(video.queue.empty())
            viewer.reset()
            viewer.push("video", next_frame)
        self.assertIs(pending.result(), next_frame)


class ProcessedMediaFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_hardware_keyframe_must_fit_the_receiving_browser_or_negotiated_level(self):
        class Packet(SimpleNamespace):
            def __bytes__(self):
                return self.data

        hardware = HARDWARE_ENCODINGS["darwin"][0]
        source = Source("fixture", "vp9", Input("clip"), video_encoding=hardware)
        actual_codec = "avc1.42c02a"
        prefix = b"\x00\x00\x00\x01"
        video_data = prefix + b"\x67\x42\xc0\x2a" + prefix + b"\x68\xca" + prefix + b"\x65\xb4"
        audio_data = np.zeros(OPUS_FRAME, dtype="<f4").tobytes()
        video = SimpleNamespace(index=0, codec_context=SimpleNamespace(name="h264"))
        audio = SimpleNamespace(index=1)
        container = MagicMock()
        container.__enter__.return_value = container
        container.streams.video = [video]
        container.streams.audio = [audio]
        keyframe = Packet(data=video_data, stream=video, is_keyframe=True, size=len(video_data),
                          pts=0, dts=0, time_base=AUDIO_TIME_BASE)
        samples = Packet(data=audio_data, stream=audio, size=len(audio_data), pts=0, time_base=AUDIO_TIME_BASE)
        container.demux.return_value = (keyframe, samples)

        for codec_for, accepted in ((lambda codec: codec == actual_codec, True),
                                    (lambda codec: codec == hardware.codec, False), (None, False)):
            with self.subTest(accepted=accepted, seek=codec_for is None):
                media = ProcessedMedia(source, DelayedIsolator(), codec_for=codec_for,
                                       start_at=1 if codec_for is None else None)
                media.process = SimpleNamespace(stdout=None)
                with patch("src.backend.media.pipeline.av.open", return_value=container), patch.object(media, "_put") as put:
                    if accepted:
                        media._demux()
                        self.assertEqual(media.source.output_codec, actual_codec)
                        self.assertEqual(put.call_args_list[0].args, (media.video, (0, keyframe)))
                    else:
                        with self.assertRaises(SourceError):
                            media._demux()
                        self.assertEqual(media.source.output_codec, hardware.codec)
                        put.assert_not_called()

    async def test_empty_output_reports_the_upstream_status(self):
        source = Source("fixture", "vp8", Input("https://example.test/clip", "https"))
        command = [
            sys.executable, "-c",
            "import sys; "
            "sys.stderr.write('untrusted diagnostic\\n' * 10000); "
            "sys.stderr.write('Server returned 403 Forbidden for https://example.test/?signature=private\\n'); "
            "sys.exit(8)",
        ]
        media = ProcessedMedia(source, DelayedIsolator())
        with patch("src.backend.media.pipeline.ffmpeg_command", return_value=command):
            await media.start()
        self.addAsyncCleanup(media.close)

        with self.assertRaises(FFmpegInputError) as failure:
            await asyncio.wait_for(media.wait_ready(media.ready), 5)
        self.assertEqual(failure.exception.status, 403)
        self.assertNotIn("signature", str(failure.exception))

    async def test_ffmpeg_server_error_without_numeric_status_is_retryable(self):
        source = Source("fixture", "vp8", Input("https://example.test/clip", "https"))
        command = [sys.executable, "-c", "import sys; "
                   "sys.stderr.write('Server returned 5XX Server Error reply\\n'); sys.exit(8)"]
        media = ProcessedMedia(source, DelayedIsolator())
        with patch("src.backend.media.pipeline.ffmpeg_command", return_value=command):
            await media.start()
        self.addAsyncCleanup(media.close)

        with self.assertRaises(RetryableInputError):
            await asyncio.wait_for(media.wait_ready(media.ready), 5)


class ProcessedMediaSeekTest(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(FFMPEG_DIRECTORY, "FFmpeg is required on PATH or in .runtime")
    async def test_split_copy_seeks_audio_to_the_video_keyframe(self):
        path = FFMPEG_DIRECTORY + os.pathsep + os.environ.get("PATH", "")
        with patch.dict(os.environ, {"PATH": path}):
            await self.check_split_copy_seeks_audio_to_the_video_keyframe()

    async def check_split_copy_seeks_audio_to_the_video_keyframe(self):
        encoders = await asyncio.to_thread(
            subprocess.check_output, ["ffmpeg", "-hide_banner", "-encoders"], text=True, timeout=5,
        )
        if " libx264 " not in encoders:
            self.skipTest("The FFmpeg libx264 encoder is required")
        with TemporaryDirectory() as directory:
            video = str(Path(directory) / "video.mp4")
            audio = str(Path(directory) / "audio.wav")
            await asyncio.to_thread(subprocess.run, [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=60:duration=6",
                "-f", "lavfi", "-i", "sine=frequency=800:duration=6",
                "-map", "0:v", "-c:v", "libx264", "-bf", "0", "-g", "180", "-sc_threshold", "0", video,
                "-map", "1:a", "-c:a", "pcm_s16le", audio,
            ], check=True, timeout=10)
            source = Source("fixture", "avc1.64001f", Input(video), Input(audio))
            media = ProcessedMedia(source, DelayedIsolator(), start_at=5.7)
            try:
                await media.start()
                await asyncio.wait_for(media.wait_ready(media.ready), 5)
                self.assertAlmostEqual(media.first_times["video"], 3)
                self.assertAlmostEqual(media.first_times["audio"], 3, delta=0.04)
            finally:
                await media.close()
