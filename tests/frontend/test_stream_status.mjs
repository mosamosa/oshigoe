import assert from "node:assert/strict";
import test from "node:test";
import { setLanguage } from "../../src/frontend/i18n.mjs";
import { StreamStatus, receiveStatus, resourceText } from "../../src/frontend/stream-status.mjs";

function report(...entries) {
  return new Map(entries.map(entry => [entry.id, entry]));
}

test("reports received rates, losses, timing and the selected transport", () => {
  const oldVideo = {id: "v", type: "inbound-rtp", kind: "video", timestamp: 1000,
    bytesReceived: 1000, packetsReceived: 100, packetsLost: 2, framesDecoded: 30};
  const oldAudio = {id: "a", type: "inbound-rtp", kind: "audio", timestamp: 1000,
    bytesReceived: 1000, packetsReceived: 100, packetsLost: 0};
  const oldPlayout = {id: "p", totalSamplesCount: 48000, totalPlayoutDelay: 4600};
  const stats = report(
    {...oldVideo, timestamp: 2000, bytesReceived: 126000, packetsReceived: 198, packetsLost: 4,
      framesDecoded: 60, codecId: "vc", estimatedPlayoutTimestamp: 4000000034, transportId: "t"},
    {...oldAudio, timestamp: 2000, bytesReceived: 11000, packetsReceived: 150, playoutId: "p",
      codecId: "ac", estimatedPlayoutTimestamp: 4000000000},
    {id: "vc", mimeType: "video/H264"}, {id: "ac", mimeType: "audio/opus"},
    {id: "t", selectedCandidatePairId: "r"}, {id: "r", currentRoundTripTime: 0.007},
    {id: "p", totalSamplesCount: 96000, totalPlayoutDelay: 6520},
  );
  const result = receiveStatus(stats, report(oldVideo, oldAudio, oldPlayout));
  assert.deepEqual(result.video, {codec: "H.264", bitrate: 1000, loss: 2, fps: 30});
  assert.deepEqual(result.audio, {codec: "Opus", bitrate: 80, loss: 0, fps: null});
  assert.equal(result.rtt, 7);
  assert.equal(result.offset, 34);
  assert.equal(result.audioOutput, 40);
});

test("missing counters stay unavailable until the browser reports a usable interval", () => {
  const result = receiveStatus(report({id: "v", type: "inbound-rtp", kind: "video", timestamp: 1000,
    bytesReceived: 1234, estimatedPlayoutTimestamp: 4000000000}));
  assert.equal(result.video.bitrate, null);
  assert.equal(result.audio.loss, null);
  assert.equal(result.rtt, null);
  assert.equal(result.offset, null);
  assert.equal(result.audioOutput, null);
});

test("formats resource values without the measurement scope", () => {
  assert.equal(resourceText({value: 3.14, scope: "app"}, "%"), "3.1%");
  assert.equal(resourceText({value: 42.44, scope: "pc"}, "%"), "42.4%");
  assert.equal(resourceText({value: 0, scope: "app"}, "%"), "0.0%");
  assert.equal(resourceText({value: 1.6 * 1024 ** 3, scope: "app"}, "B"), "1.60 GB");
  assert.equal(resourceText({value: 300 * 1024 ** 2, scope: "mac"}, "B"), "300 MB");
  assert.equal(resourceText(null, "%"), null);
});

test("shows the GPU measurement scope in the heading and updates it on language changes", t => {
  globalThis.document = {documentElement: {}, querySelectorAll: () => []};
  t.after(() => {
    setLanguage("en");
    delete globalThis.document;
  });
  const status = Object.create(StreamStatus.prototype);
  status.toggle = {};
  status.resourceSection = {};
  status.rows = {};
  for (const [field, label] of [
    ["isolation-load", "isolationLoad"], ["cpu-usage", "cpuUsage"], ["memory-usage", "memoryUsage"],
    ["gpu-usage", "gpuUsage"], ["gpu-memory-usage", "gpuMemoryUsage"],
  ]) {
    const cells = {dt: {dataset: {i18n: label}}, dd: {}};
    status.rows[field] = {querySelector: name => cells[name], hasAttribute: () => true};
  }
  const gpu = status.rows["gpu-usage"];
  status.updateResources({gpu_percent: {value: 42.44, scope: "mac"}});
  assert.equal(gpu.querySelector("dt").textContent, "GPU usage (whole Mac)");

  setLanguage("ja");
  status.updateLanguage();
  assert.equal(gpu.querySelector("dt").textContent, "GPU 使用率 (Mac全体)");
  assert.equal(gpu.querySelector("dd").textContent, "42.4%");

  status.updateResources({gpu_percent: {value: 42.44, scope: "pc"}});
  assert.equal(gpu.querySelector("dt").textContent, "GPU 使用率 (PC全体)");
  status.updateResources({gpu_percent: {value: 0, scope: "app"}});
  assert.equal(gpu.querySelector("dt").textContent, "GPU 使用率");
  assert.equal(gpu.querySelector("dd").textContent, "0.0%");
  status.updateResources(null);
  assert.equal(gpu.hidden, true);
});
