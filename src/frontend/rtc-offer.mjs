const h264ReceiveLevels = [
  {level: 52, width: 4096, height: 2160, framerate: 60, bitrate: 240_000_000},
  {level: 51, width: 4096, height: 2160, framerate: 30, bitrate: 240_000_000},
  {level: 50, width: 2560, height: 1440, framerate: 30, bitrate: 135_000_000},
  {level: 42, width: 1920, height: 1080, framerate: 60, bitrate: 50_000_000},
  {level: 41, width: 1920, height: 1080, framerate: 30, bitrate: 50_000_000},
  {level: 40, width: 1920, height: 1080, framerate: 30, bitrate: 20_000_000},
  {level: 32, width: 1280, height: 720, framerate: 60, bitrate: 20_000_000}
];
const h264ReceiveChecks = new Map();

async function checkH264ReceiveLevel(profile) {
  for (const {level, ...video} of h264ReceiveLevels) {
    const profileLevelId = `${profile}${level.toString(16)}`;
    try {
      const checks = await Promise.all([
        navigator.mediaCapabilities.decodingInfo({type: "webrtc", video: {
          ...video, contentType: `video/H264;profile-level-id=${profileLevelId};packetization-mode=1`
        }}),
        navigator.mediaCapabilities.decodingInfo({type: "file", video: {
          ...video, contentType: `video/mp4; codecs="avc1.${profileLevelId}"`
        }})
      ]);
      if (checks.every(({supported, smooth}) => supported && smooth)) return level;
    } catch {
      return 0;
    }
  }
  return 0;
}

export async function offerWithH264ReceiveLevels(sdp) {
  if (typeof navigator.mediaCapabilities?.decodingInfo !== "function") return sdp;
  const sections = await Promise.all(sdp.split(/(?=^m=)/m).map(async section => {
    if (!section.startsWith("m=video ")) return section;
    const h264Payloads = new Set(Array.from(section.matchAll(/^a=rtpmap:(\d+) H264\/90000$/gim), match => match[1]));
    const newline = section.includes("\r\n") ? "\r\n" : "\n";
    const lines = await Promise.all(section.split(newline).map(async line => {
      const fmtp = /^a=fmtp:(\d+) (.+)$/.exec(line);
      if (!fmtp || !h264Payloads.has(fmtp[1])) return line;
      const parameters = new Map(fmtp[2].split(";").map(value => value.trim().split("=")));
      const profileId = parameters.get("profile-level-id");
      if (parameters.get("packetization-mode") !== "1" || parameters.get("level-asymmetry-allowed") !== "1" ||
          parameters.has("max-recv-level") || !/^[\da-f]{6}$/i.test(profileId)) return line;

      const profile = profileId.slice(0, 4).toLowerCase();
      if (!h264ReceiveChecks.has(profile)) h264ReceiveChecks.set(profile, checkH264ReceiveLevel(profile));
      const level = await h264ReceiveChecks.get(profile);
      return level > parseInt(profileId.slice(4), 16)
        ? `${line};max-recv-level=${profile.slice(2)}${level.toString(16)}`
        : line;
    }));
    return lines.join(newline);
  }));
  return sections.join("");
}
