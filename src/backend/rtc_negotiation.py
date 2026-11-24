from dataclasses import replace

from aiortc import RTCRtpSender, sdp
from aiortc.codecs import CODECS

from .media.media_input import SourceError
from .server_messages import Message


def register_h264_profiles():
    video = CODECS["video"]
    baseline = next(codec for codec in video if codec.mimeType == "video/H264")
    profiles = {codec.parameters.get("profile-level-id") for codec in video}
    payload_type = max(codec.payloadType for codec in video) + 1
    for profile in ("4d001f", "64001f", "640c1f"):
        if profile not in profiles:
            params = {**baseline.parameters, "profile-level-id": profile}
            video.append(replace(baseline, parameters=params, payloadType=payload_type))
            payload_type += 1


def h264_format(profile_id):
    try:
        if len(profile_id) == 6:
            return sdp.parse_h264_profile_level_id(profile_id)
    except ValueError:
        pass
    return None


def parse_offer(offer):
    if not isinstance(offer, str):
        raise SourceError(Message.INVALID_WEBRTC_OFFER)
    try:
        description = sdp.SessionDescription.parse(offer)
    except (ValueError, IndexError, AssertionError) as error:
        raise SourceError(Message.INVALID_WEBRTC_OFFER) from error

    if sorted(track.kind for track in description.media) != ["audio", "video"]:
        raise SourceError(Message.MISSING_WEBRTC_TRACKS)
    if any(track.direction not in {"recvonly", "sendrecv"} for track in description.media):
        raise SourceError(Message.WEBRTC_RECEIVE_REQUIRED)
    remote = {track.kind: track.rtp.codecs for track in description.media}
    if not any(codec.mimeType.lower() == "audio/opus" for codec in remote["audio"]):
        raise SourceError(Message.OPUS_UNSUPPORTED)

    local_video = RTCRtpSender.getCapabilities("video").codecs
    vp8 = next(codec for codec in local_video if codec.mimeType == "video/VP8")
    vp8_offered = any(codec.mimeType.lower() == "video/vp8" for codec in remote["video"])
    h264_local = {}
    for codec in local_video:
        if codec.mimeType == "video/H264":
            profile, _ = h264_format(codec.parameters["profile-level-id"])
            h264_local[profile] = codec
    h264_levels = {}
    for codec in remote["video"]:
        if codec.mimeType.lower() != "video/h264" or str(codec.parameters.get("packetization-mode")) != "1":
            continue
        profile_id = str(codec.parameters.get("profile-level-id", "42e01f"))
        fmt = h264_format(profile_id)
        if fmt:
            profile, level = fmt
            if str(codec.parameters.get("level-asymmetry-allowed")) == "1":
                receive_level = h264_format(profile_id[:2] + str(codec.parameters.get("max-recv-level", "")))
                if receive_level and receive_level[0] == profile:
                    level = max(level, receive_level[1])
            if profile not in h264_levels or level > h264_levels[profile]:
                h264_levels[profile] = level

    def codec_for(name):
        if name in {"vp8", "vp8.0"} and vp8_offered:
            return vp8
        if name.startswith(("avc1.", "avc3.")):
            fmt = h264_format(name.split(".", 1)[1])
            if fmt:
                profile, level = fmt
                if profile in h264_levels and level <= h264_levels[profile]:
                    return h264_local.get(profile)
        return None

    return codec_for
