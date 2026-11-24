import asyncio
import unittest
from threading import Event
from unittest.mock import AsyncMock, Mock, patch

from src.backend.media.pipeline import FFmpegInputError, ProcessedMedia
from src.backend.broadcast import Broadcast, BroadcastRoom, SignalState, Viewer, ViewerConnectionError
from src.backend.server_messages import Message
from src.backend.settings import PlaybackSettings
from src.backend.media.video_encoding import VIDEO_ENCODINGS
from src.backend.media.media_input import Input, InputMetadata, RetryableInputError, Source, SourceError


class SharedMixTest(unittest.IsolatedAsyncioTestCase):
    async def test_coalesced_state_acknowledges_each_viewers_own_mix_change(self):
        room = BroadcastRoom()
        messages = asyncio.Queue()
        first = Mock(send_json=AsyncMock(side_effect=messages.put))
        second = Mock()
        first_state = room.sockets[first] = SignalState()
        second_state = room.sockets[second] = SignalState()
        room.set_mix({"request_id": 7, "level": "background_db", "db": -18}, first_state)
        room.set_mix({"request_id": 3, "level": "background_db", "db": -6}, second_state)

        states = asyncio.create_task(room.send_states(first, first_state))
        try:
            state = await asyncio.wait_for(messages.get(), 1)
            self.assertEqual(state["mix"]["background_db"], -6)
            self.assertEqual(state["mix_ack"], 7)
            self.assertEqual(second_state.mix_ack, 3)
        finally:
            states.cancel()
            await asyncio.gather(states, return_exceptions=True)


class ReconnectGraceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.broadcast = Broadcast(BroadcastRoom(), "https://youtu.be/aqz-KE-bpKQ", PlaybackSettings())

        async def watch(*_):
            await asyncio.Future()

        self.broadcast.watch = watch
        self.addAsyncCleanup(self.close_broadcast)

    async def close_broadcast(self):
        self.broadcast.stop()
        await self.broadcast.cancel_tasks(tuple(viewer.task for viewer in self.broadcast.viewers.values()))

    def join(self):
        socket = object()
        self.broadcast.attach(socket, 1, "", bool)
        return socket

    async def test_last_viewer_starts_grace_then_broadcast_stops(self):
        first, last = self.join(), self.join()
        with patch("src.backend.broadcast.RECONNECT_GRACE_SECONDS", 0.02):
            await self.broadcast.leave(first)
            self.assertIsNone(self.broadcast.idle_stop)
            await self.broadcast.leave(last)
        self.assertEqual(self.broadcast.snapshot()["phase"], "preparing")
        await asyncio.wait_for(self.broadcast.stopping.wait(), 1)
        self.assertEqual(self.broadcast.snapshot()["phase"], "stopping")

    async def test_rejoin_cancels_grace_and_explicit_stop_ends_immediately(self):
        await self.broadcast.leave(self.join())
        first_deadline = self.broadcast.idle_stop
        viewer = self.join()
        self.assertTrue(first_deadline.cancelled())
        self.assertFalse(self.broadcast.stopping.is_set())
        await self.broadcast.leave(viewer)
        next_deadline = self.broadcast.idle_stop

        self.broadcast.stop()
        self.assertTrue(next_deadline.cancelled())
        self.assertTrue(self.broadcast.stopping.is_set())


    async def test_connected_viewer_keeps_its_task_when_signaling_reconnects(self):
        first = self.join()
        viewer = self.broadcast.viewer_for(first)
        viewer.connected = True
        replacement, other = object(), object()
        self.assertFalse(self.broadcast.resume(other, 1, viewer.token))
        await self.broadcast.detach(first)
        deadline = viewer.detach_timer
        self.assertIsNone(viewer.socket)
        self.assertIsNone(self.broadcast.idle_stop)
        self.assertFalse(viewer.task.done())
        self.assertFalse(self.broadcast.resume(replacement, 2, viewer.token))
        self.assertFalse(self.broadcast.resume(replacement, 1, "unknown"))
        self.assertTrue(self.broadcast.resume(replacement, 1, viewer.token))
        self.assertTrue(deadline.cancelled())
        self.assertIs(self.broadcast.viewer_for(replacement), viewer)
        self.assertFalse(self.broadcast.resume(other, 1, viewer.token))
        await self.broadcast.leave(first, 1, viewer.token)
        self.assertFalse(viewer.task.done())
        await self.broadcast.leave(replacement, 1)
        self.assertIsNotNone(self.broadcast.idle_stop)

    async def test_detached_viewer_expires_and_pending_viewer_is_canceled(self):
        pending = self.join()
        await self.broadcast.detach(pending)
        self.assertIsNone(self.broadcast.viewer_for(pending))
        first = self.join()
        viewer = self.broadcast.viewer_for(first)
        viewer.connected = True
        with patch("src.backend.broadcast.VIEWER_DETACH_SECONDS", 0.02):
            await self.broadcast.detach(first)
            await asyncio.gather(viewer.task, return_exceptions=True)
        self.assertTrue(viewer.task.cancelled())
        self.assertFalse(self.broadcast.resume(object(), 1, viewer.token))
        self.assertIsNotNone(self.broadcast.idle_stop)


class SharedSeekTest(unittest.IsolatedAsyncioTestCase):
    async def test_seek_only_updates_a_ready_finite_timeline(self):
        broadcast = Broadcast(BroadcastRoom(), "https://youtu.be/aqz-KE-bpKQ", PlaybackSettings())
        broadcast.phase = "playing"
        self.assertFalse(broadcast.seek(10))
        self.assertIsNone(broadcast.snapshot()["position"])
        self.assertEqual(broadcast.snapshot()["phase"], "playing")

        broadcast.duration = 60
        broadcast.seek_request = asyncio.get_running_loop().create_future()
        for invalid in (None, True, -1, 61, float("nan"), float("inf")):
            self.assertFalse(broadcast.seek(invalid))
        self.assertFalse(broadcast.seek_request.done())
        self.assertEqual(broadcast.snapshot()["seek_revision"], 0)
        self.assertIsNone(broadcast.snapshot()["seek_target"])

        broadcast.relay.position = 7
        self.assertTrue(broadcast.seek(60))
        self.assertFalse(broadcast.seek(10))
        self.assertEqual(broadcast.seek_request.result(), 59.9)
        state = broadcast.snapshot()
        self.assertEqual(state["phase"], "seeking")
        self.assertEqual(state["seek_revision"], 1)
        self.assertEqual(state["seek_target"], 60)
        self.assertEqual(state["position"], 7)



class InputRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.broadcast = Broadcast(BroadcastRoom(), "https://youtu.be/aqz-KE-bpKQ", PlaybackSettings())
        self.broadcast.connected.set()
        self.encoding = VIDEO_ENCODINGS[1]

    def source(self, *, live=False):
        return Source("video", "vp9", Input("https://example.test/video", "https"),
                      video_encoding=self.encoding,
                      metadata=InputMetadata(is_live=live, duration=None if live else 60))

    def media(self, source, *, position=None, error=None):
        return Mock(source=source, process=None, start=AsyncMock(), close=AsyncMock(),
                    wait_ready=AsyncMock(side_effect=error),
                    first_times={"video": position or 0, "audio": position or 0},
                    video_start=position or 0)

    async def test_stopping_a_paused_video_does_not_restart_media(self):
        before = self.media(self.source())
        paused = asyncio.Event()
        publish = self.broadcast.room.publish

        def observe_state():
            publish()
            if self.broadcast.snapshot()["phase"] == "paused":
                paused.set()

        async def forward(_media, _offset):
            self.broadcast.relay.position = 17
            self.broadcast.pause_playback()
            await asyncio.Future()

        self.broadcast.forward_segment = forward
        with (patch.object(self.broadcast.room, "publish", side_effect=observe_state),
              patch.object(self.broadcast, "open_input", new_callable=AsyncMock, return_value=(before, [])),
              patch("src.backend.broadcast.ProcessedMedia") as media):
            job = asyncio.create_task(self.broadcast.run(None, (self.encoding,), bool))
            self.addAsyncCleanup(self.broadcast.cancel_tasks, (job,))
            await asyncio.wait_for(paused.wait(), 1)
            self.assertEqual(self.broadcast.snapshot()["position"], 17)
            before.close.assert_awaited_once()
            self.broadcast.stop()
            await asyncio.wait_for(job, 1)

        media.assert_not_called()
        self.assertEqual(self.broadcast.room.outcome["kind"], "stopped")

    async def test_stop_waits_for_cleanup_already_started_by_pause(self):
        room = self.broadcast.room
        room.broadcast = self.broadcast
        media = ProcessedMedia(self.source(), None)
        media.process = Mock()
        media.process.poll.return_value = None
        media.ready.set()
        media.video_start = 0
        media.first_times = {"video": 0, "audio": 0}
        loop = asyncio.get_running_loop()
        joining = asyncio.Event()
        release = Event()

        def join_worker():
            loop.call_soon_threadsafe(joining.set)
            release.wait()

        async def forward(_media, _offset):
            self.broadcast.pause_playback()
            await asyncio.Future()

        media.worker = Mock(join=join_worker)
        self.broadcast.forward_segment = forward
        with patch.object(self.broadcast, "open_input", new=AsyncMock(return_value=(media, []))):
            job = asyncio.create_task(self.broadcast.run(None, (self.encoding,), bool))
            try:
                await asyncio.wait_for(joining.wait(), 1)
                self.broadcast.stop()
                await asyncio.wait({job}, timeout=0.02)
                self.assertIs(room.broadcast, self.broadcast)
            finally:
                release.set()
                await asyncio.wait_for(job, 1)

        self.assertIsNone(room.broadcast)
        self.assertEqual(room.outcome["kind"], "stopped")
        media.process.stdout.close.assert_called_once()
        media.process.stderr.close.assert_called_once()
        media.process.wait.assert_called_once()

    async def test_startup_403_retries_with_the_same_firefox_setting(self):
        self.broadcast.settings.use_browser_login = True
        source = self.source()
        ready = self.media(source), []
        with (patch("src.backend.broadcast.resolve_source", new_callable=AsyncMock, return_value=source) as resolve,
              patch("src.backend.broadcast.prepare_media", new_callable=AsyncMock,
                    side_effect=(FFmpegInputError(403), ready))):
            self.assertEqual(await self.broadcast.open_input(None, (self.encoding,), bool), ready)

        self.assertEqual([call.kwargs for call in resolve.await_args_list], [
            {"prefer_hls": None, "use_browser_login": True},
            {"prefer_hls": True, "use_browser_login": True},
        ])

    async def test_resumes_interrupted_vod_over_alternate_transport_with_same_encoder(self):
        self.broadcast.settings.use_browser_login = True
        source = self.source()
        refreshed = Source("video", "vp8", Input("https://example.test/refreshed", "m3u8"))
        before = self.media(source)
        after = self.media(refreshed, position=17)

        async def forward(media, timestamp_offset):
            if media is before:
                self.assertEqual(self.broadcast.snapshot()["stream"]["input"]["video_protocol"], "https")
                self.broadcast.relay.position = 17
                self.broadcast.relay.last_timestamp = 17
                raise FFmpegInputError(403)
            self.assertEqual(self.broadcast.relay.position, 17)
            self.assertEqual(self.broadcast.snapshot()["stream"]["input"]["video_protocol"], "m3u8")
            self.assertGreater(17 + timestamp_offset, 17)
            self.broadcast.relay.position = 59.9

        self.broadcast.forward_segment = forward
        with (patch("src.backend.broadcast.INPUT_RETRY_DELAYS", (0,)),
              patch("src.backend.broadcast.resolve_source", new_callable=AsyncMock, return_value=refreshed) as resolve,
              patch.object(self.broadcast, "open_input", new_callable=AsyncMock, return_value=(before, [])),
              patch("src.backend.broadcast.ProcessedMedia", return_value=after) as media):
            await self.broadcast.deliver(None, (self.encoding,), bool)

        args, kwargs = resolve.await_args
        self.assertEqual(kwargs, {"prefer_hls": True, "live_only": False, "use_browser_login": True})
        self.assertTrue(args[1]("vp8.0"))
        self.assertFalse(args[1]("avc1.42c01f"))
        self.assertEqual(args[2], (self.encoding,))
        resume_source = media.call_args.args[0]
        self.assertIs(resume_source.video_encoding, self.encoding)
        self.assertIs(resume_source.video, refreshed.video)
        self.assertEqual(media.call_args.kwargs, {"start_at": 17})
        before.close.assert_awaited_once()
        after.close.assert_awaited_once()

    async def test_repeated_server_errors_switch_recovery_transport_once(self):
        source = self.source()
        alternate = Source("video", "vp9", Input("https://example.test/refreshed", "m3u8"),
                           video_encoding=self.encoding, metadata=source.metadata)
        before = self.media(source)
        same_route = self.media(source, position=17, error=FFmpegInputError(503))
        other_route = self.media(alternate, position=17, error=FFmpegInputError(502))
        after = self.media(alternate, position=17)

        async def forward(media, _offset):
            if media is before:
                self.broadcast.relay.position = 17
                raise FFmpegInputError(503)
            self.assertIs(media, after)
            self.broadcast.relay.position = 59.9

        self.broadcast.forward_segment = forward
        with (patch("src.backend.broadcast.INPUT_RETRY_DELAYS", (0, 0, 0)),
              patch("src.backend.broadcast.resolve_source", new_callable=AsyncMock,
                    side_effect=(source, alternate, alternate)) as resolve,
              patch.object(self.broadcast, "open_input", new_callable=AsyncMock, return_value=(before, [])),
              patch("src.backend.broadcast.ProcessedMedia", side_effect=(same_route, other_route, after)) as media):
            await self.broadcast.deliver(None, (self.encoding,), bool)

        self.assertEqual([call.kwargs["prefer_hls"] for call in resolve.await_args_list], [False, True, True])
        self.assertEqual([call.kwargs["start_at"] for call in media.call_args_list], [17, 17, 17])
        same_route.close.assert_awaited_once()
        other_route.close.assert_awaited_once()
        after.close.assert_awaited_once()

    async def test_ffmpeg_readiness_timeout_closes_attempt_and_switches_transport(self):
        source = self.source()
        blocked = self.media(source)
        after = self.media(source)
        cancelled = asyncio.Event()

        async def wait_for_media(_ready):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        blocked.wait_ready.side_effect = wait_for_media
        with (patch("src.backend.broadcast.INPUT_RETRY_ATTEMPT_SECONDS", 0.03),
              patch("src.backend.broadcast.resolve_source", new_callable=AsyncMock, return_value=source) as resolve,
              patch("src.backend.broadcast.ProcessedMedia", side_effect=(blocked, after))):
            resumed = await self.broadcast.recover_input(
                source, None, 17, RetryableInputError("interrupted"), iter((0, 0)),
                deadline=asyncio.get_running_loop().time() + 1,
            )

        self.assertIs(resumed, after)
        self.assertTrue(cancelled.is_set())
        self.assertEqual([call.kwargs["prefer_hls"] for call in resolve.await_args_list], [False, True])
        blocked.close.assert_awaited_once()
        after.close.assert_not_awaited()

    async def test_recovery_deadline_interrupts_metadata_refresh_and_skips_later_backoff(self):
        source = self.source()
        cancelled = asyncio.Event()

        async def refresh(*_args, **_kwargs):
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        with (patch("src.backend.broadcast.INPUT_RETRY_ATTEMPT_SECONDS", 1),
              patch("src.backend.broadcast.resolve_source", new_callable=AsyncMock, side_effect=refresh) as resolve):
            deadline = asyncio.get_running_loop().time() + 0.05
            with self.assertRaisesRegex(RetryableInputError, "within the time limit"):
                await self.broadcast.recover_input(
                    source, None, 17, RetryableInputError("interrupted"), iter((0, 1, 2)), deadline,
                )

        self.assertTrue(cancelled.is_set())
        resolve.assert_awaited_once()

    async def test_retries_a_failed_seek_at_the_requested_position(self):
        source = self.source()
        before = self.media(source)
        failed = self.media(source, position=20, error=RetryableInputError("interrupted"))
        after = self.media(source, position=19)

        async def refresh(*_args, **_kwargs):
            state = self.broadcast.snapshot()
            self.assertEqual(state["phase"], "reconnecting")
            self.assertEqual(state["seek_revision"], 1)
            self.assertEqual(state["seek_target"], 20)
            self.assertEqual(state["position"], 7)
            return source

        async def forward(media, _):
            if media is before:
                self.broadcast.relay.position = 7
                self.broadcast.seek(20)
                await asyncio.Future()
            state = self.broadcast.snapshot()
            self.assertEqual(state["phase"], "playing")
            self.assertEqual(state["seek_revision"], 1)
            self.assertIsNone(state["seek_target"])
            self.assertEqual(state["position"], 19)
            self.broadcast.relay.position = 59.9

        self.broadcast.forward_segment = forward
        with (patch("src.backend.broadcast.INPUT_RETRY_DELAYS", (0,)),
              patch.object(self.broadcast, "refresh_input", side_effect=refresh),
              patch.object(self.broadcast, "open_input", new_callable=AsyncMock, return_value=(before, [])),
              patch("src.backend.broadcast.ProcessedMedia", side_effect=(failed, after)) as media):
            await self.broadcast.deliver(None, (self.encoding,), bool)

        self.assertEqual([call.kwargs["start_at"] for call in media.call_args_list], [20, 20])
        failed.close.assert_awaited_once()

    async def test_early_vod_eof_retries_but_normal_eof_finishes(self):
        for position, retries in ((30, 1), (59.9, 0)):
            with self.subTest(position=position):
                source = self.source()
                before = self.media(source)
                after = self.media(source, position=position)

                async def forward(media, _):
                    self.broadcast.relay.position = position if media is before else 60

                self.broadcast.forward_segment = forward
                with (patch("src.backend.broadcast.INPUT_RETRY_DELAYS", (0,)),
                      patch.object(self.broadcast, "refresh_input", new_callable=AsyncMock, return_value=source) as refresh,
                      patch.object(self.broadcast, "open_input", new_callable=AsyncMock, return_value=(before, [])),
                      patch("src.backend.broadcast.ProcessedMedia", return_value=after) as media):
                    await self.broadcast.deliver(None, (self.encoding,), bool)
                self.assertEqual(refresh.await_count, retries)
                self.assertEqual(media.call_count, retries)

    async def test_repeated_live_eof_does_not_reset_retry_budget(self):
        source = self.source(live=True)
        before = self.media(source)
        self.broadcast.forward_segment = AsyncMock()
        phases = []
        with (patch("src.backend.broadcast.INPUT_RETRY_DELAYS", (0, 0)),
              patch.object(self.broadcast.room, "publish", side_effect=lambda: phases.append(self.broadcast.phase)),
              patch.object(self.broadcast, "open_input", new_callable=AsyncMock, return_value=(before, [])),
              patch("src.backend.broadcast.resolve_source", new_callable=AsyncMock, return_value=source) as resolve,
              patch("src.backend.broadcast.ProcessedMedia", side_effect=lambda *_args, **_kwargs: self.media(source))):
            with self.assertRaisesRegex(RetryableInputError, "input ended early"):
                await self.broadcast.deliver(None, (self.encoding,), bool)

        self.assertEqual(resolve.await_count, 2)
        self.assertTrue(resolve.await_args.kwargs["live_only"])
        self.assertEqual(phases.count("reconnecting"), 2)

    async def test_live_eof_ends_when_refresh_confirms_the_broadcast_finished(self):
        source = self.source(live=True)
        self.broadcast.forward_segment = AsyncMock()
        with (patch("src.backend.broadcast.INPUT_RETRY_DELAYS", (0,)),
              patch.object(self.broadcast, "open_input", new_callable=AsyncMock, return_value=(self.media(source), [])),
              patch("src.backend.broadcast.resolve_source", new_callable=AsyncMock, return_value=None) as resolve):
            await self.broadcast.deliver(None, (self.encoding,), bool)
        self.assertTrue(resolve.await_args.kwargs["live_only"])

    async def test_stop_during_backoff_cancels_the_retry(self):
        source = self.source(live=True)
        self.broadcast.forward_segment = AsyncMock(side_effect=RetryableInputError("interrupted"))
        in_backoff = asyncio.Event()

        async def backoff(_delay):
            in_backoff.set()
            await asyncio.Future()

        with (patch("src.backend.broadcast.asyncio.sleep", new=backoff),
              patch.object(self.broadcast, "open_input", new_callable=AsyncMock, return_value=(self.media(source), [])),
              patch.object(self.broadcast, "refresh_input", new_callable=AsyncMock) as refresh):
            job = asyncio.create_task(self.broadcast.run(None, (self.encoding,), bool))
            await asyncio.wait_for(in_backoff.wait(), 1)
            self.broadcast.stop()
            await asyncio.wait_for(job, 1)

        refresh.assert_not_awaited()
        self.assertEqual(self.broadcast.room.outcome["kind"], "stopped")
        self.assertEqual(self.broadcast.room.outcome["message"], Message.BROADCAST_STOPPED.payload())

    async def test_unexpected_processing_failure_reports_when_it_occurred(self):
        for started, expected in ((False, Message.BROADCAST_START_FAILED), (True, Message.BROADCAST_CONTINUE_FAILED)):
            broadcast = Broadcast(BroadcastRoom(), "https://youtu.be/aqz-KE-bpKQ", PlaybackSettings())

            async def deliver(*_):
                if started:
                    broadcast.relay.start_segment(asyncio.get_running_loop().time(), 0, 0)
                raise RuntimeError("Unexpected processing failure")

            with (self.subTest(started=started), patch.object(broadcast, "deliver", new=deliver),
                  self.assertLogs("src.backend.server", level="ERROR")):
                await broadcast.run(None, (), bool)

            self.assertEqual(broadcast.room.outcome["kind"], "error")
            self.assertEqual(broadcast.room.outcome["message"], expected.payload())

    async def test_viewer_error_only_retries_transient_faults(self):
        for error, retryable, message in (
            (ViewerConnectionError(Message.WEBRTC_DISCONNECTED), True, Message.WEBRTC_DISCONNECTED),
            (TimeoutError(), True, Message.WEBRTC_TIMEOUT),
            (SourceError(Message.VIDEO_CODEC_UNSUPPORTED), False, Message.VIDEO_CODEC_UNSUPPORTED),
        ):
            with self.subTest(error=error), patch("src.backend.broadcast.connect_viewer", new_callable=AsyncMock, side_effect=error):
                ws = Mock(closed=False, send_json=AsyncMock())
                await self.broadcast.watch(Viewer(ws, 1), "", bool)
                response = ws.send_json.await_args.args[0]
                self.assertEqual(response["retryable"], retryable)
                self.assertEqual(response["message"], message.payload())
