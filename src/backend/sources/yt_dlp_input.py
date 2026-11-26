import asyncio
import json
import math
import sys

from ..media.media_input import HLS_PROTOCOLS, Input, InputMetadata, RetryableInputError, Source, SourceError
from ..server_messages import Message
from ..settings import VideoQuality, video_short_edge
from ..media.video_encoding import encoded_short_edge, select_encoder


def choose_source(info, codec_for, encodings=(), quality=VideoQuality(), *, prefer_hls=None, fallback_title="Video"):
    def usable(fmt):
        return (
            fmt.get("protocol") in {"https", "http"} | HLS_PROTOCOLS
            and str(fmt.get("url", "")).startswith(("https://", "http://"))
            and not fmt.get("has_drm")
        )

    def audio_present(fmt):
        return fmt.get("acodec") not in {None, "none"}

    def positive_number(fmt, key):
        value = fmt.get(key)
        return value if type(value) in (int, float) and value > 0 and math.isfinite(value) else None

    formats = [fmt for fmt in info.get("formats", ()) if usable(fmt)]
    live = info.get("is_live", False)
    audio_only = [
        fmt for fmt in formats
        if fmt.get("vcodec") == "none"
        and (audio_present(fmt) or (fmt["protocol"] in HLS_PROTOCOLS and fmt.get("acodec") is None))
    ]
    candidates = []
    for index, video in enumerate(formats):
        codec = video.get("vcodec") or ""
        if codec in {"", "none"}:
            continue
        hls = video["protocol"] in HLS_PROTOCOLS
        muxed = audio_present(video)
        audio = None
        if not muxed:
            audio = next((fmt for fmt in reversed(audio_only)
                          if (fmt["protocol"] in HLS_PROTOCOLS) == hls), None)
            if audio is None:
                continue
        height = positive_number(video, "height")
        width = positive_number(video, "width")
        short_edge = video_short_edge(width, height)
        copy = bool(codec_for(codec)) and quality.allows_copy(short_edge, positive_number(video, "fps") or 0)
        encoding = None
        if not copy:
            encoding = select_encoder(encodings, codec_for, quality, height=height, width=width)
            if encoding is None:
                continue
        output_edge = encoded_short_edge(encoding, quality, height=height, width=width) if encoding else short_edge
        within_target = not quality.height or short_edge <= quality.height
        ranked_edge = short_edge if within_target else -short_edge
        priority = (hls == prefer_hls, short_edge > 0, ranked_edge, output_edge, encoding is None,
                    bool(encoding and encoding.hardware), live and hls and muxed, index)
        candidates.append((priority, video, audio, encoding))

    if not candidates:
        if not any(codec_for(encoding.codec) for encoding in encodings):
            raise SourceError(Message.NO_PLAYABLE_FORMAT)
        raise SourceError(Message.NO_SOURCE_MEDIA)

    def make_input(fmt):
        headers = {**(info.get("http_headers") or {}), **(fmt.get("http_headers") or {})}
        return Input(fmt["url"], fmt["protocol"], headers)

    _, video, audio, encoding = max(candidates, key=lambda candidate: candidate[0])
    audio_format = audio or video
    metadata = InputMetadata(
        is_live=bool(live), width=positive_number(video, "width"), height=positive_number(video, "height"),
        fps=positive_number(video, "fps"), audio_codec=audio_format.get("acodec"),
        audio_sample_rate=positive_number(audio_format, "asr"), duration=None if live else positive_number(info, "duration"),
    )
    # yt-dlp appends the extraction time to the title of a live stream; fulltitle keeps the original.
    title = info.get("fulltitle") or info.get("title") or fallback_title
    return Source(title, video["vcodec"], make_input(video),
                  make_input(audio) if audio else None, encoding, quality, metadata)


async def resolve_source(url, codec_for, encodings=(), quality=VideoQuality(), *,
                         prefer_hls=None, live_only=False, use_browser_login=False, fallback_title="Video"):
    args = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-playlist", "--no-warnings",
            "--socket-timeout", "10", "--retries", "1", "--extractor-retries", "1",
            "--dump-single-json", "--skip-download"]
    if use_browser_login:
        args += ["--cookies-from-browser", "firefox"]
    process = await asyncio.create_subprocess_exec(
        *args, "--", url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(45):
            output, _ = await process.communicate()
    except TimeoutError as error:
        raise RetryableInputError(Message.METADATA_TIMEOUT) from error
    finally:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.communicate()
    if process.returncode:
        if use_browser_login:
            raise SourceError(Message.FIREFOX_LOOKUP_FAILED)
        raise SourceError(Message.SOURCE_LOOKUP_FAILED)
    info = json.loads(output)
    if live_only and not info.get("is_live"):
        return None
    return choose_source(info, codec_for, encodings, quality, prefer_hls=prefer_hls, fallback_title=fallback_title)
