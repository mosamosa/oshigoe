import { messageText, t } from "./i18n.mjs";

const ENCODERS = {
  h264_videotoolbox: "VideoToolbox",
  h264_qsv: "Intel Quick Sync",
  h264_nvenc: "NVIDIA NVENC",
  h264_amf: "AMD AMF",
};

const CODECS = {
  avc1: "H.264", avc3: "H.264", h264: "H.264", av01: "AV1", av1: "AV1",
  vp8: "VP8", vp9: "VP9", vp09: "VP9", hvc1: "HEVC", hev1: "HEVC",
  opus: "Opus", aac: "AAC",
};

function codecLabel(value) {
  if (!value || value === "none") return null;
  const name = value.toLowerCase().split("/").at(-1);
  if (name.startsWith("mp4a.40.")) return "AAC";
  return CODECS[name.split(".")[0]] ?? value;
}

function delta(value, old, field) {
  if (!Number.isFinite(value?.[field]) || !Number.isFinite(old?.[field])) return null;
  return value[field] - old[field];
}

function trackStatus(track, report, previous) {
  const last = track && previous?.get(track.id);
  const elapsed = delta(track, last, "timestamp");
  const bytes = delta(track, last, "bytesReceived");
  const received = delta(track, last, "packetsReceived");
  const lost = delta(track, last, "packetsLost");
  const frames = delta(track, last, "framesDecoded");
  const total = received + Math.max(0, lost);
  return {
    codec: codecLabel(report.get(track?.codecId)?.mimeType),
    bitrate: elapsed > 0 && bytes !== null && bytes >= 0 ? bytes * 8 / elapsed : null,
    loss: received !== null && received >= 0 && lost !== null && total > 0 ? Math.max(0, lost) / total * 100 : null,
    fps: track?.framesPerSecond ?? (elapsed > 0 && frames !== null && frames >= 0 ? frames * 1000 / elapsed : null),
  };
}

export function receiveStatus(report, previous) {
  const entries = [...report.values()];
  const inbound = kind => entries.find(entry => entry.type === "inbound-rtp" && (entry.kind ?? entry.mediaType) === kind);
  const video = inbound("video");
  const audio = inbound("audio");
  const transport = report.get(video?.transportId ?? audio?.transportId);
  const pair = report.get(transport?.selectedCandidatePairId) ??
    entries.find(entry => entry.type === "candidate-pair" && entry.nominated && entry.state === "succeeded");
  const playout = report.get(audio?.playoutId);
  const oldPlayout = playout && previous?.get(playout.id);
  const samples = delta(playout, oldPlayout, "totalSamplesCount");
  const playoutDelay = delta(playout, oldPlayout, "totalPlayoutDelay");
  const audioTime = audio?.estimatedPlayoutTimestamp;
  const videoTime = video?.estimatedPlayoutTimestamp;
  return {
    video: trackStatus(video, report, previous),
    audio: trackStatus(audio, report, previous),
    rtt: Number.isFinite(pair?.currentRoundTripTime) ? pair.currentRoundTripTime * 1000 : null,
    offset: audioTime > 0 && videoTime > 0 ? videoTime - audioTime : null,
    audioOutput: samples > 0 && playoutDelay !== null && playoutDelay >= 0 ? playoutDelay * 1000 / samples : null,
  };
}

function valueWithUnit(value, unit, digits = 0) {
  return Number.isFinite(value) ? `${value.toFixed(digits)} ${unit}` : null;
}

function resolution(width, height) {
  if (width > 0 && height > 0) return `${width}×${height}`;
  return height > 0 ? `${height}p` : null;
}

function protocol(value) {
  return ({m3u8: "HLS", m3u8_native: "HLS", https: "HTTPS", http: "HTTP", file: t("file")})[value] ?? value;
}

function join(...items) {
  return items.filter(Boolean).join(" · ");
}

function encoderName(name) {
  return name === "copy" ? t("copy") : ENCODERS[name] ?? name;
}

function bytesText(bytes) {
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

export function resourceText(metric, unit) {
  if (!Number.isFinite(metric?.value)) return null;
  const value = metric.value;
  return unit === "%" ? `${value.toFixed(1)}%` : bytesText(value);
}

function offsetDescription(offset) {
  if (!Number.isFinite(offset)) return null;
  const ms = Math.round(Math.abs(offset));
  return ms ? t(offset > 0 ? "audioLate" : "audioEarly", {ms}) : t("noOffset");
}

export class StreamStatus {
  constructor(details, summary, player) {
    this.details = details;
    this.summary = summary;
    this.player = player;
    this.toggle = details.querySelector("summary");
    this.rows = Object.fromEntries([...details.querySelectorAll("[data-field]")].map(row => [row.dataset.field, row]));
    this.connection = details.querySelector("[data-connection]");
    this.resourceSection = details.querySelector("[data-resources]");
    this.reset();
  }

  reset() {
    this.stop();
    this.details.hidden = true;
    this.resources = null;
    this.renderResources();
    this.stream = null;
    this.streamKey = null;
    this.previous = null;
  }

  stop() {
    clearTimeout(this.timer);
    this.peer = null;
    this.summary.hidden = true;
    this.stopped = true;
    if (this.stream) this.toggle.textContent = t("previousStatus");
  }

  show(stream) {
    this.stats = {};
    this.stopped = false;
    this.details.hidden = false;
    this.streamKey = null;
    this.updateStream(stream);
  }

  updateStream(stream) {
    const key = JSON.stringify(stream);
    if (key === this.streamKey) return;
    this.streamKey = key;
    this.stream = stream;
    this.updateLanguage();
  }

  updateLanguage() {
    this.renderResources();
    const stream = this.stream;
    this.toggle.textContent = t(this.stopped && stream ? "previousStatus" : "status");
    if (!stream) return;
    const input = stream.input;
    this.set("input-type", input.is_live == null ? null : t(input.is_live ? "live" : "onDemand"));
    this.set("input-video-protocol", protocol(input.video_protocol));
    this.set("input-audio-protocol", protocol(input.audio_protocol));
    this.set("input-video-codec", codecLabel(input.video_codec));
    this.set("input-resolution", resolution(input.width, input.height));
    this.set("input-fps", valueWithUnit(input.fps, "fps", Number.isInteger(input.fps) ? 0 : 2));
    this.set("input-audio-codec", codecLabel(input.audio_codec));
    this.set("input-sample-rate", input.audio_sample_rate && `${input.audio_sample_rate / 1000} kHz`);
    const encoder = stream.video.encoder;
    this.set("video-mode", t(encoder ? "reencode" : "copy"));
    this.set("video-encoder", encoder && encoderName(encoder));
    this.set("video-encoder-type", encoder && (stream.video.hardware ? t("hardware") : "CPU"));
    this.set("video-delay", valueWithUnit(stream.video.delay_ms, "ms"));
    this.set("video-fallback", stream.video.fallbacks.map(item =>
      `${encoderName(item.from)} → ${encoderName(item.to)}\n${messageText(item.reason)}`).join("\n\n"));
    this.set("audio-model", stream.audio.model);
    this.set("audio-device", stream.audio.device?.toUpperCase());
    this.set("video-target", valueWithUnit(stream.video.target_kbps, "kbps"));
    this.set("audio-target", valueWithUnit(stream.audio.target_kbps, "kbps"));
    this.render(this.stats);
  }

  updateResources(resources) {
    if (this.stopped) return;
    this.resources = resources;
    this.renderResources();
  }

  renderResources() {
    const resources = this.resources ?? {};
    const metrics = [
      ["isolation-load", resources.isolation_load_percent, "%"],
      ["cpu-usage", resources.cpu_percent, "%"],
      ["memory-usage", resources.memory_bytes, "B"],
      ["gpu-usage", resources.gpu_percent, "%"],
      ["gpu-memory-usage", resources.gpu_memory_bytes, "B"],
    ];
    let visible = false;
    for (const [key, metric, unit] of metrics) {
      const heading = this.rows[key].querySelector("dt");
      const label = t(heading.dataset.i18n);
      const scope = metric?.scope;
      heading.textContent = scope === "mac" ? t("macWide", {label})
        : scope === "pc" ? t("pcWide", {label}) : label;
      const value = resourceText(metric, unit);
      this.set(key, value);
      if (value !== null) visible = true;
    }
    this.resourceSection.hidden = !visible;
  }

  start(peer) {
    this.peer = peer;
    if (typeof peer.getStats === "function") void this.poll(peer);
  }

  async poll(peer) {
    try {
      const report = await peer.getStats();
      if (this.peer !== peer) return;
      this.render(receiveStatus(report, this.previous));
      this.previous = report;
    } catch {
      // Skip failed statistics samples. Poll again while the connection is active.
    } finally {
      if (this.peer === peer) this.timer = setTimeout(() => this.poll(peer), 1000);
    }
  }

  render(stats) {
    this.stats = stats;
    const {video, audio, rtt, offset, audioOutput} = stats;
    const dimensions = resolution(this.player.videoWidth, this.player.videoHeight);
    const fps = valueWithUnit(video?.fps, "fps");
    this.set("output-video-codec", video?.codec);
    this.set("output-resolution", dimensions);
    this.set("output-fps", fps);
    this.set("output-audio-codec", audio?.codec);
    this.set("output-sample-rate", `${this.stream.audio.sample_rate / 1000} kHz`);
    this.set("video-bitrate", valueWithUnit(video?.bitrate, "kbps"));
    this.set("audio-bitrate", valueWithUnit(audio?.bitrate, "kbps"));
    this.set("loss-video", valueWithUnit(video?.loss, "%", 1));
    this.set("loss-audio", valueWithUnit(audio?.loss, "%", 1));
    this.set("rtt", valueWithUnit(rtt, "ms", 1));
    this.set("offset", offsetDescription(offset));
    this.set("audio-output", audioOutput == null ? null : t("audioOutputEstimate", {ms: Math.round(audioOutput)}));
    this.connection.hidden = ![video?.loss, audio?.loss, rtt, offset, audioOutput].some(Number.isFinite);

    const encoder = this.stream.video.encoder;
    const processing = [video?.codec, encoder ? encoderName(encoder) : t("copy")].filter(Boolean).join(" / ");
    const summary = [join(dimensions, fps), processing, t("audioTargetSummary", {kbps: this.stream.audio.target_kbps})];
    this.summary.textContent = summary.filter(Boolean).join(" ｜ ");
    this.summary.hidden = this.stopped;
  }

  set(key, value) {
    const row = this.rows[key];
    row.querySelector("dd").textContent = value || "—";
    if (row.hasAttribute("data-optional")) row.hidden = !value;
  }
}
