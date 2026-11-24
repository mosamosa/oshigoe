function normalizeYoutubeUrl(value) {
  const match = /^https:\/\/(youtu\.be|(?:www\.|m\.)?youtube\.com)(\/[^?#]*)?(?:\?([^#]*))?(?:#.*)?$/i
    .exec(value.trim());
  if (!match) return null;

  const [, host, pathname = "", query] = match;
  const path = pathname.replace(/^\/+|\/+$/g, "").split("/");
  let videoId;
  if (host.toLowerCase() === "youtu.be") {
    if (path.length === 1) videoId = path[0];
  } else if (path.length === 1 && path[0] === "watch") {
    videoId = new URLSearchParams(query).getAll("v").find(Boolean);
  } else if (path.length === 2 && ["live", "shorts"].includes(path[0])) {
    videoId = path[1];
  }
  return videoId?.length === 11 && /^[A-Za-z0-9_-]+$/.test(videoId)
    ? "https://www.youtube.com/watch?v=" + videoId : null;
}

const twitchNonChannelPaths = new Set([
  "directory", "downloads", "inventory", "search", "settings", "subscriptions", "videos", "wallet",
]);

function normalizeTwitchUrl(value) {
  const match = /^https:\/\/(?:www\.|m\.)?twitch\.tv\/([^?#]*)(?:[?#].*)?$/i.exec(value.trim());
  if (!match) return null;
  const path = match[1].replace(/^\/+|\/+$/g, "").split("/");
  if (path.length === 1 && /^[a-z0-9_]{1,25}$/i.test(path[0])) {
    const channel = path[0].toLowerCase();
    if (!twitchNonChannelPaths.has(channel)) return "https://www.twitch.tv/" + channel;
  }
  if (path.length === 2 && path[0] === "videos" && /^[0-9]+$/.test(path[1])) {
    return "https://www.twitch.tv/videos/" + path[1];
  }
  return null;
}

const normalizers = [normalizeYoutubeUrl, normalizeTwitchUrl];

export function normalizeSourceUrl(value) {
  for (const normalize of normalizers) {
    const url = normalize(value);
    if (url) return url;
  }
  return null;
}
