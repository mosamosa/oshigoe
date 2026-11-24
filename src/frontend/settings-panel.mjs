import { messageText, t } from "/i18n.mjs";

export function createSettingsPanel(onValidDelay) {
  const browserLogin = document.querySelector("#browser-login");
  const videoDelayEnabled = document.querySelector("#video-delay-enabled");
  const videoDelay = document.querySelector("#video-delay");
  const qualityFields = [...document.querySelector("#quality").elements];
  const qualitySummary = document.querySelector("#quality-summary");
  const settingsKey = "oshigoe.quality";
  let copyStatusMessage = "";

  for (const help of document.querySelectorAll(".setting-help")) {
    const button = help.querySelector(":scope > button");
    const hint = help.querySelector(".setting-hint");
    function showHint(visible) {
      if (visible === !hint.hidden) return;
      hint.classList.remove("above");
      hint.hidden = !visible;
      if (visible) {
        const {bottom, height} = hint.getBoundingClientRect();
        if (bottom > innerHeight && height <= help.getBoundingClientRect().top) hint.classList.add("above");
      }
      if (button.hasAttribute("aria-expanded")) button.setAttribute("aria-expanded", String(visible));
    }

    help.addEventListener("pointerenter", event => {
      if (event.pointerType === "mouse") showHint(true);
    });
    help.addEventListener("pointerleave", event => {
      if (event.pointerType === "mouse" && !help.querySelector(":focus-visible")) showHint(false);
    });
    button.addEventListener("focus", () => {
      if (button.matches(":focus-visible")) showHint(true);
    });
    help.addEventListener("focusout", event => {
      if (!help.contains(event.relatedTarget)) showHint(false);
    });
    button.addEventListener("click", () => showHint(true));
    document.addEventListener("pointerdown", event => {
      if (!help.contains(event.target)) showHint(false);
    });
    document.addEventListener("keydown", event => {
      if (event.key !== "Escape" || hint.hidden) return;
      if (hint.contains(document.activeElement)) button.focus();
      showHint(false);
    });
  }

  const copySyncTestUrl = document.querySelector("#copy-sync-test-url");
  const syncTestUrl = document.querySelector("#sync-test-url");
  const syncTestCopyStatus = document.querySelector("#sync-test-copy-status");
  copySyncTestUrl.addEventListener("click", async () => {
    let copied = false;
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(syncTestUrl.value);
        copied = true;
      } catch {
        // Try copying the URL as selected text.
      }
    }
    if (!copied) {
      syncTestUrl.hidden = false;
      syncTestUrl.focus({preventScroll: true});
      syncTestUrl.select();
      try {
        copied = document.execCommand("copy");
      } catch {
        // Keep the URL selected so the viewer can copy it manually.
      }
    }
    if (copied) {
      if (document.activeElement === syncTestUrl) copySyncTestUrl.focus({preventScroll: true});
      syncTestUrl.hidden = true;
    }
    copyStatusMessage = copied ? "copySuccess" : "copyManually";
    syncTestCopyStatus.textContent = t(copyStatusMessage);
  });

  function currentQuality() {
    return Object.fromEntries(qualityFields.map(field => [
      field.name, field.value === "" ? null : Number(field.value)
    ]));
  }

  function broadcastOptions(url) {
    const quality = currentQuality();
    const use_browser_login = browserLogin.checked;
    const video_delay_ms = videoDelayEnabled.checked ? videoDelay.valueAsNumber : 0;
    return {url, quality, use_browser_login, video_delay_ms};
  }

  const numericSettings = [...qualityFields, videoDelay];
  try {
    const saved = JSON.parse(localStorage.getItem(settingsKey));
    browserLogin.checked = saved?.use_browser_login === true;
    videoDelayEnabled.checked = saved?.video_delay_enabled === true;
    for (const field of numericSettings) {
      const value = saved?.[field.name];
      if (Number.isFinite(value) || (value === null && field !== videoDelay)) {
        const previous = field.value;
        field.value = value ?? "";
        if (!field.validity.valid || field.selectedIndex === -1) field.value = previous;
      }
    }
  } catch {
    // Use the defaults if saved settings are unavailable or unreadable.
  }
  function renderQualitySummary() {
    const quality = currentQuality();
    qualitySummary.textContent = [
      quality.height === 0 ? t("noLimit") : `${quality.height}p`,
      quality.fps === null ? t("autoFps") : quality.fps === 0 ? t("unlimitedFps") : `${quality.fps} fps`,
      quality.video_kbps === null ? t("autoVideo") : t("videoKbps", {kbps: quality.video_kbps}),
      t("audioKbps", {kbps: quality.audio_kbps})
    ].join(" · ");
  }
  for (const field of qualityFields) field.addEventListener("input", renderQualitySummary);
  let savedVideoDelay = videoDelay.valueAsNumber;
  function renderDelayControl() {
    videoDelay.disabled = !videoDelayEnabled.checked;
  }

  function isDelayValid() {
    return !videoDelayEnabled.checked || videoDelay.validity.valid;
  }

  renderDelayControl();
  videoDelayEnabled.addEventListener("input", renderDelayControl);
  for (const field of [...numericSettings, browserLogin, videoDelayEnabled]) {
    field.addEventListener("input", () => {
      if (isDelayValid()) onValidDelay();
      if (qualityFields.some(item => !item.validity.valid)) return;
      if (!videoDelay.disabled && videoDelay.validity.valid) savedVideoDelay = videoDelay.valueAsNumber;
      try {
        localStorage.setItem(settingsKey, JSON.stringify({
          ...currentQuality(), use_browser_login: browserLogin.checked,
          video_delay_enabled: videoDelayEnabled.checked, video_delay_ms: savedVideoDelay
        }));
      } catch {
        // Keep the current settings for this page if saving fails.
      }
    });
  }

  function renderLanguage() {
    syncTestCopyStatus.textContent = messageText(copyStatusMessage);
    renderQualitySummary();
  }

  return {broadcastOptions, isDelayValid, renderLanguage, focusDelay: () => videoDelay.focus()};
}
