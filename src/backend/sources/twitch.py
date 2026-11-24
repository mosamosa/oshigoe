import re
from urllib.parse import urlsplit

from . import yt_dlp_input
from ..media.media_input import SourceError
from ..server_messages import Message


HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}
NON_CHANNEL_PATHS = {"directory", "downloads", "inventory", "search", "settings", "subscriptions", "videos", "wallet"}


def normalize_url(value):
    try:
        url = urlsplit(value.strip())
        if url.scheme == "https" and url.netloc.lower() in HOSTS:
            path = url.path.strip("/").split("/")
            if len(path) == 1 and re.fullmatch(r"[A-Za-z0-9_]{1,25}", path[0]):
                channel = path[0].lower()
                if channel not in NON_CHANNEL_PATHS:
                    return f"https://www.twitch.tv/{channel}"
            if len(path) == 2 and path[0] == "videos" and re.fullmatch(r"[0-9]+", path[1]):
                return f"https://www.twitch.tv/videos/{path[1]}"
    except ValueError:
        pass
    raise SourceError(Message.INVALID_SOURCE_URL)


async def resolve(url, codec_for, encodings, quality, **options):
    return await yt_dlp_input.resolve_source(
        url, codec_for, encodings, quality, fallback_title="Twitch", **options,
    )
