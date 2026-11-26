import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { normalizeSourceUrl, youtubeLiveChatUrl } from "../../src/frontend/video-sites.mjs";

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

test("opens the YouTube live chat in the browser only during YouTube live streams", () => {
  const live = {stream: {input: {is_live: true}}};
  assert.equal(youtubeLiveChatUrl({...live, url: "https://www.youtube.com/watch?v=otP8jeHxXMY"}),
    "https://www.youtube.com/live_chat?is_popout=1&noapp=1&v=otP8jeHxXMY");
  for (const broadcast of [
    {url: "https://www.youtube.com/watch?v=otP8jeHxXMY", stream: {input: {is_live: false}}},
    {url: "https://www.youtube.com/watch?v=otP8jeHxXMY", stream: null},
    {...live, url: "https://www.twitch.tv/example"},
    null,
  ]) {
    assert.equal(youtubeLiveChatUrl(broadcast), null, JSON.stringify(broadcast));
  }
});
