import re
from urllib.parse import parse_qs, urlsplit

from . import yt_dlp_input
from ..media.media_input import SourceError
from ..server_messages import Message


WEB_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
HOSTS = WEB_HOSTS | {"youtu.be"}


def normalize_url(value):
    try:
        url = urlsplit(value.strip())
        host = url.hostname
        path = url.path.strip("/").split("/")
        video_id = ""
        if url.scheme == "https" and not url.username and not url.password and url.port is None:
            if host == "youtu.be" and len(path) == 1:
                video_id = path[0]
            if host in WEB_HOSTS:
                if path == ["watch"]:
                    video_id = parse_qs(url.query).get("v", [""])[0]
                if len(path) == 2 and path[0] in {"live", "shorts"}:
                    video_id = path[1]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
    except ValueError:
        pass
    raise SourceError(Message.INVALID_SOURCE_URL)


async def resolve(url, codec_for, encodings, quality, **options):
    return await yt_dlp_input.resolve_source(
        url, codec_for, encodings, quality, fallback_title="YouTube", **options,
    )
