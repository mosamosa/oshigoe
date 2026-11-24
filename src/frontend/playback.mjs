import { offerWithH264ReceiveLevels } from "/rtc-offer.mjs";

export function createPlayback({
  player, playView, streamStatus, shared, signal,
  renderControls, renderNotice, showTitle, showSourceError, showConnectionError
}) {
  const viewerRetryDelays = [500, 1000, 2000];
  const fullscreenPauseWindow = 500;
  const fullscreenResumeDelay = 250;
  let active = null;
  let viewerAttempt = null;
  let playbackMessage = "";
  let playbackError = false;

  function showStatus(message, error = false) {
    playbackMessage = message;
    playbackError = error;
    renderNotice();
  }

  function requestPlayback(session) {
    if (player.srcObject !== session.stream) player.srcObject = session.stream;
    showStatus("preparingPlayback");
    renderControls();
    player.play().catch(error => {
      if (active !== session) return;
      player.srcObject = null;
      session.resumePlayback = false;
      if (session.ready) {
        showStatus(error.name === "NotAllowedError" ? "pressPlay" :
          "playbackFailed", error.name !== "NotAllowedError");
      }
      renderControls();
    });
  }

  function finish(session, message, {error = false, ended = false, detach = false} = {}) {
    if (!session || active !== session) return;
    active = null;
    if (detach && session.offered) {
      const leave = {type: "leave", request_id: session.requestId, broadcast_id: session.broadcastId,
        viewer_token: session.viewerToken};
      if (!signal.send(leave) && session.viewerToken && viewerAttempt?.broadcastId === session.broadcastId) {
        viewerAttempt.pendingLeave = leave;
      }
    }
    session.cancelIce?.();
    clearTimeout(session.disconnectTimer);
    clearTimeout(session.stableTimer);
    clearTimeout(session.offerTimer);
    clearTimeout(session.resumeTimer);
    clearTimeout(session.fullscreenResumeTimer);
    session.peer.close();
    streamStatus.stop();
    if (!ended) player.srcObject = null;
    showStatus(message, error);
    renderControls();
  }

  function trackBroadcast(broadcastId) {
    if (viewerAttempt?.broadcastId !== broadcastId) {
      cancelViewerRetry();
      viewerAttempt = {broadcastId, retries: 0, pending: null, timer: null, resumePlayback: false};
    }
    return viewerAttempt;
  }

  function cancelViewerRetry() {
    if (!viewerAttempt) return;
    clearTimeout(viewerAttempt.timer);
    viewerAttempt.timer = null;
  }

  function reconcileViewer() {
    const {broadcast, outcome} = shared;
    const id = active?.broadcastId;
    if (id != null) {
      if (outcome?.id === id) {
        finish(active, "", {ended: outcome.kind === "ended"});
      } else if ((active.needsResume && broadcast?.id !== id) || broadcast?.id > id || outcome?.id > id) {
        finish(active, "");
      }
    }

    if (!viewerAttempt) return;
    const attemptId = viewerAttempt.broadcastId;
    if (outcome?.id === attemptId || (broadcast && broadcast.id !== attemptId) || (!active && !broadcast)) {
      cancelViewerRetry();
      if (!active || viewerAttempt.pending) showStatus("");
      viewerAttempt = null;
    } else if (broadcast?.phase === "stopping") {
      cancelViewerRetry();
      viewerAttempt.pending = null;
      viewerAttempt.stopping = true;
    }
  }

  function canJoinBroadcast() {
    const {broadcast} = shared;
    return signal.canSend() && !document.hidden && !active && broadcast && broadcast.phase !== "stopping";
  }

  function joinBroadcast() {
    if (!canJoinBroadcast()) return;
    const id = shared.broadcast.id;
    if (viewerAttempt?.broadcastId !== id) {
      trackBroadcast(id);
      void openViewer(id);
      return;
    }
    const attempt = viewerAttempt;
    if (!attempt.pending || attempt.timer !== null || attempt.stopping) return;
    if (attempt.pending === "socket") {
      attempt.pending = null;
      void openViewer(id, {}, attempt.resumePlayback);
      return;
    }
    attempt.timer = setTimeout(() => {
      attempt.timer = null;
      if (viewerAttempt !== attempt || !canJoinBroadcast() || shared.broadcast.id !== id) return;
      attempt.pending = null;
      attempt.retries++;
      void openViewer(id, {}, attempt.resumePlayback);
    }, viewerRetryDelays[attempt.retries]);
  }

  function resumeViewer(session) {
    const {broadcast} = shared;
    if (!session?.needsResume || session.resumeTimer != null || broadcast?.id !== session.broadcastId ||
        broadcast.phase === "stopping") return;
    if (!signal.send({type: "resume", broadcast_id: session.broadcastId, request_id: session.requestId,
      viewer_token: session.viewerToken})) return;
    session.resumeTimer = setTimeout(() => recoverViewer(session, "resumeTimeout"), 8000);
  }

  function gatherIce(session) {
    const {peer} = session;
    if (peer.iceGatheringState === "complete") return Promise.resolve();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        cleanup();
        reject(new DOMException("Preparing the WebRTC connection timed out.", "TimeoutError"));
      }, 8000);
      function cleanup() {
        clearTimeout(timer);
        peer.removeEventListener("icegatheringstatechange", changed);
        session.cancelIce = null;
      }
      function changed() {
        if (peer.iceGatheringState === "complete") {
          cleanup();
          resolve();
        }
      }
      session.cancelIce = () => {
        cleanup();
        resolve();
      };
      peer.addEventListener("icegatheringstatechange", changed);
      changed();
    });
  }

  async function receive(response) {
    const session = active;
    if (!session || response.request_id !== session.requestId) return;
    if (response.type === "resumed" || response.type === "resume_error") {
      clearTimeout(session.resumeTimer);
      session.resumeTimer = null;
      session.needsResume = false;
      if (response.type === "resume_error") recoverViewer(session, "resumeFailed");
      return;
    }
    if (["accepted", "answer", "error"].includes(response.type)) clearTimeout(session.offerTimer);
    if (response.type === "accepted") {
      session.viewerToken = response.viewer_token;
      session.broadcastId = response.broadcast_id;
      trackBroadcast(session.broadcastId);
      reconcileViewer();
      return;
    }
    if (response.type === "error") {
      if (session.broadcastId === null) {
        showSourceError(response.message);
        finish(session, "", {detach: true});
      } else if (response.retryable === true) {
        recoverViewer(session, response.message);
      } else {
        finish(session, response.message, {error: true, detach: true});
      }
      return;
    }
    if (response.type !== "answer") return;
    try {
      session.viewerToken = response.viewer_token;
      session.broadcastId = response.broadcast_id;
      trackBroadcast(session.broadcastId);
      reconcileViewer();
      if (active !== session) return;
      showTitle(response.title);
      streamStatus.show(response.stream);
      showStatus("rtcConnecting");
      await session.peer.setRemoteDescription({type: "answer", sdp: response.sdp});
      if (active !== session) return;
      streamStatus.start(session.peer);
      session.ready = true;
      const {broadcast} = shared;
      if (broadcast?.id === session.broadcastId && broadcast.stream) streamStatus.updateStream(broadcast.stream);
      if (session.resumePlayback) {
        requestPlayback(session);
      } else {
        if (!player.srcObject) showStatus("pressPlay");
        renderControls();
      }
    } catch {
      finish(session, "rtcStartFailed", {error: true, detach: true});
    }
  }

  function rememberPlayback(session, attempt) {
    attempt.resumePlayback = session.resumePlayback ||
      (session.hasPlayed && player.srcObject === session.stream && !player.paused);
  }

  function recoverViewer(session, message) {
    if (active !== session) return;
    const attempt = viewerAttempt;
    if (session.broadcastId == null || attempt?.broadcastId !== session.broadcastId ||
        attempt.stopping || shared.outcome?.id === session.broadcastId ||
        attempt.retries >= viewerRetryDelays.length) {
      finish(session, message, {error: true, detach: true});
      return;
    }
    rememberPlayback(session, attempt);
    attempt.pending = "peer";
    finish(session, "rtcReconnecting", {detach: true});
    joinBroadcast();
  }

  function serverDisconnected() {
    cancelViewerRetry();
    const session = active;
    if (session?.ready && session.viewerToken &&
        ["connected", "disconnected"].includes(session.peer.connectionState)) {
      clearTimeout(session.resumeTimer);
      session.resumeTimer = null;
      session.needsResume = true;
    } else {
      if (session?.broadcastId != null && viewerAttempt?.broadcastId === session.broadcastId &&
          !viewerAttempt.stopping && shared.broadcast?.phase !== "stopping") {
        rememberPlayback(session, viewerAttempt);
        viewerAttempt.pending = "socket";
      }
      finish(session, "");
    }
  }

  async function openViewer(broadcastId, startOptions = {}, resumePlayback = false) {
    if (!window.RTCPeerConnection) {
      showStatus("rtcUnsupported", true);
      return;
    }

    const peer = new RTCPeerConnection({iceServers: [], bundlePolicy: "max-bundle"});
    const stream = new MediaStream();
    const session = {peer, stream, broadcastId, resumePlayback, requestId: signal.nextRequestId()};
    active = session;
    streamStatus.reset();
    player.srcObject = null;
    showStatus("preparingPlayback");
    renderControls();

    peer.ontrack = ({receiver, track}) => {
      if ("jitterBufferTarget" in receiver) receiver.jitterBufferTarget = 0;
      stream.addTrack(track);
    };
    peer.onconnectionstatechange = () => {
      if (active !== session) return;
      clearTimeout(session.disconnectTimer);
      clearTimeout(session.stableTimer);
      if (peer.connectionState === "connected") {
        session.stableTimer = setTimeout(() => {
          if (viewerAttempt?.broadcastId === session.broadcastId) viewerAttempt.retries = 0;
        }, 10000);
      } else if (peer.connectionState === "disconnected") {
        session.disconnectTimer = setTimeout(() => {
          if (peer.connectionState === "disconnected") recoverViewer(session, "rtcLost");
        }, 3000);
      } else if (peer.connectionState === "failed") {
        recoverViewer(session, "rtcLost");
      }
    };
    try {
      peer.addTransceiver("video", {direction: "recvonly"});
      peer.addTransceiver("audio", {direction: "recvonly"});
      if (broadcastId === null) requestPlayback(session);
      await peer.setLocalDescription(await peer.createOffer());
      await gatherIce(session);
      if (active !== session) return;
      const sdp = await offerWithH264ReceiveLevels(peer.localDescription.sdp);
      if (active !== session) return;
      session.offered = signal.send({
        type: "offer", request_id: session.requestId, broadcast_id: broadcastId,
        sdp, ...startOptions
      });
      const reconnect = signal.retryCurrentConnection();
      if (!session.offered) {
        if (reconnect) {
          reconnect();
        } else {
          showConnectionError();
          finish(session, "");
        }
        return;
      }
      session.offerTimer = setTimeout(() => {
        if (active === session) reconnect?.();
      }, 8000);
    } catch (error) {
      if (error.name === "TimeoutError") {
        recoverViewer(session, "rtcPreparingTimeout");
      } else {
        finish(session, "rtcStartFailed", {error: true, detach: true});
      }
    }
  }

  playView.addEventListener("click", () => {
    if (!playView.hidden && active?.ready) requestPlayback(active);
  });
  player.addEventListener("webkitbeginfullscreen", () => {
    if (!active?.ready || player.srcObject !== active.stream) return;
    clearTimeout(active.fullscreenResumeTimer);
    active.nativeFullscreen = {pausedAt: player.paused ? -Infinity : null};
  });
  player.addEventListener("webkitendfullscreen", () => {
    const session = active;
    const fullscreen = session?.nativeFullscreen;
    if (!fullscreen) return;
    session.nativeFullscreen = null;
    // WebKit may pause playback just before or after leaving fullscreen.
    // Preserve pauses that happened before that transition.
    if (fullscreen.pausedAt !== null && performance.now() - fullscreen.pausedAt > fullscreenPauseWindow) return;
    session.fullscreenResumeTimer = setTimeout(() => {
      const {broadcast} = shared;
      if (active === session && !session.nativeFullscreen && !document.hidden &&
          player.srcObject === session.stream && player.paused && !player.ended &&
          broadcast?.id === session.broadcastId && broadcast.phase !== "stopping" && !viewerAttempt?.stopping) {
        requestPlayback(session);
      }
    }, fullscreenResumeDelay);
  });
  player.addEventListener("play", () => {
    if (active?.nativeFullscreen) active.nativeFullscreen.pausedAt = null;
  });
  player.addEventListener("playing", () => {
    if (!active || player.srcObject !== active.stream) return;
    active.hasPlayed = true;
    active.resumePlayback = false;
    showStatus("playing");
  });
  player.addEventListener("waiting", () => {
    if (active && playbackMessage === "playing") showStatus("buffering");
  });
  player.addEventListener("pause", () => {
    if (active?.nativeFullscreen && active.nativeFullscreen.pausedAt === null) {
      active.nativeFullscreen.pausedAt = performance.now();
    }
    if (active && playbackMessage === "playing") showStatus("paused");
  });

  function stopBroadcast() {
    const {broadcast} = shared;
    if (!broadcast) return;
    if (signal.send({type: "stop", broadcast_id: broadcast.id})) {
      const attempt = trackBroadcast(broadcast.id);
      cancelViewerRetry();
      attempt.pending = null;
      attempt.stopping = true;
    }
  }

  function updateStream(resources) {
    const {broadcast} = shared;
    if (active?.ready && active.broadcastId === broadcast.id && broadcast.stream) {
      streamStatus.updateStream(broadcast.stream);
      streamStatus.updateResources(resources);
    }
  }

  function syncViewer() {
    reconcileViewer();
    if (viewerAttempt?.pendingLeave && signal.send(viewerAttempt.pendingLeave)) viewerAttempt.pendingLeave = null;
    resumeViewer(active);
  }

  function render(stopping) {
    playView.hidden = !active?.ready || Boolean(player.srcObject) || stopping;
    const controls = Boolean(player.srcObject);
    if (player.controls !== controls) player.controls = controls;
  }

  function close() {
    cancelViewerRetry();
    finish(active, "", {detach: true});
    viewerAttempt = null;
  }

  return {openViewer, receive, updateStream, syncViewer, joinBroadcast, stopBroadcast,
    serverDisconnected, cancelViewerRetry, render, close,
    get hasSession() { return active !== null; },
    get message() { return playbackMessage; },
    get error() { return playbackError; }
  };
}
