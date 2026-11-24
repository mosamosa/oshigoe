import asyncio
import logging
import re
import subprocess
import threading
from concurrent.futures import TimeoutError as ThreadTimeout
from dataclasses import dataclass, replace
from fractions import Fraction
from io import BufferedReader, BytesIO

import av
import numpy as np
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError

from .audio_isolation import SAMPLE_RATE, IsolationUnavailable
from ..server_messages import Message
from ..settings import PlaybackSettings
from .video_encoding import h264_keyframe_codec, h264_matches
from .media_input import RetryableInputError, SourceError, ffmpeg_command, ffmpeg_keyframe_command


LOG = logging.getLogger(__name__)
AUDIO_TIME_BASE = Fraction(1, SAMPLE_RATE)
OPUS_FRAME = 960
MEDIA_STALL_SECONDS = 20
HTTP_ERROR_STATUS = re.compile(rb"\b(?:HTTP (?:error )?|Server returned )([45][0-9]{2})\b", re.IGNORECASE)
NETWORK_ERROR = re.compile(
    rb"timed out|connection (?:reset|refused|aborted)|network is unreachable|"
    rb"error during demuxing|stream ends prematurely|failed to resolve hostname|"
    rb"temporary failure in name resolution|server returned 5xx server error",
    re.IGNORECASE,
)


class FFmpegInputError(SourceError):
    def __init__(self, status):
        super().__init__(Message.FFMPEG_HTTP_ERROR, status=status)
        self.status = status


class VideoReorderingError(SourceError):
    def __init__(self):
        super().__init__(Message.VIDEO_REORDERING)


class PipeReader:
    """Expose only `read`, so PyAV opens the pipe as unseekable.

    On Windows a pipe reports `seekable()` as true and `seek` returns without moving, so the NUT
    demuxer's seeks during `av.open` misread the stream.
    """

    def __init__(self, pipe):
        self.pipe = pipe

    def read(self, size):
        return self.pipe.read(size)


@dataclass(frozen=True)
class AudioChunk:
    pts: int
    voice: np.ndarray
    background: np.ndarray
    passthrough: np.ndarray


class IsolatedAudio:
    def __init__(self, isolator):
        self.session = isolator.stream()
        self.original = np.empty(0, dtype=np.float32)
        self.pending = np.empty((3, 0), dtype=np.float32)
        self.next_pts = None

    def push(self, packet):
        if self.next_pts is None:
            self.next_pts = round(packet.pts * packet.time_base * SAMPLE_RATE)
        samples = np.frombuffer(bytes(packet), dtype="<f4").astype(np.float32, copy=True)
        self.original = np.concatenate((self.original, samples))
        try:
            voice = self.session.push(samples)
        except IsolationUnavailable:
            yield from self._chunks(self.original, passthrough=True)
        else:
            yield from self._chunks(voice)

    def flush(self):
        if self.next_pts is not None:
            try:
                voice = self.session.flush(as_numpy=True)
            except IsolationUnavailable:
                yield from self._chunks(self.original, passthrough=True, final=True)
            else:
                yield from self._chunks(voice, final=True)

    def _chunks(self, voice, *, passthrough=False, final=False):
        original = self.original[:len(voice)]
        self.original = self.original[len(voice):]
        silence = np.zeros_like(voice)
        components = (silence, silence, original) if passthrough else (voice, original - voice, silence)
        samples = np.concatenate((self.pending, np.stack(components)), axis=1)
        length = samples.shape[1]
        if final and length % OPUS_FRAME:
            samples = np.pad(samples, ((0, 0), (0, OPUS_FRAME - length % OPUS_FRAME)))
        count = samples.shape[1] // OPUS_FRAME * OPUS_FRAME
        for start in range(0, count, OPUS_FRAME):
            voice_frame, background_frame, passthrough_frame = samples[:, start:start + OPUS_FRAME]
            yield AudioChunk(self.next_pts, voice_frame, background_frame, passthrough_frame)
            self.next_pts += OPUS_FRAME
        self.pending = samples[:, count:].copy()


class PacedTrack(MediaStreamTrack):
    def __init__(self, kind, media):
        super().__init__()
        self.kind = kind
        self.media = media
        self.queue = asyncio.Queue(maxsize=120)
        self.drained = asyncio.Event()

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        item = await self.queue.get()
        if item is None:
            self.drained.set()
            self.stop()
            raise MediaStreamError
        await self.media.ready.wait()
        when, frame = item
        now = self.media.loop.time()
        if self.media.clock_offset is None:
            self.media.clock_offset = now - min(self.media.first_times.values())
        delay = self.media.clock_offset + when - now
        if delay > 0:
            await asyncio.sleep(delay)
        return frame


class MixedAudioTrack(PacedTrack):
    def __init__(self, media, settings):
        super().__init__("audio", media)
        self.settings = settings
        self.previous_mix = None
        self.encoder = av.CodecContext.create("libopus", "w")
        self.encoder.format = "s16"
        self.encoder.layout = "mono"
        self.encoder.sample_rate = SAMPLE_RATE
        self.encoder.time_base = AUDIO_TIME_BASE
        self.encoder.bit_rate = settings.audio_kbps * 1000
        self.encoder.options = {"application": "voip"}

    async def recv(self):
        chunk = await super().recv()
        mix = self.settings.mix
        voice, background = mix.voice, mix.background
        if self.previous_mix is not None and mix != self.previous_mix:
            voice = np.linspace(self.previous_mix.voice, voice, OPUS_FRAME, dtype=np.float32)
            background = np.linspace(self.previous_mix.background, background, OPUS_FRAME, dtype=np.float32)
        self.previous_mix = mix
        samples = chunk.voice * voice + chunk.background * background + chunk.passthrough
        pcm = np.rint(np.clip(samples * 32768, -32768, 32767)).astype(np.int16)
        frame = av.AudioFrame.from_ndarray(pcm[None, :], format="s16", layout="mono")
        frame.sample_rate = SAMPLE_RATE
        frame.pts = chunk.pts
        frame.time_base = AUDIO_TIME_BASE
        packet, = await asyncio.to_thread(self.encoder.encode, frame)
        packet.pts = chunk.pts
        return packet


class RelayedTrack(MediaStreamTrack):
    def __init__(self, kind):
        super().__init__()
        self.kind = kind
        self.queue = asyncio.Queue(maxsize=120)
        self.generation = 0

    async def recv(self):
        while self.readyState == "live":
            item = await self.queue.get()
            if item is None:
                break
            packet, ready_at = item
            if ready_at is not None:
                generation = self.generation
                delay = ready_at - asyncio.get_running_loop().time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if generation != self.generation:
                    continue
            return packet
        raise MediaStreamError

    def clear(self):
        self.generation += 1
        while not self.queue.empty():
            self.queue.get_nowait()

    def stop(self):
        if self.readyState == "live":
            super().stop()
            self.clear()
            self.queue.put_nowait(None)


class MediaSubscription:
    def __init__(self):
        self.tracks = {kind: RelayedTrack(kind) for kind in ("video", "audio")}
        self.needs_keyframe = True

    def push(self, kind, packet, ready_at=None):
        track = self.tracks[kind]
        if track.readyState != "live":
            return
        if track.queue.full():
            self.reset()
        if self.needs_keyframe:
            if kind != "video" or not packet.is_keyframe:
                return
            self.needs_keyframe = False
        track.queue.put_nowait((packet, ready_at))

    def reset(self):
        for track in self.tracks.values():
            if track.readyState == "live":
                track.clear()
        self.needs_keyframe = True

    def close(self):
        for track in self.tracks.values():
            track.stop()


class MediaRelay:
    def __init__(self, video_delay_ms=0):
        self.subscribers = set()
        self.video_delay = video_delay_ms / 1000
        self.last_timestamp = -0.02
        self.started_at = None
        self.position = 0.0

    def reset_subscribers(self):
        for viewer in self.subscribers:
            viewer.reset()

    def start_segment(self, now, first_timestamp, video_start):
        self.reset_subscribers()
        self.position = video_start
        if self.started_at is None:
            self.started_at = now
        # Keep outgoing timestamps increasing across segments, including backward seeks.
        timestamp = max(now - self.started_at, self.last_timestamp + 0.02)
        return timestamp - first_timestamp

    async def forward(self, track, timestamp_offset=None):
        loop = asyncio.get_running_loop()
        delay = self.video_delay if track.kind == "video" else 0
        while True:
            try:
                async with asyncio.timeout(MEDIA_STALL_SECONDS):
                    packet = await track.recv()
            except MediaStreamError:
                return
            except TimeoutError as error:
                raise RetryableInputError(Message.MEDIA_STALLED) from error
            if timestamp_offset is not None:
                if track.kind == "video":
                    self.position = max(self.position, float(packet.pts * packet.time_base))
                packet.pts += round(timestamp_offset / packet.time_base)
                self.last_timestamp = max(self.last_timestamp, float(packet.pts * packet.time_base))
                if delay:
                    packet.pts += round(delay / packet.time_base)
            ready_at = loop.time() + delay if delay else None
            for viewer in self.subscribers:
                viewer.push(track.kind, packet, ready_at)


class ProcessedMedia:
    def __init__(self, source, isolator, settings=None, *, start_at=None, codec_for=None):
        self.source = source
        self.isolator = isolator
        self.loop = asyncio.get_running_loop()
        self.ready = asyncio.Event()
        self.finished = self.loop.create_future()
        self.stopping = threading.Event()
        self.video = PacedTrack("video", self)
        self.audio = MixedAudioTrack(self, settings if settings is not None else PlaybackSettings())
        self.first_times = {}
        self.video_start = None
        self.clock_offset = None
        self.start_at = start_at
        self.codec_for = codec_for
        self.process = None
        self._close_task = None
        self.worker = None
        self.stderr_worker = None
        self.http_status = None
        self.network_error = False

    async def start(self):
        audio_start = None
        if self.start_at and self.source.audio is not None and self.source.video_encoding is None:
            audio_start = await self._seek_keyframe()
        self.process = subprocess.Popen(
            ffmpeg_command(self.source, self.start_at, audio_start=audio_start),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0,
        )
        stderr_worker = threading.Thread(target=self._read_stderr, name="ffmpeg-stderr")
        stderr_worker.start()
        self.stderr_worker = stderr_worker
        worker = threading.Thread(target=self._read, name="media")
        worker.start()
        self.worker = worker

    async def _seek_keyframe(self):
        process = await asyncio.create_subprocess_exec(
            *ffmpeg_keyframe_command(self.source, self.start_at),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(15):
                output, errors = await process.communicate()
        except TimeoutError as error:
            raise RetryableInputError(Message.MEDIA_TIMEOUT) from error
        finally:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.communicate()

        if process.returncode:
            status = HTTP_ERROR_STATUS.search(errors)
            if status:
                raise FFmpegInputError(int(status[1]))
            raise RetryableInputError(Message.FFMPEG_READ_FAILED)
        try:
            with av.open(BytesIO(output), format="nut") as container:
                for packet in container.demux(video=0):
                    if packet.is_keyframe and packet.pts is not None:
                        return max(0.0, float(packet.pts * packet.time_base))
        except av.FFmpegError as error:
            raise RetryableInputError(Message.KEYFRAME_MISSING) from error
        raise RetryableInputError(Message.KEYFRAME_MISSING)

    def _read_stderr(self):
        with BufferedReader(self.process.stderr) as stream:
            while line := stream.readline(8192):
                match = HTTP_ERROR_STATUS.search(line)
                if match:
                    self.http_status = int(match[1])
                if NETWORK_ERROR.search(line):
                    self.network_error = True

    async def play_to_end(self):
        error = await self.finished
        if error is not None:
            raise error
        await asyncio.gather(self.video.drained.wait(), self.audio.drained.wait())

    async def wait_ready(self, event):
        async with asyncio.TaskGroup() as tasks:
            ready = tasks.create_task(event.wait())
            try:
                done, _ = await asyncio.wait({ready, self.finished}, timeout=30,
                                             return_when=asyncio.FIRST_COMPLETED)
            finally:
                ready.cancel()
        if not done:
            raise RetryableInputError(Message.MEDIA_TIMEOUT)
        if not event.is_set():
            raise self.finished.result() or SourceError(Message.NO_MEDIA_RECEIVED)

    def _kill_process(self):
        if self.process.poll() is None:
            try:
                self.process.kill()
            except ProcessLookupError:
                pass

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _close(self):
        self.stopping.set()
        if self.process is not None:
            self._kill_process()
            if self.worker is not None:
                await asyncio.to_thread(self.worker.join)
            if self.stderr_worker is not None:
                await asyncio.to_thread(self.stderr_worker.join)
            self.process.stdout.close()
            self.process.stderr.close()
            await asyncio.to_thread(self.process.wait)
        self.audio.stop()
        self.video.stop()

    async def _enqueue(self, track, item):
        await track.queue.put(item)
        if item is not None and track.kind not in self.first_times:
            self.first_times[track.kind] = item[0]
            if track.kind == "video":
                packet = item[1]
                self.video_start = float(packet.pts * packet.time_base)
            if len(self.first_times) == 2:
                self.ready.set()

    def _put(self, track, item):
        if self.stopping.is_set():
            return
        put = asyncio.run_coroutine_threadsafe(self._enqueue(track, item), self.loop)
        while not self.stopping.is_set():
            try:
                put.result(timeout=0.2)
                return
            except ThreadTimeout:
                continue
        put.cancel()

    def _complete(self, error):
        if not self.finished.done():
            self.finished.set_result(error)

    def _input_error(self):
        self.stderr_worker.join()
        if self.http_status is not None:
            return FFmpegInputError(self.http_status)
        return self._read_error(Message.FFMPEG_READ_FAILED)

    def _read_error(self, message):
        if self.ready.is_set() or self.network_error:
            return RetryableInputError(message)
        return SourceError(message)

    def _read(self):
        error = None
        try:
            self._demux()
            if not self.stopping.is_set():
                if self.process.wait() != 0:
                    error = self._input_error()
                else:
                    self._put(self.video, None)
                    self._put(self.audio, None)
        except SourceError as exc:
            error = exc
        except av.FFmpegError:
            if not self.stopping.is_set():
                try:
                    returncode = self.process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    self._kill_process()
                    self.process.wait()
                    returncode = 0
                if returncode != 0:
                    error = self._input_error()
                else:
                    error = self._read_error(Message.MEDIA_PROCESSING_FAILED)
        except Exception:
            if not self.stopping.is_set():
                LOG.exception("Media processing failed")
                error = SourceError(Message.MEDIA_PROCESSING_FAILED)
        finally:
            self.loop.call_soon_threadsafe(self._complete, None if self.stopping.is_set() else error)

    def _demux(self):
        isolated = IsolatedAudio(self.isolator)
        encoding = self.source.video_encoding
        first_video_pts = None
        with av.open(PipeReader(self.process.stdout), format="nut", buffer_size=4096,
                     options={"probesize": "32768", "analyzeduration": "100000"}) as container:
            video, audio = container.streams.video[0], container.streams.audio[0]
            expected = "vp8" if self.source.output_codec.startswith("vp8") else "h264"
            if video.codec_context.name != expected:
                raise SourceError(Message.CODEC_MISMATCH)
            if encoding is None and expected == "h264" and video.codec_context.has_b_frames:
                raise VideoReorderingError()
            for packet in container.demux(video, audio):
                if self.stopping.is_set():
                    return
                if not packet.size or packet.pts is None:
                    continue
                if packet.stream.index == video.index:
                    if first_video_pts is None:
                        if not packet.is_keyframe:
                            continue
                        if encoding and encoding.hardware:
                            codec = h264_keyframe_codec(bytes(packet))
                            if not h264_matches(codec, encoding.codec, allow_higher_level=self.codec_for is not None):
                                raise SourceError(Message.HARDWARE_H264_MISSING)
                            if self.codec_for is not None:
                                if not self.codec_for(codec):
                                    raise SourceError(Message.HARDWARE_H264_LEVEL)
                                self.source = replace(self.source, video_encoding=replace(encoding, codec=codec))
                        first_video_pts = packet.pts
                    send_pts = packet.dts if packet.dts is not None else first_video_pts
                    self._put(self.video, (float(send_pts * packet.time_base), packet))
                else:
                    for chunk in isolated.push(packet):
                        self._put(self.audio, (chunk.pts / SAMPLE_RATE, chunk))
            for chunk in isolated.flush():
                self._put(self.audio, (chunk.pts / SAMPLE_RATE, chunk))
        if first_video_pts is None or isolated.next_pts is None:
            error = RetryableInputError if self.source.video.protocol != "file" else SourceError
            raise error(Message.KEYFRAME_MISSING)
