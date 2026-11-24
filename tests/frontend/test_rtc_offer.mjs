import assert from "node:assert/strict";
import test from "node:test";
import { offerWithH264ReceiveLevels } from "../../src/frontend/rtc-offer.mjs";

const offer = [
  "v=0",
  "m=audio 9 UDP/TLS/RTP/SAVPF 111",
  "a=rtpmap:111 opus/48000/2",
  "m=video 9 UDP/TLS/RTP/SAVPF 96",
  "a=rtpmap:96 H264/90000",
  "a=fmtp:96 profile-level-id=42e01f;packetization-mode=1;level-asymmetry-allowed=1",
  "",
].join("\n");

test("only adds a supported higher H.264 receive level to an eligible offer", async t => {
  const originalNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  Object.defineProperty(globalThis, "navigator", {configurable: true, value: {}});
  t.after(() => {
    if (originalNavigator) Object.defineProperty(globalThis, "navigator", originalNavigator);
    else delete globalThis.navigator;
  });
  assert.equal(await offerWithH264ReceiveLevels(offer), offer);

  const decodingInfo = t.mock.fn(async () => ({supported: true, smooth: true}));
  navigator.mediaCapabilities = {decodingInfo};
  const extended = offer.replace("level-asymmetry-allowed=1", "level-asymmetry-allowed=1;max-recv-level=e034");
  assert.equal(await offerWithH264ReceiveLevels(offer), extended);
  assert.equal(await offerWithH264ReceiveLevels(offer.replaceAll("\n", "\r\n")), extended.replaceAll("\n", "\r\n"));
  assert.equal(decodingInfo.mock.callCount(), 2);

  for (const unchanged of [
    offer.replace("packetization-mode=1", "packetization-mode=0"),
    offer.replace("level-asymmetry-allowed=1", "level-asymmetry-allowed=0"),
    offer.replace("42e01f", "42e034"),
    offer.replace("packetization-mode=1", "packetization-mode=1;max-recv-level=e02a"),
  ]) {
    assert.equal(await offerWithH264ReceiveLevels(unchanged), unchanged);
  }
});
