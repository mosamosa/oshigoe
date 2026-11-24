import io
import logging
import re
import subprocess
import sys
from dataclasses import dataclass, replace

import av
from aiortc.sdp import parse_h264_profile_level_id

from ..settings import DEFAULT_VIDEO_SHORT_EDGE, VideoQuality, video_short_edge


LOG = logging.getLogger(__name__)
H264_PROFILE = "avc1.42e01f"
H264_BITSTREAM_FILTER = "h264_mp4toannexb,dump_extra=freq=keyframe"
# Each SDR tier lists two target bitrates in kbps, one for 30 fps or less and one for higher frame rates.
SDR_BITRATES = (
    (144, 150, 250),
    (240, 400, 600),
    (360, 1000, 1500),
    (480, 2500, 4000),
    (720, 5000, 7500),
    (1080, 8000, 12000),
    (1440, 16000, 24000),
    (2160, 35000, 53000),
    (4320, 80000, 120000),
)


@dataclass(frozen=True)
class VideoEncoding:
    codec: str
    encoder: str
    options: tuple[str, ...]
    max_short_edge: int = 0
    max_fps: int = 0
    hardware: bool = False
    pixel_format: str = "yuv420p"
    device_args: tuple[str, ...] = ()


VIDEO_ENCODINGS = (
    VideoEncoding(H264_PROFILE, "libx264", (
        "-preset", "ultrafast", "-tune", "zerolatency", "-profile:v", "baseline",
        "-level:v", "3.1", "-bf", "0", "-x264-params", "repeat-headers=1",
    ), max_short_edge=720, max_fps=30),
    VideoEncoding("vp8", "libvpx", (
        "-deadline", "realtime", "-cpu-used", "8", "-lag-in-frames", "0", "-auto-alt-ref", "0",
        "-error-resilient", "1",
    )),
)


def hardware_h264(encoder, options, *, pixel_format="nv12", device_args=()):
    return VideoEncoding(
        H264_PROFILE, encoder, (*options, "-bf", "0"),
        hardware=True, pixel_format=pixel_format, device_args=device_args,
    )


HARDWARE_ENCODINGS = {
    "darwin": (hardware_h264("h264_videotoolbox", (
        "-allow_sw", "0", "-realtime", "1", "-prio_speed", "1",
        "-profile:v", "constrained_baseline",
    )),),
    "win32": (
        hardware_h264("h264_qsv", (
            "-async_depth", "1", "-look_ahead", "0", "-cavlc", "1", "-profile:v", "baseline",
        ), pixel_format="qsv", device_args=(
            "-init_hw_device", "qsv=focus:hw,child_device_type=d3d11va", "-filter_hw_device", "focus",
        )),
        hardware_h264("h264_nvenc", (
            "-preset", "p1", "-tune", "ull", "-zerolatency", "1", "-rc-lookahead", "0", "-delay", "0",
            "-profile:v", "baseline",
        )),
        hardware_h264("h264_amf", (
            "-usage", "ultralowlatency", "-profile:v", "constrained_baseline",
        )),
    ),
}


def h264_keyframe_codec(data):
    units = {nal[0] & 31: nal for nal in re.split(rb"\x00\x00(?:\x00)?\x01", data)[1:] if nal}
    sps = units.get(7, b"")
    if len(sps) < 4 or 8 not in units or 5 not in units:
        return None
    return "avc1." + sps[1:4].hex()


def h264_matches(actual, expected, *, allow_higher_level=False):
    if actual is None:
        return False
    try:
        profile, level = parse_h264_profile_level_id(actual[5:])
        expected_profile, expected_level = parse_h264_profile_level_id(expected[5:])
    except ValueError:
        return False
    return profile == expected_profile and (allow_higher_level or level <= expected_level)


def encoding_size_limits(encoding, quality):
    limits = (quality.height, encoding.max_short_edge)
    short_limit = min((limit for limit in limits if limit), default=0)
    return short_limit, short_limit * 16 // 9


def encoded_short_edge(encoding, quality, *, height=None, width=None):
    short_edge = video_short_edge(width, height)
    if not short_edge:
        return 0
    short_limit, long_limit = encoding_size_limits(encoding, quality)
    if short_limit:
        long_edge = max(width or 0, height)
        scaled_edge = int(short_edge * long_limit / long_edge + 0.5)
        short_edge = min(short_edge, short_limit, scaled_edge)
    return int(short_edge) // 2 * 2


def encoding_fps_limit(encoding, quality):
    rates = (30 if quality.fps is None else quality.fps, encoding.max_fps)
    return min((limit for limit in rates if limit), default=0)


def video_bitrate_kbps(encoding, quality, *, height=None, width=None, fps=None):
    if encoding is None:
        return None
    if quality.bitrate_kbps is not None:
        return quality.bitrate_kbps

    short_limit, _ = encoding_size_limits(encoding, quality)
    output_edge = encoded_short_edge(encoding, quality, height=height, width=width)
    output_edge = output_edge or short_limit or DEFAULT_VIDEO_SHORT_EDGE
    fps_limit = encoding_fps_limit(encoding, quality)
    output_fps = fps or fps_limit or 30
    if fps_limit:
        output_fps = min(output_fps, fps_limit)
    _, standard, high = next((tier for tier in SDR_BITRATES if output_edge <= tier[0]), SDR_BITRATES[-1])
    return high if output_fps > 30 else standard


def select_encoder(encodings, codec_for, quality, *, height=None, width=None):
    compatible = (encoding for encoding in encodings if codec_for(encoding.codec))
    fps = 30 if quality.fps is None else quality.fps

    def priority(encoding):
        fits_fps = not encoding.max_fps or 0 < fps <= encoding.max_fps
        return encoded_short_edge(encoding, quality, height=height, width=width), encoding.hardware, fits_fps

    return max(compatible, key=priority, default=None)


def video_encode_args(encoding, quality, *, height=None, width=None, fps=None):
    bitrate = video_bitrate_kbps(encoding, quality, height=height, width=width, fps=fps)
    short_limit, long_limit = encoding_size_limits(encoding, quality)
    fps_limit = encoding_fps_limit(encoding, quality)
    scale = "scale=w=trunc(iw/2)*2:h=trunc(ih/2)*2"
    if short_limit:
        scale = (f"scale=w='min(iw,if(lt(iw,ih),{short_limit},{long_limit}))'"
                 f":h='min(ih,if(lt(iw,ih),{long_limit},{short_limit}))'"
                 ":force_original_aspect_ratio=decrease:force_divisible_by=2")
    if encoding.pixel_format == "qsv":
        scale += ",format=nv12,hwupload=extra_hw_frames=16"
    args = ["-c:v", encoding.encoder, *encoding.options,
            "-b:v", f"{bitrate}k", "-maxrate", f"{bitrate}k", "-bufsize", f"{bitrate // 5}k",
            "-vf", scale, "-pix_fmt", encoding.pixel_format]
    if encoding.hardware:
        args += ["-bsf:v", H264_BITSTREAM_FILTER]
    if fps_limit:
        args += ["-fpsmax", str(fps_limit)]
    return args + ["-g", str(fps_limit or 30)]


def probe_hardware(encoding):
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *encoding.device_args,
               "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30", "-frames:v", "5", "-an",
               *video_encode_args(encoding, VideoQuality(height=720, bitrate_kbps=1800)),
               "-f", "nut", "pipe:1"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=5, check=True)
        with av.open(io.BytesIO(result.stdout), format="nut") as container:
            codec = next((h264_keyframe_codec(bytes(packet)) for packet in container.demux(video=0)
                          if packet.is_keyframe and packet.size), None)
        if codec:
            parse_h264_profile_level_id(codec[5:])
            return replace(encoding, codec=codec)
    except (OSError, subprocess.SubprocessError, av.FFmpegError, ValueError, IndexError):
        pass
    LOG.debug("Hardware encoder unavailable: %s", encoding.encoder)
    return None


def available_video_encodings():
    result = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                            capture_output=True, text=True, errors="replace", timeout=30, check=True)
    encoders = set(re.findall(r"^\s*V\S*\s+(\S+)", result.stdout, re.MULTILINE))
    hardware = []
    for candidate in HARDWARE_ENCODINGS.get(sys.platform, ()):
        if candidate.encoder in encoders:
            encoding = probe_hardware(candidate)
            if encoding is not None:
                LOG.info("Hardware encoder available: %s (%s)", encoding.encoder, encoding.codec)
                hardware.append(encoding)
    return (*hardware, *(encoding for encoding in VIDEO_ENCODINGS if encoding.encoder in encoders))
