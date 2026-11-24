from urllib.parse import urlsplit

from . import twitch, youtube
from ..media.media_input import SourceError
from ..server_messages import Message
from ..settings import VideoQuality


SITES_BY_HOST = {host: site for site in (youtube, twitch) for host in site.HOSTS}


def _site_for(url):
    try:
        host = urlsplit(url.strip()).hostname
    except ValueError:
        host = None
    site = SITES_BY_HOST.get(host)
    if site is None:
        raise SourceError(Message.INVALID_SOURCE_URL)
    return site


def normalize_url(url):
    return _site_for(url).normalize_url(url)


async def resolve_source(url, codec_for, encodings=(), quality=VideoQuality(), **options):
    site = _site_for(url)
    return await site.resolve(site.normalize_url(url), codec_for, encodings, quality, **options)
