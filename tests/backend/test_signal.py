import asyncio
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer

from src.backend.broadcast import Broadcast, BroadcastRoom, SignalState
from src.backend.server import (MODEL, ROOM, create_app, finish_datagram_close_after_pending_send,
                                ignore_resets_during_close, sample_resources)


class SignalTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch("src.backend.server.available_video_encodings", return_value=()):
            app = create_app(None)
        self.client = await self.enterAsyncContext(TestClient(TestServer(app)))
        self.origin = str(self.client.make_url("/").origin())

    async def test_origin_must_match_the_request_host(self):
        with self.assertRaises(WSServerHandshakeError) as error:
            await self.client.ws_connect("/signal", origin="http://localhost:1")
        self.assertEqual(error.exception.status, 403)

    async def test_page_loads_the_stylesheet_and_app_module(self):
        async with self.client.get("/") as page:
            self.assertEqual(page.status, 200)
            html = await page.text()
            self.assertIn('rel="stylesheet" href="/styles.css"', html)
            self.assertIn('<script type="module" src="/app.mjs"></script>', html)
        async with self.client.get("/styles.css") as stylesheet:
            self.assertEqual(stylesheet.status, 200)
            self.assertEqual(stylesheet.content_type, "text/css")

    async def test_page_files_are_revalidated_so_updates_load_together(self):
        pending = ["/", "/styles.css", "/app.mjs"]
        visited = set()
        while pending:
            path = pending.pop()
            if path in visited:
                continue
            visited.add(path)
            async with self.client.get(path) as response:
                self.assertEqual(response.status, 200, path)
                self.assertEqual(response.headers.get("Cache-Control"), "no-cache", path)
                etag = response.headers["ETag"]
                if path.endswith(".mjs"):
                    self.assertEqual(response.content_type, "text/javascript", path)
                    pending.extend(re.findall(r'\bfrom "(/[^"]+\.mjs)"', await response.text()))
                else:
                    await response.read()
            async with self.client.get(path, headers={"If-None-Match": etag}) as revalidated:
                self.assertEqual(revalidated.status, 304, path)
        self.assertIn("/rtc-offer.mjs", visited)

    async def test_invalid_mix_is_acknowledged_without_changing_the_mix(self):
        async with self.client.ws_connect("/signal", origin=self.origin) as socket:
            state = await socket.receive_json(timeout=2)
            self.assertEqual(state["type"], "state")
            await socket.send_json({"type": "mix", "request_id": 7, "level": "voice_db", "db": 1000})
            self.assertEqual(await socket.receive_json(timeout=2), {**state, "mix_ack": 7})

    async def test_resume_and_seek_echo_the_request_id_when_no_broadcast_exists(self):
        async with self.client.ws_connect("/signal", origin=self.origin) as socket:
            await socket.receive_json(timeout=2)
            await socket.send_json({"type": "resume", "request_id": 8, "broadcast_id": 1, "viewer_token": "expired"})
            self.assertEqual(await socket.receive_json(timeout=2), {"type": "resume_error", "request_id": 8})
            await socket.send_json({"type": "seek", "request_id": 9, "broadcast_id": 1, "position": 0})
            self.assertEqual(await socket.receive_json(timeout=2), {"type": "seek_rejected", "request_id": 9})

    async def test_reconnected_socket_resumes_the_same_viewer(self):
        viewer_finished = asyncio.Event()
        socket_detached = asyncio.Event()
        detach = Broadcast.detach

        async def connect(broadcast, viewer, _offer, _codec_for):
            viewer.connected = True
            try:
                await viewer.socket.send_json({"type": "accepted", "request_id": viewer.request_id,
                                               "broadcast_id": broadcast.id, "viewer_token": viewer.token})
                await asyncio.Future()
            finally:
                viewer_finished.set()

        async def observe_detach(broadcast, socket):
            await detach(broadcast, socket)
            socket_detached.set()

        async def wait_for_media(*_):
            await asyncio.Future()

        with (patch("src.backend.broadcast.connect_viewer", new=connect),
              patch("src.backend.broadcast.parse_offer", return_value=bool),
              patch.object(Broadcast, "deliver", new=wait_for_media),
              patch.object(Broadcast, "detach", new=observe_detach)):
            async with self.client.ws_connect("/signal", origin=self.origin) as socket:
                await socket.receive_json(timeout=2)
                await socket.send_json({"type": "offer", "request_id": 1, "sdp": "offer",
                                        "url": "https://youtu.be/dQw4w9WgXcQ"})
                async with asyncio.timeout(2):
                    while (response := await socket.receive_json())["type"] == "state":
                        pass
                self.assertEqual(response["type"], "accepted")
            await asyncio.wait_for(socket_detached.wait(), 2)
            self.assertFalse(viewer_finished.is_set())

            async with self.client.ws_connect("/signal", origin=self.origin) as socket:
                await socket.receive_json(timeout=2)
                await socket.send_json({"type": "resume", "request_id": 1, "broadcast_id": response["broadcast_id"],
                                        "viewer_token": response["viewer_token"]})
                self.assertEqual(await socket.receive_json(timeout=2), {"type": "resumed", "request_id": 1})
                self.assertFalse(viewer_finished.is_set())
                await socket.send_json({"type": "stop", "broadcast_id": response["broadcast_id"]})
                await asyncio.wait_for(viewer_finished.wait(), 2)


class ResourceRefreshTest(unittest.IsolatedAsyncioTestCase):
    async def test_each_resource_sample_is_published_to_connected_browsers(self):
        samples = iter(({"cpu_percent": {"value": value, "scope": "app"}} for value in range(100)))
        room = BroadcastRoom()
        state = SignalState()
        room.sockets[object()] = state
        app = {MODEL: None, ROOM: room}
        monitor = SimpleNamespace(sample=lambda: next(samples))
        with patch("src.backend.server.ResourceMonitor", return_value=monitor), \
                patch("src.backend.server.RESOURCE_INTERVAL", 0.01):
            refresh = sample_resources(app)
            await anext(refresh)
            seen = []
            for _ in range(2):
                await asyncio.wait_for(state.changed.wait(), 1)
                state.changed.clear()
                seen.append(room.resources["cpu_percent"]["value"])
            await anext(refresh, None)
        self.assertEqual(len(seen), 2)
        self.assertLess(seen[0], seen[1])


class ConnectionResetLogTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_resets_while_closing_a_transport_are_not_logged(self):
        loop = asyncio.get_running_loop()

        def _call_connection_lost(_):
            pass

        def other_callback(_):
            pass

        def report(callback, error):
            loop.call_exception_handler({"message": "Exception in callback", "exception": error,
                                         "handle": asyncio.Handle(callback, (None,), loop)})

        cleanup = ignore_resets_during_close(None)
        await anext(cleanup)
        try:
            with self.assertNoLogs("asyncio", "ERROR"):
                report(_call_connection_lost, ConnectionResetError(10054, "reset"))
            with self.assertLogs("asyncio", "ERROR"):
                report(other_callback, ConnectionResetError(10054, "reset"))
            with self.assertLogs("asyncio", "ERROR"):
                report(_call_connection_lost, OSError("other failure"))
        finally:
            await anext(cleanup, None)
        self.assertIsNone(loop.get_exception_handler())


class DatagramCloseTest(unittest.IsolatedAsyncioTestCase):
    async def test_closing_with_a_send_in_flight_reports_connection_lost(self):
        finish_datagram_close_after_pending_send()
        loop = asyncio.get_running_loop()
        lost = loop.create_future()

        class Sender(asyncio.DatagramProtocol):
            def connection_lost(self, exc):
                lost.set_result(exc)

        receiver, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0))
        sender, _ = await loop.create_datagram_endpoint(Sender, local_addr=("127.0.0.1", 0))
        try:
            sender.sendto(b"bye", receiver.get_extra_info("sockname"))
            sender.close()
            self.assertIsNone(await asyncio.wait_for(lost, 1))
        finally:
            receiver.close()
