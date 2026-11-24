// Each UI label lists the English text first and the Japanese text second.
const labels = {
  language: ["Language", "言語"],
  start: ["Start", "開始"],
  stop: ["Stop", "停止"],
  sourceUrl: ["Video or live stream URL", "動画・ライブ配信の URL"],
  clearUrl: ["Clear URL", "URL を消去"],
  enterUrl: ["Enter a YouTube or Twitch URL.", "YouTube または Twitch の URL を入力してください。"],
  enterValidUrl: ["Enter a valid YouTube or Twitch URL.", "有効な YouTube または Twitch の URL を入力してください。"],
  player: ["Player", "プレーヤー"],
  webrtcPlayer: ["WebRTC player", "WebRTC プレーヤー"],
  pasteUrl: ["1. Paste a video or live stream URL", "1. 動画またはライブ配信の URL を貼り付け"],
  pressStart: ["2. Press Start", "2. 開始ボタンを押下"],
  adjustMix: ["3. Adjust voice and background volume", "3. 声と背景音の音量を調整"],
  play: ["Play", "再生"],
  skipBack: ["Go back 10 seconds", "10秒巻き戻し"],
  skipForward: ["Skip forward 10 seconds", "10秒早送り"],
  sharedPosition: ["Shared playback position", "全員の再生位置"],
  pauseSharedPlayback: ["Pause for everyone", "全員の再生を一時停止"],
  resumeSharedPlayback: ["Resume for everyone", "全員の再生を再開"],
  volume: ["Volume", "音量"],
  voice: ["Voice", "声"],
  background: ["Background", "背景音"],
  settings: ["Settings", "設定"],
  settingsNote: ["Settings take effect the next time you press Start.", "設定は次回「開始」を押下したときに反映されます"],
  browserLogin: ["Use browser sign-in", "ブラウザのログイン情報を使用"],
  browserLoginHelp: ["Browser sign-in help", "ブラウザのログイン情報を使用する条件と手順"],
  browserLoginHint: [
    "If a video requires sign-in or a public video won't play, try this setting. It uses the sign-in saved in Firefox on the server.",
    "ログインが必要な動画や、公開されているのに再生できない場合にこの設定を試してみて下さい。サーバー側の Firefox のログイン情報を使用します。"
  ],
  videoDelay: ["Video delay", "映像遅延"],
  videoDelayMs: ["Video delay (ms)", "映像遅延（ms）"],
  videoDelayHelp: ["Video delay help", "映像遅延のヒント"],
  videoDelayHint: [
    "Set a delay between 0 and 500 ms. Play the sync test video here to check the timing.",
    "映像遅延を 0〜500 ms で指定します。このアプリで同期テスト動画を再生すると、映像と音声のズレを確認できます。"
  ],
  invalidVideoDelay: ["Enter a whole number from 0 to 500 milliseconds.", "映像遅延は 0〜500 ms の整数で指定してください。"],
  copyTestUrl: ["Copy sync test video URL", "同期テスト動画の URL をコピー"],
  syncTestUrl: ["Sync test video URL", "同期テスト動画の URL"],
  copySuccess: ["Copied.", "コピーしました。"],
  copyManually: ["Copy the selected URL.", "選択中の URL をコピーしてください。"],
  quality: ["Video quality", "動画の品質"],
  resolutionLimit: ["Resolution limit", "解像度の上限"],
  frameRateLimit: ["Frame rate limit", "フレームレートの上限"],
  autoFrameRate: ["Auto (up to 30 fps when re-encoding)", "自動（再エンコード時は最大 30 fps）"],
  noLimit: ["No limit", "上限なし"],
  auto: ["Auto", "自動"],
  videoBitrate: ["Video bitrate (kbps)", "映像ビットレート（kbps）"],
  audioBitrate: ["Audio bitrate (Opus)", "音声ビットレート（Opus）"],
  autoFps: ["Auto fps", "自動 fps"],
  unlimitedFps: ["Unlimited fps", "fps 上限なし"],
  autoVideo: ["Auto video bitrate", "映像 自動"],
  videoKbps: ["Video {kbps} kbps", "映像 {kbps} kbps"],
  audioKbps: ["Audio {kbps} kbps", "音声 {kbps} kbps"],
  mute: ["Muted", "ミュート"],
  status: ["Status", "ステータス"],
  previousStatus: ["Last stream status", "前回のステータス"],
  input: ["Input", "入力"],
  inputType: ["Type", "種別"],
  videoProtocol: ["Video protocol", "映像の取得方式"],
  audioProtocol: ["Audio protocol", "音声の取得方式"],
  videoCodec: ["Video codec", "映像コーデック"],
  audioCodec: ["Audio codec", "音声コーデック"],
  resolution: ["Resolution", "解像度"],
  frameRate: ["Frame rate", "フレームレート"],
  sampleRate: ["Sample rate", "サンプリング周波数"],
  videoProcessing: ["Video processing", "映像処理"],
  processingMode: ["Mode", "処理方式"],
  videoEncoder: ["Encoder", "エンコーダー"],
  videoEncoderType: ["Encoder type", "エンコーダー種別"],
  appliedVideoDelay: ["Applied video delay", "適用中の映像遅延"],
  videoFallback: ["Automatic encoder switch", "自動切り替え"],
  audioProcessing: ["Audio processing", "音声処理"],
  isolationModel: ["Isolation model", "分離モデル"],
  audioDevice: ["Processing device", "処理デバイス"],
  output: ["Output", "出力"],
  videoTarget: ["Target video bitrate", "映像の目標ビットレート"],
  audioTarget: ["Target audio bitrate", "音声の目標ビットレート"],
  videoReceived: ["Received video bitrate", "映像の受信ビットレート"],
  audioReceived: ["Received audio bitrate", "音声の受信ビットレート"],
  connectionQuality: ["Connection quality", "接続品質"],
  roundTripTime: ["Round-trip time", "往復時間"],
  videoPacketLoss: ["Video packet loss", "映像パケット損失"],
  audioPacketLoss: ["Audio packet loss", "音声パケット損失"],
  avOffset: ["Audio/video offset", "映像と音声のずれ"],
  audioOutputDelay: ["Audio output delay", "音声出力遅延"],
  resourceUsage: ["Resource usage", "リソース使用量"],
  isolationLoad: ["Voice isolation load", "音声分離の負荷"],
  cpuUsage: ["CPU usage", "CPU 使用率"],
  memoryUsage: ["Memory usage", "メモリ使用量"],
  gpuUsage: ["GPU usage", "GPU 使用率"],
  gpuMemoryUsage: ["GPU memory usage", "GPU メモリ使用量"],
  pcWide: ["{label} (whole PC)", "{label} (PC全体)"],
  macWide: ["{label} (whole Mac)", "{label} (Mac全体)"],
  connecting: ["Connecting", "接続中"],
  disconnected: ["Disconnected", "未接続"],
  starting: ["Preparing", "準備中"],
  streaming: ["Running", "動作中"],
  pausingSharedPlayback: ["Pausing", "一時停止処理中"],
  resumingSharedPlayback: ["Resuming", "再開準備中"],
  seeking: ["Seeking", "シーク中"],
  reconnecting: ["Reconnecting", "再接続中"],
  reconnectingSource: ["Reconnecting to the source…", "配信元に再接続しています…"],
  stopping: ["Stopping", "停止処理中"],
  stopped: ["Stopped", "停止中"],
  preparingPlayback: ["Preparing playback…", "再生を準備しています…"],
  pressPlay: ["Press Play to start watching.", "再生ボタンを押下してください。"],
  playbackFailed: ["Couldn't start playback.", "プレーヤーで再生できませんでした。"],
  rawAudio: ["Original audio", "原音"],
  isolatedAudio: ["Voice isolation active", "分離処理中"],
  resumeTimeout: ["Couldn't reconnect to the viewing session in time.", "視聴セッションの再接続がタイムアウトしました。"],
  resumeFailed: ["Could not reconnect to the viewing session.", "視聴セッションに再接続できませんでした。"],
  rtcPreparingTimeout: ["WebRTC connection setup timed out.", "WebRTC の接続準備がタイムアウトしました。"],
  rtcConnecting: ["Connecting over WebRTC…", "WebRTC に接続しています…"],
  rtcReconnecting: ["Reconnecting over WebRTC…", "WebRTC に再接続しています…"],
  serverConnecting: ["Connecting to the server…", "サーバーに接続しています…"],
  rtcUnsupported: ["This browser does not support WebRTC.", "このブラウザーは WebRTC に対応していません。"],
  rtcLost: ["The WebRTC connection was lost.", "WebRTC の接続が切断されました。"],
  rtcStartFailed: ["Could not start the WebRTC connection.", "WebRTC の接続を開始できませんでした。"],
  serverConnectFailed: [
    "Could not connect to the server. Retrying…",
    "サーバーに接続できませんでした。再接続を試行しています…"
  ],
  playing: ["Playing", "再生中"],
  buffering: ["Buffering…", "再生に必要なデータの受信を待っています…"],
  paused: ["Paused", "一時停止中"],
  file: ["File", "ファイル"],
  live: ["Live", "ライブ"],
  onDemand: ["On demand", "通常動画"],
  copy: ["Copy", "コピー"],
  reencode: ["Re-encoding", "再エンコード"],
  hardware: ["Hardware", "ハードウェア"],
  audioTargetSummary: ["Opus {kbps} kbps (target)", "Opus {kbps} kbps（目標）"],
  audioLate: ["Audio is about {ms} ms behind (estimated)", "音声が約 {ms} ms 遅れ（推定）"],
  audioEarly: ["Audio is about {ms} ms ahead (estimated)", "音声が約 {ms} ms 先行（推定）"],
  noOffset: ["0 ms (estimated)", "0 ms（推定）"],
  audioOutputEstimate: ["About {ms} ms (estimated)", "約 {ms} ms（推定）"]
};

const serverJapanese = {
  invalid_volume: "音量はミュート、または −30〜0 dB で指定してください。",
  missing_mix: "声と背景音の音量を指定してください。",
  invalid_quality: "動画の品質設定が無効です。",
  invalid_video_delay: "映像遅延は 0〜500 ms の整数で指定してください。",
  invalid_browser_login: "ブラウザのログイン情報を使用するかどうかを指定してください。",
  invalid_resolution: "解像度の上限を選択してください。",
  invalid_frame_rate: "フレームレートの上限を選択してください。",
  invalid_video_bitrate: "映像ビットレートは 100〜12000 kbps で指定してください。",
  invalid_audio_bitrate: "音声ビットレートを選択してください。",
  invalid_source_url: "YouTube の動画・ライブ配信、または Twitch のチャンネル・アーカイブ動画の HTTPS URL を入力してください。",
  missing_source_url: "YouTube または Twitch の URL を入力してください。",
  no_playable_format: "コピー可能な映像と音声の組み合わせを取得できませんでした。ブラウザーが受信できる形式への再エンコードも利用できません。",
  no_source_media: "配信元から映像と音声を取得できませんでした。",
  metadata_timeout: "動画情報の取得がタイムアウトしました。",
  firefox_lookup_failed: "動画情報を取得できませんでした。URL と動画の公開状態およびブラウザのログイン状態を確認してください。",
  source_lookup_failed: "動画情報を取得できませんでした。URL と動画の公開状態を確認してください。",
  ffmpeg_http_error: "FFmpeg が配信元からデータを取得できませんでした（HTTP {status}）。",
  video_reordering: "B フレームを含む H.264 映像のコピーでは再生が途切れるため、再エンコードが必要です。",
  media_stalled: "映像または音声の受信が途切れました。",
  media_timeout: "映像と音声の取得がタイムアウトしました。",
  no_media_received: "映像と音声を受信できませんでした。",
  ffmpeg_read_failed: "FFmpeg が映像または音声を取得できませんでした。",
  media_processing_failed: "映像または音声の処理中にエラーが発生しました。",
  codec_mismatch: "実際の映像コーデックが WebRTC の接続設定と一致しません。",
  hardware_h264_missing: "ブラウザーの受信条件に適合する H.264 映像をハードウェアエンコーダーから取得できませんでした。",
  hardware_h264_level: "このブラウザーはハードウェアエンコーダーが出力した H.264 のレベルに対応していません。",
  keyframe_missing: "映像のキーフレームと音声を受信できませんでした。",
  invalid_webrtc_offer: "WebRTC の接続情報が無効です。",
  missing_webrtc_tracks: "映像と音声の受信トラックを指定してください。",
  webrtc_receive_required: "映像と音声の受信を許可してください。",
  opus_unsupported: "ブラウザーが Opus 音声を受信できません。",
  video_codec_unsupported: "このブラウザーは現在の映像コーデックに対応していません。",
  webrtc_connect_failed: "WebRTC で接続できませんでした。",
  webrtc_disconnected: "WebRTC の接続が切断されました。",
  webrtc_timeout: "WebRTC の接続がタイムアウトしました。",
  webrtc_start_failed: "WebRTC の接続を開始できませんでした。",
  input_recovery_deadline: "制限時間内に配信元へ再接続できませんでした。",
  input_recovery_timeout: "配信元への再接続がタイムアウトしました。",
  input_ended_early: "配信元からの受信が途中で終了しました。",
  broadcast_stopped: "処理を停止しました。",
  broadcast_ended: "再生が終了しました。",
  broadcast_start_failed: "処理を開始できませんでした。",
  broadcast_continue_failed: "処理を継続できませんでした。",
  broadcast_gone: "この動画・ライブ配信の視聴は終了しました。",
  already_watching: "このブラウザーではすでに視聴中です。",
  broadcast_busy: "別の動画・ライブ配信を処理中です。"
};

const languageKey = "oshigoe.language";
let language = "en";

function interpolate(text, values) {
  return text.replace(/\{(\w+)\}/g, (placeholder, key) => values[key] ?? placeholder);
}

export function t(key, values = {}) {
  return interpolate(labels[key][language === "ja" ? 1 : 0], values);
}

export function messageText(message) {
  if (!message) return "";
  if (typeof message === "string") return t(message);
  const localized = language === "ja" && Object.hasOwn(serverJapanese, message.key) ? serverJapanese[message.key] : null;
  return localized ? interpolate(localized, message.values ?? {}) : message.text;
}

export function initialLanguage() {
  try {
    const saved = localStorage.getItem(languageKey);
    if (saved === "en" || saved === "ja") return saved;
  } catch {
    // Fall back to the browser's preferred languages if local storage is unavailable.
  }
  return navigator.languages.map(locale => locale.toLowerCase().split("-")[0])
    .find(locale => locale === "en" || locale === "ja") ?? "en";
}

export function setLanguage(locale) {
  language = locale === "ja" ? "ja" : "en";
  document.documentElement.lang = language;
  for (const node of document.querySelectorAll("[data-i18n]")) node.textContent = t(node.dataset.i18n);
  for (const node of document.querySelectorAll("[data-i18n-label]")) node.setAttribute("aria-label", t(node.dataset.i18nLabel));
  for (const node of document.querySelectorAll("[data-i18n-placeholder]")) node.placeholder = t(node.dataset.i18nPlaceholder);
}

export function saveLanguage(locale) {
  try {
    localStorage.setItem(languageKey, locale);
  } catch {
    // The selected language still applies to the current page if saving fails.
  }
}
