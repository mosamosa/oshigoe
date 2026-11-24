import asyncio
import logging
import secrets
from dataclasses import asdict, dataclass, field, replace

from aiohttp import web
from aiortc import (
    RTCBundlePolicy,
    RTCConfiguration,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)

from .media.media_input import RetryableInputError, SourceError
from .media.pipeline import SAMPLE_RATE, FFmpegInputError, MediaRelay, MediaSubscription, ProcessedMedia, VideoReorderingError
from .media.video_encoding import h264_matches, select_encoder, video_bitrate_kbps
from .rtc_negotiation import parse_offer
from .server_messages import Message, message_payload
from .settings import Mix, PlaybackSettings
from .sources.registry import normalize_url, resolve_source


LOG = logging.getLogger("src.backend.server")
VOICE_MODEL = "voice-isolator-v1-realtime"
RECONNECT_GRACE_SECONDS = 15
VIEWER_DETACH_SECONDS = 30
INPUT_RETRY_DELAYS = (1, 2, 4)
INPUT_RETRY_RESET_SECONDS = 10
INPUT_RECOVERY_SECONDS = 35
INPUT_RETRY_ATTEMPT_SECONDS = 9


class ViewerConnectionError(SourceError):
    pass


@dataclass(eq=False)
class Viewer:
    socket: web.WebSocketResponse | None
    request_id: int
    token: str = field(default_factory=lambda: secrets.token_urlsafe(24), repr=False)
    task: asyncio.Task | None = None
    connected: bool = False
    detach_timer: asyncio.TimerHandle | None = None

    def cancel_detach_timer(self):
        if self.detach_timer is not None:
            self.detach_timer.cancel()
            self.detach_timer = None


async def connect_viewer(broadcast, viewer, offer, codec_for):
    ws = viewer.socket
    reply = {"request_id": viewer.request_id, "broadcast_id": broadcast.id, "viewer_token": viewer.token}
    await ws.send_json({"type": "accepted", **reply})
    await broadcast.prepared.wait()
    codec = codec_for(broadcast.output_codec)
    if codec is None:
        raise SourceError(Message.VIDEO_CODEC_UNSUPPORTED)

    subscription = MediaSubscription()
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[], bundlePolicy=RTCBundlePolicy.MAX_BUNDLE))
    connected = asyncio.Event()
    lost = asyncio.Event()

    @peer.on("connectionstatechange")
    def connection_changed():
        if peer.connectionState == "connected":
            connected.set()
        elif peer.connectionState in {"failed", "closed"}:
            lost.set()
            connected.set()

    try:
        video = peer.addTransceiver(subscription.tracks["video"], direction="sendonly")
        video.setCodecPreferences([codec])
        audio = peer.addTransceiver(subscription.tracks["audio"], direction="sendonly")
        opus = next(codec for codec in RTCRtpSender.getCapabilities("audio").codecs
                    if codec.mimeType.lower() == "audio/opus")
        audio.setCodecPreferences([opus])
        await peer.setRemoteDescription(RTCSessionDescription(sdp=offer, type="offer"))
        await peer.setLocalDescription(await peer.createAnswer())
        await ws.send_json({"type": "answer", **reply, "sdp": peer.localDescription.sdp,
                            "title": broadcast.title, "stream": broadcast.stream})
        await asyncio.wait_for(connected.wait(), 15)
        if lost.is_set():
            raise ViewerConnectionError(Message.WEBRTC_CONNECT_FAILED)

        viewer.connected = True
        broadcast.relay.subscribers.add(subscription)
        broadcast.connected.set()
        await lost.wait()
        raise ViewerConnectionError(Message.WEBRTC_DISCONNECTED)
    finally:
        broadcast.relay.subscribers.discard(subscription)
        subscription.close()
        await peer.close()


def stream_status(source, isolator, settings, fallbacks):
    encoding = source.video_encoding
    metadata = source.metadata
    device = getattr(isolator, "device", None)
    return {
        "input": {
            **asdict(metadata), "video_codec": source.codec,
            "video_protocol": source.video.protocol,
            "audio_protocol": source.audio.protocol if source.audio else None,
        },
        "video": {
            "encoder": encoding.encoder if encoding else None,
            "hardware": encoding.hardware if encoding else None,
            "target_kbps": video_bitrate_kbps(encoding, source.quality, height=metadata.height,
                                              width=metadata.width, fps=metadata.fps),
            "auto_bitrate": encoding is not None and source.quality.bitrate_kbps is None,
            "delay_ms": settings.video_delay_ms,
            "fallbacks": fallbacks,
        },
        "audio": {
            "model": VOICE_MODEL, "device": str(device) if device is not None else None,
            "mode": getattr(isolator, "mode", "isolated"),
            "sample_rate": SAMPLE_RATE, "target_kbps": settings.audio_kbps,
        },
    }


async def prepare_media(source, isolator, settings, encodings, codec_for):
    remaining = list(encodings)
    fallbacks = []
    while True:
        encoding = source.video_encoding
        media = ProcessedMedia(source, isolator, settings, codec_for=codec_for)
        try:
            await media.start()
            await media.wait_ready(media.ready)
        except SourceError as error:
            retry_copy = encoding is None and isinstance(error, VideoReorderingError)
            retry_encoder = (encoding is not None and media.finished.done()
                             and not isinstance(error, (FFmpegInputError, RetryableInputError)))
            await media.close()
            if not (retry_copy or retry_encoder):
                raise
            if encoding is not None:
                remaining.remove(encoding)
            fallback = select_encoder(remaining, codec_for, settings.video,
                                      height=source.metadata.height, width=source.metadata.width)
            if fallback is None:
                raise
            previous_mode = encoding.encoder if encoding else "copy"
            LOG.warning("Could not start video processing with %s. Retrying with %s.", previous_mode, fallback.encoder)
            fallbacks.append({"from": previous_mode, "to": fallback.encoder, "reason": message_payload(error)})
            source = replace(source, video_encoding=fallback)
        except BaseException:
            await media.close()
            raise
        else:
            return media, fallbacks


def should_retry_input(error):
    if isinstance(error, FFmpegInputError):
        return error.status in {403, 404, 408, 410, 429} or 500 <= error.status <= 599
    return isinstance(error, RetryableInputError)


def should_switch_input_transport(error):
    if isinstance(error, FFmpegInputError):
        return error.status in {403, 408} or error.status >= 500
    return isinstance(error, RetryableInputError)


class Broadcast:
    def __init__(self, room, url, settings):
        self.room = room
        self.id = room.next_id
        self.url = url
        self.settings = settings
        self.title = None
        self.stream = None
        self.phase = "preparing"
        self.prepared = asyncio.Event()
        self.connected = asyncio.Event()
        self.stopping = asyncio.Event()
        self.relay = MediaRelay(settings.video_delay_ms)
        self.viewers = {}
        self.job = None
        self.idle_stop = None
        self.duration = None
        self.seek_request = None
        self.seek_target = None
        self.seek_revision = 0
        self.resume_requested = asyncio.Event()
        self.audio_isolator = None

    def snapshot(self):
        position = min(self.duration, max(0.0, self.relay.position)) if self.duration else None
        if self.phase in {"pausing", "paused", "resuming"}:
            position = self.seek_target
        audio_mode = getattr(self.audio_isolator, "mode", "isolated")
        stream = self.stream
        if stream is not None:
            device = getattr(self.audio_isolator, "device", None)
            stream = {**stream, "audio": {**stream["audio"], "mode": audio_mode,
                                        "device": str(device) if device is not None else None}}
        return {"id": self.id, "url": self.url, "title": self.title, "phase": self.phase,
                "duration": self.duration, "position": position,
                "seek_target": self.seek_target, "seek_revision": self.seek_revision,
                "stream": stream,
                "audio_mode": audio_mode}

    def seek(self, position):
        if (self.phase != "playing" or self.seek_request is None
                or type(position) not in (int, float) or not 0 <= position <= self.duration):
            return False
        self.phase = "seeking"
        self.seek_target = position
        self.seek_revision += 1
        self.seek_request.set_result(min(position, max(0.0, self.duration - 0.1)))
        self.room.publish()
        return True

    def pause_playback(self):
        if self.phase != "playing" or self.seek_request is None:
            return
        position = min(self.relay.position, max(0.0, self.duration - 0.1))
        self.phase = "pausing"
        self.seek_target = position
        self.seek_request.set_result(position)
        self.room.publish()

    def resume_playback(self):
        if self.phase == "paused":
            self.phase = "resuming"
            self.resume_requested.set()
            self.room.publish()

    def viewer_for(self, ws):
        return next((viewer for viewer in self.viewers.values() if viewer.socket is ws), None)

    def attach(self, ws, request_id, offer, codec_for):
        self.cancel_idle_stop()
        viewer = Viewer(ws, request_id)
        viewer.task = asyncio.create_task(self.watch(viewer, offer, codec_for))
        self.viewers[viewer.token] = viewer
        viewer.task.add_done_callback(lambda _task: self.remove(viewer))

    def remove(self, viewer):
        del self.viewers[viewer.token]
        viewer.cancel_detach_timer()
        if not self.viewers and not self.stopping.is_set():
            self.idle_stop = asyncio.get_running_loop().call_later(RECONNECT_GRACE_SECONDS, self.stop)

    def resume(self, ws, request_id, token):
        viewer = self.viewers.get(token) if isinstance(token, str) else None
        if (viewer is None or viewer.request_id != request_id or not viewer.connected or self.stopping.is_set()
                or viewer.task.done() or viewer.task.cancelling()
                or (viewer.socket is not None and viewer.socket is not ws)
                or (self.viewer_for(ws) not in (None, viewer))):
            return False
        viewer.cancel_detach_timer()
        viewer.socket = ws
        return True

    async def detach(self, ws):
        viewer = self.viewer_for(ws)
        if viewer is None:
            return
        if not viewer.connected:
            await self.cancel_tasks((viewer.task,))
            return
        viewer.socket = None
        viewer.detach_timer = asyncio.get_running_loop().call_later(VIEWER_DETACH_SECONDS, viewer.task.cancel)

    async def leave(self, ws, request_id=None, token=None):
        viewer = self.viewer_for(ws) if token is None else self.viewers.get(token)
        if (viewer is not None and (viewer.socket is None or viewer.socket is ws)
                and (request_id is None or viewer.request_id == request_id)):
            await self.cancel_tasks((viewer.task,))

    def cancel_idle_stop(self):
        if self.idle_stop is not None:
            self.idle_stop.cancel()
            self.idle_stop = None

    def stop(self):
        self.cancel_idle_stop()
        if not self.stopping.is_set():
            self.stopping.set()
            self.phase = "stopping"
            self.room.publish()

    async def watch(self, viewer, offer, codec_for):
        retryable = False
        try:
            await connect_viewer(self, viewer, offer, codec_for)
        except ConnectionError:
            return
        except SourceError as error:
            message = error
            retryable = isinstance(error, ViewerConnectionError)
        except TimeoutError:
            message = Message.WEBRTC_TIMEOUT
            retryable = True
        except Exception:
            LOG.exception("Viewer connection failed")
            message = Message.WEBRTC_START_FAILED
        ws = viewer.socket
        if ws is not None and not ws.closed:
            try:
                await ws.send_json({"type": "error", "request_id": viewer.request_id, "message": message_payload(message),
                                    "retryable": retryable})
            except ConnectionError:
                pass

    async def retry_input(self, error, delays, *, deadline=None):
        delay = next(delays, None) if should_retry_input(error) else None
        if delay is None:
            raise error
        if deadline is not None and asyncio.get_running_loop().time() + delay >= deadline:
            raise RetryableInputError(Message.INPUT_RECOVERY_DEADLINE) from error
        self.phase = "reconnecting"
        self.room.publish()
        LOG.warning("Retrying the source in %s s. %s", delay, error)
        await asyncio.sleep(delay)

    async def open_input(self, isolator, encodings, codec_for):
        delays = iter(INPUT_RETRY_DELAYS)
        prefer_hls = None
        immediate_retry = True
        while True:
            source = None
            try:
                source = await resolve_source(self.url, codec_for, encodings, self.settings.video,
                                              prefer_hls=prefer_hls,
                                              use_browser_login=self.settings.use_browser_login)
                return await prepare_media(source, isolator, self.settings, encodings, codec_for)
            except SourceError as error:
                if isinstance(error, FFmpegInputError) and error.status == 403 and source is not None:
                    prefer_hls = not source.video.hls
                    if immediate_retry:
                        immediate_retry = False
                        LOG.warning("FFmpeg received HTTP 403 over %s. Refreshing source formats and trying %s first.",
                                    source.video.protocol, "HLS" if prefer_hls else "direct HTTP(S)")
                        continue
                await self.retry_input(error, delays)

    async def refresh_input(self, source, *, prefer_hls):
        def compatible(codec):
            if self.output_codec in {"vp8", "vp8.0"}:
                return codec in {"vp8", "vp8.0"}
            return codec.startswith(("avc1.", "avc3.")) and h264_matches(codec, self.output_codec)

        encodings = (source.video_encoding,) if source.video_encoding else ()
        try:
            fresh = await resolve_source(self.url, compatible, encodings, self.settings.video,
                                         prefer_hls=prefer_hls, live_only=bool(source.metadata.is_live),
                                         use_browser_login=self.settings.use_browser_login)
        except SourceError as error:
            raise RetryableInputError(error) from error
        if fresh is None:
            return None
        return replace(fresh, video_encoding=source.video_encoding)

    async def reopen_input(self, source, isolator, position, *, prefer_hls):
        fresh = await self.refresh_input(source, prefer_hls=prefer_hls)
        if fresh is None:
            return None
        media = ProcessedMedia(fresh, isolator, self.settings, start_at=position)
        try:
            await media.start()
            await media.wait_ready(media.ready)
        except BaseException:
            await media.close()
            raise
        return media

    async def recover_input(self, source, isolator, position, failure, delays, deadline):
        loop = asyncio.get_running_loop()
        prefer_hls = source.video.hls
        if isinstance(failure, FFmpegInputError) and failure.status == 403:
            prefer_hls = not prefer_hls
        while True:
            await self.retry_input(failure, delays, deadline=deadline)
            expires = min(deadline, loop.time() + INPUT_RETRY_ATTEMPT_SECONDS)
            try:
                async with asyncio.timeout_at(expires):
                    return await self.reopen_input(source, isolator, position, prefer_hls=prefer_hls)
            except TimeoutError:
                failure = RetryableInputError(Message.INPUT_RECOVERY_TIMEOUT)
            except SourceError as error:
                failure = error
            if prefer_hls == source.video.hls and should_switch_input_transport(failure):
                prefer_hls = not prefer_hls

    async def deliver(self, isolator, encodings, codec_for):
        media, fallbacks = await self.open_input(isolator, encodings, codec_for)
        source = media.source
        self.title = source.title
        self.duration = None if source.metadata.is_live else source.metadata.duration
        self.output_codec = source.output_codec
        LOG.info("Video input %s, output %s (%s)", source.codec, source.output_codec,
                 source.video_encoding.encoder if source.video_encoding else "copy")
        position = None
        deadline = None
        delays = iter(INPUT_RETRY_DELAYS)
        while True:
            failure = None
            try:
                if self.stopping.is_set():
                    return
                if media is None:
                    media = ProcessedMedia(source, isolator, self.settings, start_at=position)
                if media.process is None:
                    await media.start()
                self.stream = stream_status(source, isolator, self.settings, fallbacks)
                if not self.prepared.is_set():
                    self.prepared.set()
                    self.room.publish()
                target = await self.play_segment(media)
                if target is None:
                    if self.stopping.is_set():
                        return
                    end_tolerance = min(5, max(0.5, (self.duration or 0) * 0.02))
                    early = self.duration and self.relay.position < self.duration - end_tolerance
                    if source.metadata.is_live or early:
                        raise RetryableInputError(Message.INPUT_ENDED_EARLY)
                    return
                position = target
                if self.phase == "pausing":
                    self.relay.reset_subscribers()
            except SourceError as error:
                if self.phase == "playing":
                    if self.relay.position - media.video_start >= INPUT_RETRY_RESET_SECONDS:
                        delays = iter(INPUT_RETRY_DELAYS)
                        deadline = None
                    if self.duration:
                        position = min(self.relay.position, max(0, self.duration - 0.1))
                failure = error
                if deadline is None:
                    deadline = asyncio.get_running_loop().time() + INPUT_RECOVERY_SECONDS
            finally:
                if media is not None:
                    await media.close()
                    media = None
            if self.phase == "pausing":
                self.phase = "paused"
                self.room.publish()
                await self.resume_requested.wait()
                self.resume_requested.clear()
                delays = iter(INPUT_RETRY_DELAYS)
                deadline = None
            if failure is not None:
                media = await self.recover_input(source, isolator, position, failure, delays, deadline)
                if media is None:
                    return
                source = media.source

    async def play_segment(self, media):
        await self.connected.wait()
        await media.wait_ready(media.ready)
        if self.stopping.is_set():
            return None

        loop = asyncio.get_running_loop()
        now = loop.time()
        first_timestamp = min(media.first_times.values())
        media.clock_offset = now - first_timestamp
        timestamp_offset = self.relay.start_segment(now, first_timestamp, media.video_start)
        self.seek_target = None
        if self.duration:
            self.seek_request = loop.create_future()
        self.phase = "playing"
        self.room.publish()

        playback = asyncio.create_task(self.forward_segment(media, timestamp_offset))
        if self.seek_request is None:
            await playback
            return None

        seek_request = self.seek_request
        updates = asyncio.create_task(self.publish_position())
        try:
            done, _ = await asyncio.wait((playback, seek_request), return_when=asyncio.FIRST_COMPLETED)
            if seek_request in done:
                return seek_request.result()
            await playback
        finally:
            self.seek_request = None
            seek_request.cancel()
            await self.cancel_tasks((playback, updates))

    async def forward_segment(self, media, timestamp_offset):
        tasks = [asyncio.create_task(operation) for operation in (
            self.relay.forward(media.video, timestamp_offset), self.relay.forward(media.audio, timestamp_offset),
            media.play_to_end(),
        )]
        try:
            await asyncio.gather(*tasks)
            await asyncio.sleep(0.5 + self.relay.video_delay)
        finally:
            await self.cancel_tasks(tasks)

    async def publish_position(self):
        while True:
            await asyncio.sleep(1)
            self.room.publish()

    @staticmethod
    async def cancel_tasks(tasks):
        for task in tasks:
            if not task.cancelling():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, isolator, encodings, codec_for):
        self.audio_isolator = isolator
        delivery = asyncio.create_task(self.deliver(isolator, encodings, codec_for))
        stop = asyncio.create_task(self.stopping.wait())
        kind, message = "stopped", Message.BROADCAST_STOPPED
        try:
            await asyncio.wait((delivery, stop), return_when=asyncio.FIRST_COMPLETED)
            if not self.stopping.is_set():
                await delivery
                kind, message = "ended", Message.BROADCAST_ENDED
        except SourceError as error:
            kind, message = "error", error
        except Exception:
            LOG.exception("Stream processing failed")
            kind = "error"
            message = (Message.BROADCAST_START_FAILED if self.relay.started_at is None
                       else Message.BROADCAST_CONTINUE_FAILED)
        finally:
            self.stop()
            await self.cancel_tasks((delivery, stop, *(viewer.task for viewer in self.viewers.values())))
            self.room.broadcast = None
            self.room.outcome = {"id": self.id, "kind": kind, "message": message_payload(message)}
            self.room.publish()


@dataclass
class SignalState:
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    mix_ack: int = 0


class BroadcastRoom:
    def __init__(self):
        self.broadcast = None
        self.outcome = None
        self.next_id = 1
        self.mix = Mix()
        self.sockets = {}
        self.resources = None

    def publish(self):
        for state in self.sockets.values():
            state.changed.set()

    async def send_states(self, ws, state):
        try:
            while True:
                await state.changed.wait()
                state.changed.clear()
                await ws.send_json({"type": "state", "mix": self.mix.to_message(), "outcome": self.outcome,
                                    "mix_ack": state.mix_ack, "resources": self.resources,
                                    "broadcast": self.broadcast.snapshot() if self.broadcast else None})
        except ConnectionError:
            await ws.close()

    def set_mix(self, data, state):
        try:
            level = data.get("level")
            if level in ("voice_db", "background_db") and "db" in data:
                self.mix = Mix.from_message({**self.mix.to_message(), level: data["db"]})
                if self.broadcast:
                    self.broadcast.settings.mix = self.mix
                self.publish()
        finally:
            request_id = data.get("request_id")
            if type(request_id) is int and request_id > state.mix_ack:
                state.mix_ack = request_id
                state.changed.set()

    def offer(self, ws, data, isolator, encodings):
        request_id = data["request_id"]
        broadcast_id = data.get("broadcast_id")
        broadcast = self.broadcast
        if broadcast_id is not None:
            if (type(broadcast_id) is not int or broadcast is None or broadcast.id != broadcast_id
                    or broadcast.stopping.is_set()):
                raise SourceError(Message.BROADCAST_GONE)
            if broadcast.viewer_for(ws) is not None:
                raise SourceError(Message.ALREADY_WATCHING)
            broadcast.attach(ws, request_id, data["sdp"], parse_offer(data.get("sdp")))
            return
        if broadcast is not None:
            raise SourceError(Message.BROADCAST_BUSY)
        url = data.get("url")
        if not isinstance(url, str):
            raise SourceError(Message.MISSING_SOURCE_URL)
        url = normalize_url(url)
        codec_for = parse_offer(data.get("sdp"))
        settings = PlaybackSettings.from_message({
            "quality": data.get("quality", {}), "use_browser_login": data.get("use_browser_login", False),
            "video_delay_ms": data.get("video_delay_ms", 0),
        })
        settings.mix = self.mix
        self.broadcast = broadcast = Broadcast(self, url, settings)
        self.next_id += 1
        self.outcome = None
        broadcast.attach(ws, request_id, data["sdp"], codec_for)
        broadcast.job = asyncio.create_task(broadcast.run(isolator, encodings, codec_for))
        self.publish()
