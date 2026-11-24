from enum import StrEnum


class Message(StrEnum):
    INVALID_VOLUME = "Choose a volume between -30 and 0 dB or select mute."
    MISSING_MIX = "Set the volume for both voice and background sound."
    INVALID_QUALITY = "The video quality settings are invalid."
    INVALID_VIDEO_DELAY = "Enter a whole number from 0 to 500 milliseconds."
    INVALID_BROWSER_LOGIN = "Choose whether to use your Firefox sign-in."
    INVALID_RESOLUTION = "Choose a resolution limit."
    INVALID_FRAME_RATE = "Choose a frame rate limit."
    INVALID_VIDEO_BITRATE = "Enter a video bitrate from 100 to 12000 kbps."
    INVALID_AUDIO_BITRATE = "Choose an audio bitrate."
    INVALID_SOURCE_URL = "Enter an HTTPS URL for a YouTube video or live stream, a Twitch channel, or a Twitch archive."
    MISSING_SOURCE_URL = "Enter a YouTube or Twitch URL."
    NO_PLAYABLE_FORMAT = (
        "No compatible video and audio formats are available to copy, "
        "and the app cannot re-encode them for this browser."
    )
    NO_SOURCE_MEDIA = "Could not get video and audio from the source."
    METADATA_TIMEOUT = "Retrieving video information timed out."
    FIREFOX_LOOKUP_FAILED = (
        "Couldn't get the video details. Check the URL, make sure the video is available, "
        "and confirm that you're signed in to the video site in your browser."
    )
    SOURCE_LOOKUP_FAILED = "Couldn't get the video details. Check the link and make sure the video is available."
    FFMPEG_HTTP_ERROR = "FFmpeg could not get data from the source (HTTP {status})."
    VIDEO_REORDERING = "H.264 video with B-frames must be re-encoded for uninterrupted playback."
    MEDIA_STALLED = "Video or audio stopped arriving."
    MEDIA_TIMEOUT = "Loading video and audio timed out."
    NO_MEDIA_RECEIVED = "Could not receive video and audio."
    FFMPEG_READ_FAILED = "FFmpeg could not read video or audio."
    MEDIA_PROCESSING_FAILED = "An error occurred while processing video or audio."
    CODEC_MISMATCH = "The video codec does not match the codec negotiated for WebRTC."
    HARDWARE_H264_MISSING = "The hardware encoder did not produce a compatible H.264 stream."
    HARDWARE_H264_LEVEL = "This browser does not support the H.264 level produced by the hardware encoder."
    KEYFRAME_MISSING = "Could not receive both a video keyframe and audio."
    INVALID_WEBRTC_OFFER = "The WebRTC offer is invalid."
    MISSING_WEBRTC_TRACKS = "The WebRTC offer must include both video and audio tracks."
    WEBRTC_RECEIVE_REQUIRED = "Allow the browser to receive video and audio."
    OPUS_UNSUPPORTED = "This browser cannot receive Opus audio."
    VIDEO_CODEC_UNSUPPORTED = "This browser does not support the current stream's video codec."
    WEBRTC_CONNECT_FAILED = "Could not connect over WebRTC."
    WEBRTC_DISCONNECTED = "The WebRTC connection was lost."
    WEBRTC_TIMEOUT = "The WebRTC connection timed out."
    WEBRTC_START_FAILED = "Could not start the WebRTC connection."
    INPUT_RECOVERY_DEADLINE = "Could not reconnect to the source within the time limit."
    INPUT_RECOVERY_TIMEOUT = "The connection to the source could not be restored in time."
    INPUT_ENDED_EARLY = "The media input ended early."
    BROADCAST_STOPPED = "Processing stopped."
    BROADCAST_ENDED = "Playback ended."
    BROADCAST_START_FAILED = "Could not start processing."
    BROADCAST_CONTINUE_FAILED = "Could not continue processing."
    BROADCAST_GONE = "Playback for this video or live stream has ended."
    ALREADY_WATCHING = "This browser is already watching the stream."
    BROADCAST_BUSY = "The app is processing another video or live stream."

    def payload(self, **values):
        return {"key": self.name.lower(), "values": values, "text": self.value.format(**values)}


class MessageError(Exception):
    def __init__(self, message, **values):
        self.message = message_payload(message, **values)
        super().__init__(self.message["text"])


class SettingsError(MessageError, ValueError):
    pass


def message_payload(message, **values):
    if isinstance(message, Message):
        return message.payload(**values)
    if isinstance(message, MessageError):
        return message.message
    return {"text": str(message)}
