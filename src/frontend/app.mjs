import { initialLanguage, messageText, saveLanguage, setLanguage, t } from "/i18n.mjs";
import { normalizeSourceUrl } from "/video-sites.mjs";
import { StreamStatus } from "/stream-status.mjs";
import { createSettingsPanel } from "/settings-panel.mjs";
import { createTimelineControls, createVolumeControls } from "/broadcast-controls.mjs";
import { createSignaling } from "/signaling.mjs";
import { createPlayback } from "/playback.mjs";

const form = document.querySelector("form");
const languagePicker = document.querySelector("#language");
const input = document.querySelector("#url");
const clearUrl = document.querySelector("#clear-url");
const player = document.querySelector("video");
const playView = document.querySelector("#play-view");
const title = document.querySelector("#video-title");
const status = document.querySelector("#status");
const sourceNotice = document.querySelector("#source-notice");
const broadcastStatus = document.querySelector("#broadcast-status");
const start = document.querySelector("#start");
const stop = document.querySelector("#stop");
const playerEmpty = document.querySelector("#player-empty");
const streamStatus = new StreamStatus(document.querySelector("#stream-details"),
  document.querySelector("#stream-summary"), player);
const urlKey = "oshigoe.url";
const phaseLabels = {
  preparing: "starting",
  playing: "streaming",
  pausing: "pausingSharedPlayback",
  paused: "paused",
  resuming: "resumingSharedPlayback",
  seeking: "seeking",
  reconnecting: "reconnecting",
  stopping: "stopping"
};

const shared = {broadcast: null, outcome: null};
let broadcastMessage = "connecting";
let serverMessage = "serverConnecting";
let sourceMessage = "";
let dismissedOutcomeId = null;

const signal = createSignaling({
  onState: showSharedState,
  onMessage(response) {
    if (response.type === "seek_rejected") timeline.reject(response.request_id);
    else void playback.receive(response);
  },
  onConnecting() {
    if (serverMessage !== "serverConnectFailed") {
      serverMessage = "serverConnecting";
      showBroadcastStatus("connecting", "busy");
      renderSourceNotice();
    }
    renderControls();
  },
  onDisconnected() {
    playback.serverDisconnected();
    serverMessage = "serverConnectFailed";
    showBroadcastStatus("disconnected", "idle");
    renderSourceNotice();
    renderControls();
  }
});
const playback = createPlayback({
  player, playView, streamStatus, shared, signal,
  renderControls,
  renderNotice: renderPlaybackNotice,
  showTitle(value) { title.textContent = value; },
  showSourceError(message) {
    sourceMessage = message;
    renderSourceNotice();
  },
  showConnectionError() {
    serverMessage = "serverConnectFailed";
    renderSourceNotice();
  }
});
const settings = createSettingsPanel(() => {
  if (sourceMessage !== "invalidVideoDelay") return;
  sourceMessage = "";
  renderSourceNotice();
});
const volume = createVolumeControls(signal);
const timeline = createTimelineControls(signal, shared);

function showBroadcastStatus(message, state) {
  broadcastMessage = message;
  const text = messageText(message);
  if (text !== broadcastStatus.textContent) broadcastStatus.textContent = text;
  broadcastStatus.dataset.state = state;
}

function renderNotice(element, message, error = false) {
  const text = messageText(message);
  if (text !== element.textContent) element.textContent = text;
  element.hidden = !message;
  element.classList.toggle("error", error);
}

function renderSourceNotice() {
  const {broadcast, outcome} = shared;
  if (serverMessage) {
    renderNotice(sourceNotice, serverMessage, serverMessage === "serverConnectFailed");
  } else if (sourceMessage) {
    renderNotice(sourceNotice, sourceMessage, true);
  } else if (outcome && outcome.id !== dismissedOutcomeId && outcome.kind !== "stopped") {
    renderNotice(sourceNotice, outcome.message, outcome.kind === "error");
  } else {
    renderNotice(sourceNotice, broadcast?.phase === "reconnecting" ? "reconnectingSource" : "");
  }
}

function renderPlaybackNotice() {
  const phase = shared.broadcast?.phase;
  const {message: playbackMessage, error: playbackError} = playback;
  const sharedPause = phase === "pausing" || phase === "paused" || phase === "resuming";
  const locallyPlaying = playbackMessage === "playing" || playbackMessage === "buffering";
  renderNotice(status, sharedPause && locallyPlaying ? phaseLabels[phase] : playbackMessage, playbackError);
}

function renderAudioMode() {
  const audioMode = shared.broadcast?.audio_mode;
  streamStatus.set("audio-mode", audioMode === "raw" ? t("rawAudio") : audioMode === "isolated" ? t("isolatedAudio") : null);
}

function renderLanguage(locale) {
  setLanguage(locale);
  languagePicker.value = locale;
  broadcastStatus.textContent = messageText(broadcastMessage);
  renderSourceNotice();
  renderPlaybackNotice();
  settings.renderLanguage();
  timeline.render();
  volume.renderLanguage();
  streamStatus.updateLanguage();
  renderAudioMode();
}

function renderControls() {
  const {broadcast} = shared;
  const stopping = broadcast?.phase === "stopping";
  input.readOnly = Boolean(broadcast || playback.hasSession);
  renderClearUrl();
  start.hidden = Boolean(broadcast || playback.hasSession);
  start.disabled = !signal.isReady() || start.hidden;
  stop.hidden = !broadcast;
  playerEmpty.hidden = Boolean(broadcast || playback.hasSession);
  stop.disabled = !signal.isReady() || stopping;
  playback.render(stopping);
  volume.setEnabled(signal.isReady());
  timeline.render();
}

function showSharedState(state, wasReady) {
  const {broadcast} = state;
  timeline.beforeStateChange(broadcast, wasReady);
  if (shared.broadcast?.id !== broadcast?.id && broadcast) sourceMessage = "";
  serverMessage = "";
  shared.broadcast = broadcast;
  shared.outcome = state.outcome;
  renderPlaybackNotice();
  renderAudioMode();
  if (broadcast) {
    const videoTitle = broadcast.title ?? "";
    if (input.value !== broadcast.url) updateUrl(broadcast.url);
    if (title.textContent !== videoTitle) title.textContent = videoTitle;
    playback.updateStream(state.resources);
  }
  showBroadcastStatus(broadcast ? phaseLabels[broadcast.phase] : "stopped",
    broadcast?.phase === "playing" ? "active" : broadcast && broadcast.phase !== "paused" ? "busy" : "idle");
  renderSourceNotice();
  volume.update(state.mix, state.mix_ack, wasReady);
  playback.syncViewer();
  renderControls();
  playback.joinBroadcast();
}

function renderClearUrl() {
  clearUrl.hidden = input.readOnly || !input.value;
}

function updateUrl(value) {
  if (input.value !== value) input.value = value;
  try {
    if (input.value) localStorage.setItem(urlKey, input.value);
    else localStorage.removeItem(urlKey);
  } catch {
    // The URL stays in the field if saving fails.
  }
  renderClearUrl();
}

function cleanUrlInput() {
  updateUrl(normalizeSourceUrl(input.value) ?? input.value);
}

input.addEventListener("paste", (event) => {
  if (input.readOnly || !event.clipboardData) return;
  const url = normalizeSourceUrl(event.clipboardData.getData("text/plain"));
  if (!url) return;
  event.preventDefault();
  updateUrl(url);
});
input.addEventListener("input", () => updateUrl(input.value));
input.addEventListener("change", cleanUrlInput);
clearUrl.addEventListener("click", () => {
  updateUrl("");
  if (sourceMessage === "enterUrl" || sourceMessage === "enterValidUrl") {
    sourceMessage = "";
    renderSourceNotice();
  }
  input.focus();
});

form.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!signal.isReady() || playback.hasSession || shared.broadcast) return;
  dismissedOutcomeId = shared.outcome?.id ?? null;
  cleanUrlInput();
  if (!input.validity.valid) {
    sourceMessage = input.validity.valueMissing ? "enterUrl" : "enterValidUrl";
    renderSourceNotice();
    input.focus();
    return;
  }
  if (!settings.isDelayValid()) {
    sourceMessage = "invalidVideoDelay";
    renderSourceNotice();
    settings.focusDelay();
    return;
  }
  sourceMessage = "";
  renderSourceNotice();
  void playback.openViewer(null, settings.broadcastOptions(input.value.trim()));
});

stop.addEventListener("click", playback.stopBroadcast);
window.addEventListener("pagehide", () => {
  playback.close();
  serverMessage = "";
  signal.close();
});
window.addEventListener("pageshow", event => {
  if (event.persisted) signal.connect();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    signal.cancelReconnect();
    playback.cancelViewerRetry();
    return;
  }
  signal.connect();
  playback.joinBroadcast();
});
languagePicker.addEventListener("change", () => {
  saveLanguage(languagePicker.value);
  renderLanguage(languagePicker.value);
});
try {
  input.value = localStorage.getItem(urlKey) ?? "";
} catch {
  // Start with an empty URL field if local storage is unavailable.
}
renderClearUrl();
renderLanguage(initialLanguage());
signal.connect();
