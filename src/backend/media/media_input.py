from dataclasses import dataclass, field

from ..server_messages import MessageError
from ..settings import VideoQuality
from .audio_isolation import PACKET_SAMPLES
from .video_encoding import H264_BITSTREAM_FILTER, VideoEncoding, video_encode_args


HLS_PROTOCOLS = {"m3u8", "m3u8_native"}
# FFmpeg's HTTP reader drains the rest of the response before a seek when the remaining bytes are
# within this threshold. Without an explicit value, HTTPS on Windows treats every response as
# within it and reads up to the seek position. Use avio's default read-ahead threshold instead.
HTTP_SHORT_SEEK_BYTES = 32768


class SourceError(MessageError):
    pass


class RetryableInputError(SourceError):
    pass


@dataclass(frozen=True)
class Input:
    url: str
    protocol: str = "file"
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def hls(self):
        return self.protocol in HLS_PROTOCOLS


@dataclass(frozen=True)
class InputMetadata:
    is_live: bool | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    audio_codec: str | None = None
    audio_sample_rate: int | None = None
    duration: float | None = None


@dataclass(frozen=True)
class Source:
    title: str
    codec: str
    video: Input
    audio: Input | None = None
    video_encoding: VideoEncoding | None = None
    quality: VideoQuality = VideoQuality()
    metadata: InputMetadata = InputMetadata()

    @property
    def output_codec(self):
        return self.video_encoding.codec if self.video_encoding else self.codec


def _ffmpeg_input(media, start_at, *, accurate=True):
    args = []
    if media.protocol != "file":
        args += ["-rw_timeout", "15000000", "-protocol_whitelist", "http,https,tcp,tls,crypto"]
    if media.hls:
        args += ["-live_start_index", "-1"]
    elif media.protocol != "file":
        args += ["-short_seek_size", str(HTTP_SHORT_SEEK_BYTES)]
    headers = "".join(f"{key}: {value}\r\n" for key, value in media.headers.items()
                      if key.lower() in {"user-agent", "referer", "origin"}
                      and "\r" not in value and "\n" not in value)
    if headers:
        args += ["-headers", headers]
    if start_at is not None:
        if not accurate:
            args += ["-noaccurate_seek"]
        args += ["-ss", f"{start_at:.6f}"]
    return args + ["-i", media.url]


def _copy_video_args(source):
    args = ["-c:v", "copy"]
    if source.codec.startswith(("avc1.", "avc3.")):
        bitstream_filter = H264_BITSTREAM_FILTER
        if source.video.hls:
            # NUT requires presentation timestamps even when FFmpeg estimates decoding timestamps.
            bitstream_filter += r",setts=pts=if(eq(PTS\,NOPTS)\,DTS\,PTS)"
        args += ["-bsf:v", bitstream_filter]
    return args


def ffmpeg_keyframe_command(source, start_at):
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-copyts", "-start_at_zero",
        *_ffmpeg_input(source.video, start_at), "-map", "0:v:0", *_copy_video_args(source),
        "-frames:v", "1", "-f", "nut", "pipe:1",
    ]


def ffmpeg_command(source, start_at=None, *, audio_start=None):
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
    encoding = source.video_encoding
    if start_at is not None:
        command += ["-copyts", "-start_at_zero"]
    if encoding:
        command += encoding.device_args
    command += _ffmpeg_input(source.video, start_at, accurate=encoding is not None or source.audio is not None)
    if source.audio is not None:
        command += _ffmpeg_input(source.audio, start_at if audio_start is None else audio_start)
    command += ["-map", "0:v:0", "-map", "1:a:0" if source.audio else "0:a:0"]
    if encoding:
        metadata = source.metadata
        command += video_encode_args(encoding, source.quality, height=metadata.height,
                                     width=metadata.width, fps=metadata.fps)
    else:
        command += _copy_video_args(source)
    resample = "aresample=48000:async=1"
    if start_at is None:
        resample += ":first_pts=0"
    return command + [
        "-af", f"{resample},asetnsamples=n={PACKET_SAMPLES}:p=0",
        "-ac", "1", "-c:a", "pcm_f32le",
        "-map_metadata", "-1", "-max_interleave_delta", "80000",
        "-flush_packets", "1", "-f", "nut", "pipe:1",
    ]
