import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { normalizeSourceUrl } from "../../src/frontend/video-sites.mjs";

test("normalizes Twitch channel and archive URLs", () => {
  assert.equal(normalizeSourceUrl("https://m.twitch.tv/Example_Channel/?share=1"), "https://www.twitch.tv/example_channel");
  assert.equal(normalizeSourceUrl("https://twitch.tv/videos/123456789?t=1h"), "https://www.twitch.tv/videos/123456789");
});

test("rejects malformed and unsupported Twitch URLs", () => {
  for (const url of [
    "http://twitch.tv/example", "https://twitch.tv.example.test/example", "https://user@twitch.tv/example",
    "https://twitch.tv:443/example", "https://twitch.tv/videos", "https://twitch.tv/videos/no",
    "https://twitch.tv/directory", "https://twitch.tv/example/videos",
  ]) {
    assert.equal(normalizeSourceUrl(url), null, url);
  }
});

test("normalizes the shared source URL cases", () => {
  const cases = JSON.parse(readFileSync(new URL("../source-urls.json", import.meta.url), "utf8"));
  for (const {url, frontend} of cases) {
    assert.equal(normalizeSourceUrl(url), frontend, url);
  }
});
