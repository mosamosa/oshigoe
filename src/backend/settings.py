from dataclasses import dataclass
from math import log10

from .server_messages import Message, SettingsError


DEFAULT_VIDEO_SHORT_EDGE = 1080


def video_short_edge(width, height):
    return min(width, height) if width and height else height or 0


def gain_from_db(value):
    if value is None:
        return 0.0
    if type(value) not in {int, float} or not -30 <= value <= 0:
        raise SettingsError(Message.INVALID_VOLUME)
    return 10 ** (value / 20)


@dataclass(frozen=True)
class Mix:
    voice: float = 1.0
    background: float = gain_from_db(-6)

    @classmethod
    def from_message(cls, data):
        if not isinstance(data, dict) or not {"voice_db", "background_db"} <= data.keys():
            raise SettingsError(Message.MISSING_MIX)
        return cls(gain_from_db(data["voice_db"]), gain_from_db(data["background_db"]))

    def to_message(self):
        return {
            "voice_db": round(20 * log10(self.voice), 6) if self.voice else None,
            "background_db": round(20 * log10(self.background), 6) if self.background else None,
        }


@dataclass(frozen=True)
class VideoQuality:
    height: int = DEFAULT_VIDEO_SHORT_EDGE  # This limits the video's shorter edge.
    fps: int | None = None
    bitrate_kbps: int | None = None

    def allows_copy(self, short_edge, fps):
        return (
            self.bitrate_kbps is None
            and (not self.height or 0 < short_edge <= self.height)
            and (not self.fps or 0 < fps <= self.fps)
        )


@dataclass
class PlaybackSettings:
    mix: Mix = Mix()
    video: VideoQuality = VideoQuality()
    audio_kbps: int = 96
    use_browser_login: bool = False
    video_delay_ms: int = 0

    @classmethod
    def from_message(cls, data):
        quality = data.get("quality", {})
        if not isinstance(quality, dict):
            raise SettingsError(Message.INVALID_QUALITY)
        height = quality.get("height", DEFAULT_VIDEO_SHORT_EDGE)
        fps = quality.get("fps")
        video_kbps = quality.get("video_kbps")
        audio_kbps = quality.get("audio_kbps", 96)
        use_browser_login = data.get("use_browser_login", False)
        video_delay_ms = data.get("video_delay_ms", 0)
        if type(video_delay_ms) is not int or not 0 <= video_delay_ms <= 500:
            raise SettingsError(Message.INVALID_VIDEO_DELAY)
        if type(use_browser_login) is not bool:
            raise SettingsError(Message.INVALID_BROWSER_LOGIN)
        if type(height) is not int or height not in {0, 360, 480, 720, 1080}:
            raise SettingsError(Message.INVALID_RESOLUTION)
        if fps is not None and (type(fps) is not int or fps not in {0, 24, 30, 60}):
            raise SettingsError(Message.INVALID_FRAME_RATE)
        if video_kbps is not None and (type(video_kbps) is not int or not 100 <= video_kbps <= 12_000):
            raise SettingsError(Message.INVALID_VIDEO_BITRATE)
        if type(audio_kbps) is not int or audio_kbps not in {48, 64, 96, 128}:
            raise SettingsError(Message.INVALID_AUDIO_BITRATE)
        mix = Mix.from_message(data["mix"]) if "mix" in data else Mix()
        return cls(mix=mix, video=VideoQuality(height, fps, video_kbps), audio_kbps=audio_kbps,
                   use_browser_login=use_browser_login, video_delay_ms=video_delay_ms)
