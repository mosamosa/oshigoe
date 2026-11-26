import asyncio
import ipaddress
import logging
import shutil
from asyncio import proactor_events
from functools import partial
from pathlib import Path

import ifaddr
from aiohttp import WSMsgType, web

from .bootstrap_runtime import parse_server_args
from .broadcast import VOICE_MODEL, BroadcastRoom, SignalState
from .media.audio_isolation import AudioIsolation
from .media.media_input import SourceError
from .media.video_encoding import available_video_encodings
from .install_lock import server_installation
from .resource_usage import ResourceMonitor
from .rtc_negotiation import register_h264_profiles
from .server_messages import message_payload


LOG = logging.getLogger(__name__)
FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
PAGE = FRONTEND / "index.html"
STYLESHEET = FRONTEND / "styles.css"
BROWSER_MODULES = (
    "app.mjs",
    "broadcast-controls.mjs",
    "i18n.mjs",
    "playback.mjs",
    "rtc-offer.mjs",
    "settings-panel.mjs",
    "signaling.mjs",
    "stream-status.mjs",
    "video-sites.mjs",
)
MODEL = web.AppKey("isolator", AudioIsolation)
ENCODINGS = web.AppKey("video encodings", tuple)
ROOM = web.AppKey("broadcast room", BroadcastRoom)
RESOURCE_INTERVAL = 2


async def signal(request):
    address, port = request.transport.get_extra_info("sockname")[:2]
    hosts = {f"{address}:{port}"}
    if ipaddress.ip_address(address).is_loopback:
        hosts.add(f"localhost:{port}")
    if port == 80:
        hosts |= {host.rsplit(":", 1)[0] for host in hosts}
    if request.host not in hosts or request.headers.get("Origin") != f"http://{request.host}":
        raise web.HTTPForbidden()

    ws = web.WebSocketResponse(heartbeat=20, max_msg_size=65_536)
    await ws.prepare(request)
    room = request.app[ROOM]
    state = SignalState()
    room.sockets[ws] = state
    state.changed.set()
    states = asyncio.create_task(room.send_states(ws, state))
    try:
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            try:
                data = message.json()
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            kind = data.get("type")
            request_id = data.get("request_id")
            broadcast = room.broadcast
            try:
                if kind == "offer" and type(request_id) is int and request_id > 0:
                    room.offer(ws, data, request.app[MODEL], request.app[ENCODINGS])
                elif kind == "resume" and type(request_id) is int and request_id > 0:
                    broadcast_id = data.get("broadcast_id")
                    resumed = (broadcast is not None and type(broadcast_id) is int and broadcast_id == broadcast.id
                               and broadcast.resume(ws, request_id, data.get("viewer_token")))
                    await ws.send_json({"type": "resumed" if resumed else "resume_error", "request_id": request_id})
                elif kind == "mix":
                    room.set_mix(data, state)
                elif broadcast is not None and kind == "leave" and type(request_id) is int:
                    token = data.get("viewer_token")
                    if token is None or (isinstance(token, str) and data.get("broadcast_id") == broadcast.id):
                        await broadcast.leave(ws, request_id, token)
                elif broadcast is not None and kind == "stop":
                    broadcast_id = data.get("broadcast_id")
                    if type(broadcast_id) is int and broadcast_id == broadcast.id:
                        broadcast.stop()
                elif broadcast is not None and kind in ("pause_playback", "resume_playback"):
                    broadcast_id = data.get("broadcast_id")
                    if type(broadcast_id) is int and broadcast_id == broadcast.id:
                        if kind == "pause_playback":
                            broadcast.pause_playback()
                        else:
                            broadcast.resume_playback()
                elif kind == "seek" and type(request_id) is int and request_id > 0:
                    broadcast_id = data.get("broadcast_id")
                    accepted = (broadcast is not None and type(broadcast_id) is int and broadcast_id == broadcast.id
                                and broadcast.seek(data.get("position")))
                    if not accepted:
                        await ws.send_json({"type": "seek_rejected", "request_id": request_id})
            except (ValueError, SourceError) as error:
                if kind == "offer":
                    await ws.send_json({"type": "error", "request_id": request_id, "message": message_payload(error)})
    except ConnectionError:
        pass
    finally:
        room.sockets.pop(ws)
        states.cancel()
        if room.broadcast is not None:
            await room.broadcast.detach(ws)
        await asyncio.gather(states, return_exceptions=True)
        await ws.close()
    return ws


def page_file(path, content_type=None):
    # Browsers revalidate every page file, so an updated server never runs with a cached older file.
    headers = {"Cache-Control": "no-cache"}
    if content_type:
        headers["Content-Type"] = content_type
    return web.FileResponse(path, headers=headers)


async def index(_):
    return page_file(PAGE)


async def stylesheet(_):
    return page_file(STYLESHEET, "text/css")


async def browser_module(_, name):
    return page_file(FRONTEND / name, "text/javascript")


async def close_sockets(app):
    room = app[ROOM]
    broadcast = room.broadcast
    if broadcast is not None:
        broadcast.stop()
    await asyncio.gather(*(ws.close(code=1001) for ws in tuple(room.sockets)))
    if broadcast is not None:
        await broadcast.job


def finish_datagram_close_after_pending_send():
    """Make a proactor datagram transport report connection_lost when close() waits for a send.

    CPython defers connection_lost until the in-flight send completes, but the write loop then
    returns early because the transport is closing, so connection_lost never runs. aioice waits
    for it while closing a WebRTC peer, which kept a stopping broadcast from finishing.
    """
    transport = proactor_events._ProactorDatagramTransport
    loop_writing = transport._loop_writing
    if getattr(loop_writing, "finishes_close", False):
        return

    def finish_close(self, fut=None):
        if fut is not None and fut is self._write_fut and self._closing:
            self._write_fut = None
            if not fut.cancelled():
                fut.exception()
            self._loop.call_soon(self._call_connection_lost, None)
            return
        loop_writing(self, fut)

    finish_close.finishes_close = True
    transport._loop_writing = finish_close


def is_reset_during_close(context):
    """Match the Windows proactor shutting down a socket the peer already reset."""
    callback = getattr(context.get("handle"), "_callback", None)
    return (isinstance(context.get("exception"), ConnectionResetError)
            and getattr(callback, "__name__", None) == "_call_connection_lost")


async def ignore_resets_during_close(_):
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()

    def handle(loop, context):
        if is_reset_during_close(context):
            return
        if previous is None:
            loop.default_exception_handler(context)
        else:
            previous(loop, context)

    loop.set_exception_handler(handle)
    try:
        yield
    finally:
        loop.set_exception_handler(previous)


async def manage_audio_isolation(app):
    isolator = app[MODEL]
    loop = asyncio.get_running_loop()
    isolator.on_mode_change = lambda: loop.call_soon_threadsafe(app[ROOM].publish)
    isolator.start()
    try:
        yield
    finally:
        isolator.on_mode_change = None
        await asyncio.to_thread(isolator.close)


async def sample_resources(app):
    monitor = await asyncio.to_thread(ResourceMonitor, app[MODEL])
    room = app[ROOM]

    async def refresh():
        while True:
            room.resources = await asyncio.to_thread(monitor.sample)
            room.publish()
            await asyncio.sleep(RESOURCE_INTERVAL)

    task = asyncio.create_task(refresh())
    try:
        yield
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def create_app(isolator):
    register_h264_profiles()
    app = web.Application()
    app[MODEL] = isolator
    app[ROOM] = BroadcastRoom()
    app[ENCODINGS] = available_video_encodings()
    app.router.add_get("/", index)
    app.router.add_get("/styles.css", stylesheet)
    for name in BROWSER_MODULES:
        app.router.add_get(f"/{name}", partial(browser_module, name=name))
    app.router.add_get("/signal", signal)
    app.on_shutdown.append(close_sockets)
    app.cleanup_ctx.append(ignore_resets_during_close)
    if isinstance(isolator, AudioIsolation):
        app.cleanup_ctx.append(manage_audio_isolation)
        app.cleanup_ctx.append(sample_resources)
    return app


def print_listening_urls(host: ipaddress.IPv4Address, port: int) -> None:
    addresses = {host}
    if host.is_unspecified:
        addresses = {ipaddress.IPv4Address("127.0.0.1")}
        try:
            addresses.update(
                ipaddress.IPv4Address(address.ip)
                for adapter in ifaddr.get_adapters()
                for address in adapter.ips
                if address.is_IPv4 and address.ip != "0.0.0.0"
            )
        except OSError as error:
            LOG.warning("Could not list network interfaces (%s)", error)

    urls = [f"  http://{address}:{port}/"
            for address in sorted(addresses, key=lambda item: (not item.is_loopback, item))]
    print("Listening URLs", *urls, "Press Ctrl+C to stop.", sep="\n", flush=True)


def serve(args):
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg was not found on PATH.")
    logging.basicConfig(level=logging.INFO)
    finish_datagram_close_after_pending_send()
    isolator = AudioIsolation(VOICE_MODEL, None if args.device == "auto" else args.device)
    web.run_app(create_app(isolator), host=str(args.host), port=args.port,
                print=lambda _: print_listening_urls(args.host, args.port))


def main():
    args = parse_server_args()
    with server_installation(FRONTEND.parents[1], args.port):
        serve(args)


if __name__ == "__main__":
    main()
