import { t } from "/i18n.mjs";

function showFill(range) {
  const span = Number(range.max) - Number(range.min);
  const fraction = span > 0 ? (range.valueAsNumber - Number(range.min)) / span : 0;
  range.style.setProperty("--fill", `${fraction * 100}%`);
}

export function createVolumeControls(signal) {
  const levels = [...document.querySelectorAll(".mix input")];
  let sharedMix = null;
  const pendingMix = new Map();

  function levelDb(level) {
    return level.value === level.min ? null : level.valueAsNumber;
  }

  function showLevel(level) {
    const db = levelDb(level);
    const label = db === null ? t("mute") : `${String(db).replace("-", "−")} dB`;
    document.querySelector(`output[for="${level.id}"]`).value = label;
    level.setAttribute("aria-valuetext", label);
    showFill(level);
  }

  for (const level of levels) {
    showLevel(level);
    level.addEventListener("input", () => {
      const requestId = signal.nextRequestId();
      if (signal.send({type: "mix", request_id: requestId, level: level.name, db: levelDb(level)})) {
        pendingMix.set(level.name, requestId);
      } else if (sharedMix) {
        const db = sharedMix[level.name];
        level.value = db === null ? level.min : String(db);
      }
      showLevel(level);
    });
  }

  function update(mix, ack, wasReady) {
    if (!wasReady) pendingMix.clear();
    sharedMix = mix;
    for (const level of levels) {
      if (pendingMix.get(level.name) > ack) continue;
      pendingMix.delete(level.name);
      const db = sharedMix[level.name];
      const value = db === null ? level.min : String(db);
      if (value === level.value) continue;
      level.value = value;
      showLevel(level);
    }
  }

  function setEnabled(enabled) {
    for (const level of levels) level.disabled = !enabled;
  }

  function renderLanguage() {
    for (const level of levels) showLevel(level);
  }

  return {update, setEnabled, renderLanguage};
}

export function createTimelineControls(signal, shared) {
  const seekTimeline = document.querySelector("#seek-timeline");
  const sharedPlayback = document.querySelector("#shared-playback");
  const [pauseIcon, resumeIcon] = sharedPlayback.querySelectorAll("path");
  const seekSkipButtons = seekTimeline.querySelectorAll("[data-seek-offset]");
  const seekPosition = document.querySelector("#seek-position");
  const seekTime = document.querySelector("#seek-time");
  let seekPointer = null;
  let pendingSeek = null;

  function formatTime(seconds) {
    const whole = Math.floor(seconds);
    const minutes = Math.floor(whole / 60);
    const remainder = String(whole % 60).padStart(2, "0");
    if (minutes < 60) return `${minutes}:${remainder}`;
    return `${Math.floor(minutes / 60)}:${String(minutes % 60).padStart(2, "0")}:${remainder}`;
  }

  function showSeekTime() {
    const time = `${formatTime(seekPosition.valueAsNumber)} / ${formatTime(Number(seekPosition.max))}`;
    seekTime.value = time;
    seekPosition.setAttribute("aria-valuetext", time);
    showFill(seekPosition);
  }

  function renderSeek() {
    const {broadcast} = shared;
    const duration = broadcast?.duration;
    seekTimeline.hidden = !Number.isFinite(duration) || duration <= 0;
    seekPosition.disabled = seekTimeline.hidden || !signal.isReady() || broadcast.phase !== "playing" || pendingSeek !== null;
    const paused = broadcast?.phase === "paused";
    sharedPlayback.disabled = seekTimeline.hidden || !signal.isReady() || (seekPosition.disabled && !paused);
    const label = t(paused ? "resumeSharedPlayback" : "pauseSharedPlayback");
    sharedPlayback.setAttribute("aria-label", label);
    sharedPlayback.title = label;
    pauseIcon.toggleAttribute("hidden", paused);
    resumeIcon.toggleAttribute("hidden", !paused);
    for (const button of seekSkipButtons) button.disabled = seekPosition.disabled;
    if (seekPosition.disabled) seekPointer = null;
    if (seekTimeline.hidden) {
      seekPosition.max = "0";
      seekPosition.value = "0";
      seekTime.value = "";
      seekPosition.removeAttribute("aria-valuetext");
      showFill(seekPosition);
      return;
    }
    if (Number(seekPosition.max) !== duration) seekPosition.max = String(duration);
    if (seekPointer === null) {
      seekPosition.valueAsNumber = pendingSeek?.position ?? broadcast.seek_target ?? broadcast.position ?? 0;
    }
    showSeekTime();
  }

  sharedPlayback.addEventListener("click", () => {
    if (sharedPlayback.disabled) return;
    const {broadcast} = shared;
    const type = broadcast.phase === "paused" ? "resume_playback" : "pause_playback";
    signal.send({type, broadcast_id: broadcast.id});
  });

  seekPosition.addEventListener("pointerdown", event => {
    if (!seekPosition.disabled) seekPointer = event.pointerId;
  });
  window.addEventListener("pointerup", event => {
    if (seekPointer === event.pointerId) seekPointer = null;
  });
  window.addEventListener("pointercancel", event => {
    if (seekPointer !== event.pointerId) return;
    seekPointer = null;
    renderSeek();
  });
  function requestSeek(target) {
    if (seekPosition.disabled) return;
    seekPointer = null;
    const {broadcast} = shared;
    const requestId = signal.nextRequestId();
    const position = Math.min(broadcast.duration, Math.max(0, target));
    if (signal.send({type: "seek", request_id: requestId, broadcast_id: broadcast.id, position})) {
      pendingSeek = {requestId, position, broadcastId: broadcast.id, revision: broadcast.seek_revision};
    }
    renderSeek();
  }

  seekPosition.addEventListener("input", showSeekTime);
  seekPosition.addEventListener("change", () => requestSeek(seekPosition.valueAsNumber));
  for (const button of seekSkipButtons) {
    button.addEventListener("click", () => {
      requestSeek(seekPosition.valueAsNumber + Number(button.dataset.seekOffset));
    });
  }

  function beforeStateChange(broadcast, wasReady) {
    if (shared.broadcast?.id !== broadcast?.id) seekPointer = null;
    if (pendingSeek && (!wasReady || broadcast?.id !== pendingSeek.broadcastId ||
        broadcast.seek_revision > pendingSeek.revision)) pendingSeek = null;
  }

  function reject(requestId) {
    if (requestId !== pendingSeek?.requestId) return;
    pendingSeek = null;
    renderSeek();
  }

  return {render: renderSeek, beforeStateChange, reject};
}
