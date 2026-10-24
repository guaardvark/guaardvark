/**
 * The app's one microphone session.
 *
 * Owns the MediaStream, an AudioContext of its own, the VAD worklet and the
 * MediaRecorder; nothing else in the app opens the mic. A click starts either
 * a hands-free session (every pause sends a message) or a single utterance;
 * holding a mic button records until release. Each finished utterance is its
 * own recording and its own POST /api/voice/speech-to-text, transcribed in
 * order, then handed to whichever chat registered as the sink. The mic is
 * gated while a reply is spoken so the speakers are not transcribed.
 *
 * Plain JS rather than a hook so no callback can hold a stale copy of React
 * state; VoiceSessionProvider feeds it settings and playback state and the
 * store (useVoiceSessionStore) carries what it is doing to the UI.
 */

import voiceService from "../api/voiceService";
import { checkForWakeWord } from "../utils/wakeWordMatcher";
import { createVadSegmenter } from "../utils/vadSegmenter";
import {
  DEFAULT_ACTIVATION_MODE,
  normalizeVoiceSettings,
  vadConfigFrom,
} from "../config/voiceDefaults";
import { derivePhase } from "../stores/useVoiceSessionStore";
import { translate } from "../i18n";

const RECORDER_TIMESLICE_MS = 250;
// With nobody speaking, the running recording is restarted this often so it
// never grows past a few seconds of silence ahead of the next utterance.
const IDLE_RECYCLE_MS = 10000;
const NO_SPEECH_TIMEOUT_MS = 8000;
const MIN_PUSH_MS = 250;
// Speakers ring on for a moment after the reply's audio element ends.
const PLAYBACK_TAIL_MS = 300;
const MIN_BLOB_BYTES = 1000;
const NOTICE_MS = 5000;
const TRANSCRIPT_HISTORY = 20;
// While a reply is being spoken, talking over it has to be this much louder
// than the normal start threshold to count as interrupting rather than echo.
const BARGE_IN_MULTIPLIER = 2.5;
const CHIME_GUARD_MS = 350;
const LEVEL_INTERVAL_MS = 50;
// A sink that never reports its reply finished stops showing "waiting" after this.
const AWAIT_REPLY_TIMEOUT_MS = 180000;

const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/mp4",
  "audio/ogg;codecs=opus",
];

// The message is a function so it is worded in the interface's language when the error happens.
const micBlocked = () => translate("messages.voiceSession.micBlocked");
const noMic = () => translate("messages.voiceSession.noMic");
const micBusy = () => translate("messages.voiceSession.micBusy");
const MEDIA_ERRORS = {
  NotAllowedError: ["denied", micBlocked],
  PermissionDeniedError: ["denied", micBlocked],
  SecurityError: ["denied", micBlocked],
  NotFoundError: ["no-device", noMic],
  DevicesNotFoundError: ["no-device", noMic],
  OverconstrainedError: ["no-device", () => translate("messages.voiceSession.noMatchingMic")],
  NotReadableError: ["busy", micBusy],
  TrackStartError: ["busy", micBusy],
};

function stopTracks(stream) {
  try {
    stream?.getTracks?.().forEach((track) => track.stop());
  } catch {
    // A track that is already gone needs no stopping.
  }
}

function safeDisconnect(node) {
  try {
    node?.disconnect?.();
  } catch {
    // Already disconnected.
  }
}

/** Short low "grunt" played when the wake phrase is heard. */
export function playWakeChime(ctx) {
  if (!ctx || typeof ctx.createOscillator !== "function") return;
  try {
    const now = ctx.currentTime;
    const osc = ctx.createOscillator();
    osc.type = "sawtooth";
    osc.frequency.setValueAtTime(180, now);
    osc.frequency.linearRampToValueAtTime(120, now + 0.15);
    const filter = ctx.createBiquadFilter();
    filter.type = "bandpass";
    filter.frequency.setValueAtTime(200, now);
    filter.Q.setValueAtTime(2, now);
    const gain = ctx.createGain();
    gain.gain.setValueAtTime(0, now);
    gain.gain.linearRampToValueAtTime(0.12, now + 0.02);
    gain.gain.setValueAtTime(0.12, now + 0.08);
    gain.gain.exponentialRampToValueAtTime(0.001, now + 0.2);
    osc.connect(filter);
    filter.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now);
    osc.stop(now + 0.2);
  } catch {
    // The chime is a courtesy; a failure must not stop listening.
  }
}

function defaultDeps() {
  const g = typeof window !== "undefined" ? window : globalThis;
  return {
    isSecureContext: () => g.isSecureContext !== false,
    hasMediaDevices: () => Boolean(g.navigator?.mediaDevices?.getUserMedia),
    getUserMedia: (constraints) => g.navigator.mediaDevices.getUserMedia(constraints),
    createAudioContext: () => new (g.AudioContext || g.webkitAudioContext)(),
    createWorkletNode: (ctx, name) => new g.AudioWorkletNode(ctx, name),
    createRecorder: (stream, options) => new g.MediaRecorder(stream, options),
    isTypeSupported: (type) => Boolean(g.MediaRecorder?.isTypeSupported?.(type)),
    transcribe: (blob, options) => voiceService.transcribeUtterance(blob, options),
    stopPlayback: () => voiceService.stopPlayback(),
    chime: playWakeChime,
    now: () => Date.now(),
    workletUrl: "/vad-processor.js",
  };
}

export class VoiceSessionEngine {
  /**
   * @param {{store: {getState: Function, setState: Function}, deps?: object}} options
   *   `deps` replaces browser and network seams (tests pass fakes).
   */
  constructor({ store, deps = {} }) {
    this.store = store;
    this.deps = { ...defaultDeps(), ...deps };
    this.settings = normalizeVoiceSettings({});
    this.activationMode = DEFAULT_ACTIVATION_MODE;
    this.systemName = "Guaardvark";
    this.vad = createVadSegmenter(vadConfigFrom(this.settings));
    this.bargeVad = createVadSegmenter(this._bargeConfig());

    this.sinks = [];
    this.sinkSeq = 0;
    this.holders = new Set();
    this.opening = null;
    this.stream = null;
    this.audioCtx = null;
    this.graph = {};
    this.pollTimer = null;

    this.session = null;
    this.recorder = null;
    this.pushActive = false;
    this.pushToken = null;
    this.pushStartedAt = 0;
    this.playbackActive = false;

    this.tailTimer = null;
    this.noSpeechTimer = null;
    this.wakeTimer = null;
    this.noticeTimer = null;
    this.replyTimer = null;
    this.lastLevelAt = 0;
    this.chimeGuardUntil = 0;

    this.sttChain = Promise.resolve();
    this.sttControllers = new Set();
    this.turnSeq = 0;
    this.shutDown = false;
  }

  // ---------------------------------------------------------------- config

  /** Apply settings, activation mode and the name the wake phrase uses. */
  configure({ settings, activationMode, systemName } = {}) {
    if (settings) {
      const wakeWas = this.settings.wakeWordEnabled;
      this.settings = normalizeVoiceSettings(settings);
      this.vad.setConfig(vadConfigFrom(this.settings));
      this.bargeVad.setConfig(this._bargeConfig());
      const wakeNow = this.settings.wakeWordEnabled;
      if (!wakeNow && this.store.getState().listenMode !== "active") {
        this._clearWakeTimer();
        this._set({ listenMode: "active" });
      } else if (wakeNow && !wakeWas && this.session === "handsfree") {
        this._set({ listenMode: "passive" });
      }
    }
    if (activationMode) this.activationMode = activationMode;
    if (systemName) this.systemName = systemName;
  }

  _bargeConfig() {
    const base = vadConfigFrom(this.settings);
    return { ...base, threshold: base.threshold * BARGE_IN_MULTIPLIER };
  }

  // ----------------------------------------------------------------- sinks

  /**
   * Register where transcripts go. The highest priority wins; among equals the
   * latest registration. `deliver(turn)` may return a promise.
   * @returns {() => void} unregister
   */
  registerSink(sink) {
    const entry = { ...sink, priority: sink.priority || 0, seq: ++this.sinkSeq };
    this.sinks.push(entry);
    this.sinks.sort((a, b) => b.priority - a.priority || b.seq - a.seq);
    return () => {
      this.sinks = this.sinks.filter((s) => s !== entry);
    };
  }

  activeSink() {
    return this.sinks[0] || null;
  }

  // --------------------------------------------------------------- actions

  /** What a click on a mic button does, by activation mode. */
  toggle() {
    if (this.session || this.pushActive) {
      this.stop();
      return Promise.resolve(false);
    }
    if (this.activationMode === "push") {
      if (this.playbackActive) this.stopSpeaking();
      this._notice(translate("messages.voiceSession.pushToTalkHint"));
      return Promise.resolve(false);
    }
    return this.start(this.activationMode === "handsfree" ? "handsfree" : "utterance");
  }

  /**
   * Open the mic and start listening. A second call while one is starting or
   * running returns without opening another stream.
   * @param {"handsfree"|"utterance"} kind
   */
  async start(kind = "handsfree") {
    if (this.shutDown || this.session) return Boolean(this.session);
    if (!this._micAllowed()) return false;
    this.clearError();
    if (this.playbackActive) this.stopSpeaking();
    this.session = kind;
    const passive = kind === "handsfree" && this.settings.wakeWordEnabled;
    this._set({ session: kind, listenMode: passive ? "passive" : "active" });

    const ok = await this._acquire("session");
    if (this.session !== kind) {
      if (ok) this._release("session");
      return false;
    }
    if (!ok) {
      this.session = null;
      this._set({ session: null, listenMode: "active" });
      return false;
    }
    this.vad.reset();
    this._startRecorder();
    if (kind === "utterance") this._armNoSpeechTimer();
    return true;
  }

  /**
   * End the session. With `flush` (the default) an utterance in progress is
   * still transcribed and sent; replies already on their way still arrive.
   */
  stop({ flush = true } = {}) {
    this._clearNoSpeechTimer();
    this._clearWakeTimer();
    if (this.playbackActive) this.stopSpeaking();
    const wasCapturing = this.vad.speaking || this.pushActive;
    // A push is deliberate; a hands-free clip still has to carry the wake phrase.
    const wake = !this.pushActive && this._wakeApplies();
    const entry = this._takeRecorder();
    this.pushActive = false;
    this.pushToken = null;
    this.session = null;
    this.vad.reset();
    // listenMode is left as it is so a flushed clip is judged by the mode it was spoken in.
    this._set({ session: null, capturing: false, push: false });
    if (flush && wasCapturing && entry) {
      this._transcribeEntry(entry, { wake });
    } else {
      this._discardEntry(entry);
    }
    this._release("session");
    this._release("push");
  }

  /** Start push-to-talk: record from now until pushEnd(). */
  async pushStart() {
    if (this.shutDown || this.pushActive) return this.pushActive;
    if (!this._micAllowed()) return false;
    this.clearError();
    if (this.playbackActive) this.stopSpeaking();
    const token = {};
    this.pushToken = token;
    this.pushActive = true;
    this.pushStartedAt = this.deps.now();
    this._set({ push: true });

    const ok = await this._acquire("push");
    if (this.pushToken !== token || !this.pushActive) {
      // Released before the mic was ready (often: while the permission prompt was up).
      if (ok) {
        this._release("push");
        this._notice(translate("messages.voiceSession.micReady"));
      }
      return false;
    }
    if (!ok) {
      this.pushActive = false;
      this.pushToken = null;
      this._set({ push: false });
      return false;
    }
    // Whatever a hands-free recording held is superseded by this deliberate one.
    this._discardEntry(this._takeRecorder());
    this.vad.reset();
    this._set({ capturing: false });
    this.pushStartedAt = this.deps.now();
    this._startRecorder();
    return true;
  }

  /** Release push-to-talk: transcribe and send what was held. */
  pushEnd() {
    if (!this.pushActive) return;
    const token = this.pushToken;
    this.pushActive = false;
    this.pushToken = null;
    this._set({ push: false });
    if (!token || !this.stream || this.opening) return;

    const heldMs = this.deps.now() - this.pushStartedAt;
    const entry = this._takeRecorder();
    if (heldMs < MIN_PUSH_MS) {
      this._discardEntry(entry);
      this._notice(translate("messages.voiceSession.holdLonger"));
    } else {
      this._transcribeEntry(entry, { wake: false });
    }
    if (this.session === "handsfree" && !this.playbackActive) this._startRecorder();
    this._release("push");
  }

  /** Hold the mic open without a session, e.g. for a level meter. */
  acquire(reason = "meter") {
    if (this.shutDown) return Promise.resolve(false);
    if (!this.deps.isSecureContext() || !this.deps.hasMediaDevices()) {
      this._fail("insecure", translate("messages.voice.micNeedsSecureContext"));
      return Promise.resolve(false);
    }
    this.clearError();
    return this._acquire(reason);
  }

  /** Let go of a hold taken with acquire(). */
  release(reason = "meter") {
    this._release(reason);
  }

  /** Called by the sink when the reply to the last turn has finished. */
  replyFinished() {
    clearTimeout(this.replyTimer);
    this.replyTimer = null;
    this._set({ awaitingReply: false });
  }

  /** Tell the engine whether a spoken reply is playing (VoiceContext.isPlaying). */
  setPlaybackActive(active) {
    if (active) {
      clearTimeout(this.tailTimer);
      this.tailTimer = null;
      if (this.playbackActive) return;
      this.playbackActive = true;
      this.bargeVad.reset();
      this._clearNoSpeechTimer();
      clearTimeout(this.replyTimer);
      this.replyTimer = null;
      this._set({ speaking: true, awaitingReply: false });
      if (this.recorder && !this.pushActive) {
        if (this.vad.speaking) {
          // Someone was mid-sentence when the reply started: send what they said.
          this.vad.reset();
          this._set({ capturing: false });
          this._transcribeEntry(this._takeRecorder(), { wake: this._wakeApplies() });
        } else {
          this._discardEntry(this._takeRecorder());
        }
      }
      return;
    }
    if (!this.playbackActive || this.tailTimer) return;
    this.tailTimer = setTimeout(() => {
      this.tailTimer = null;
      this._endPlayback();
    }, PLAYBACK_TAIL_MS);
  }

  /** Silence a reply that is being spoken. */
  stopSpeaking() {
    try {
      this.deps.stopPlayback();
    } catch {
      // Nothing was playing.
    }
    clearTimeout(this.tailTimer);
    this.tailTimer = null;
    if (this.playbackActive) this._endPlayback();
  }

  clearError() {
    if (this.store.getState().error) this._set({ error: null, errorCode: null });
  }

  /** Re-enable after shutdown() (React StrictMode mounts effects twice). */
  attach() {
    this.shutDown = false;
  }

  /** Release the mic, timers and requests. Registered sinks stay. */
  shutdown() {
    this.shutDown = true;
    for (const controller of this.sttControllers) {
      try {
        controller.abort();
      } catch {
        // Already finished.
      }
    }
    this.sttControllers.clear();
    [this.tailTimer, this.noSpeechTimer, this.wakeTimer, this.noticeTimer, this.replyTimer].forEach(clearTimeout);
    this.tailTimer = this.noSpeechTimer = this.wakeTimer = this.noticeTimer = this.replyTimer = null;
    this.session = null;
    this.pushActive = false;
    this.pushToken = null;
    this.playbackActive = false;
    this.holders.clear();
    this._closeStream();
    this._set({
      session: null, push: false, capturing: false, speaking: false,
      sending: false, awaitingReply: false, pendingStt: 0, listenMode: "active",
    });
  }

  // ------------------------------------------------------------ mic and graph

  _micAllowed() {
    if (this.settings.micEnabled === false) {
      this._fail("mic-off", translate("messages.voiceSession.micOff"));
      return false;
    }
    if (!this.deps.isSecureContext() || !this.deps.hasMediaDevices()) {
      this._fail("insecure", translate("messages.voice.micNeedsSecureContext"));
      return false;
    }
    return true;
  }

  async _acquire(reason) {
    this.holders.add(reason);
    if (this.stream && !this.opening) return true;
    if (!this.opening) {
      this.opening = this._openStream().finally(() => {
        this.opening = null;
      });
    }
    const ok = await this.opening;
    if (!ok) {
      this.holders.delete(reason);
      return false;
    }
    return this.holders.has(reason);
  }

  _release(reason) {
    if (!this.holders.delete(reason)) return;
    // While the stream is still opening, _openStream closes it on arrival.
    if (this.holders.size === 0 && !this.opening) this._closeStream();
  }

  async _openStream() {
    this._set({ requesting: true });
    let stream;
    try {
      stream = await this.deps.getUserMedia({
        audio: {
          echoCancellation: this.settings.echoCancellation,
          noiseSuppression: this.settings.noiseSuppression,
          autoGainControl: this.settings.autoGainControl,
          channelCount: 1,
        },
      });
    } catch (err) {
      this._set({ requesting: false });
      const known = MEDIA_ERRORS[err?.name];
      const [code, message] = known ? [known[0], known[1]()] : [
        "mic",
        err?.message ? translate("messages.voiceSession.micStartFailedReason", { reason: err.message }) : translate("messages.voiceSession.micStartFailed"),
      ];
      if (code === "denied") this._set({ permission: "denied" });
      this._fail(code, message);
      return false;
    }
    this._set({ requesting: false, permission: "granted" });
    if (this.holders.size === 0 || this.shutDown) {
      stopTracks(stream);
      return false;
    }
    this.stream = stream;
    try {
      await this._buildGraph(stream);
    } catch (err) {
      this._closeStream();
      this._fail("audio", translate("messages.voiceSession.audioStartFailed", { reason: err?.message || err }));
      return false;
    }
    if (this.holders.size === 0 || this.shutDown) {
      this._closeStream();
      return false;
    }
    stream.getAudioTracks?.().forEach((track) => {
      track.addEventListener?.("ended", () => this._onTrackEnded());
    });
    this._set({ micOpen: true });
    return true;
  }

  async _buildGraph(stream) {
    const ctx = this.deps.createAudioContext();
    this.audioCtx = ctx;
    if (ctx.state === "suspended") {
      try {
        await ctx.resume();
      } catch {
        // The worklet still receives frames once the page has been interacted with.
      }
    }
    const source = ctx.createMediaStreamSource(stream);
    this.graph.source = source;
    try {
      if (!ctx.audioWorklet) throw new Error("AudioWorklet unavailable");
      await ctx.audioWorklet.addModule(this.deps.workletUrl);
      const node = this.deps.createWorkletNode(ctx, "vad-processor");
      // Routed to the output through a zero gain: some browsers only run
      // nodes that reach the destination.
      const mute = ctx.createGain();
      mute.gain.value = 0;
      source.connect(node);
      node.connect(mute);
      mute.connect(ctx.destination);
      node.port.onmessage = (event) => this._onFrame(Number(event?.data?.volume) || 0);
      this.graph.node = node;
      this.graph.mute = mute;
    } catch {
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      const buffer = new Float32Array(analyser.fftSize);
      this.graph.analyser = analyser;
      this.pollTimer = setInterval(() => {
        analyser.getFloatTimeDomainData(buffer);
        let sum = 0;
        for (let i = 0; i < buffer.length; i++) sum += buffer[i] * buffer[i];
        this._onFrame(Math.sqrt(sum / buffer.length));
      }, LEVEL_INTERVAL_MS);
    }
  }

  _closeStream() {
    this._discardEntry(this._takeRecorder());
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.graph.node?.port) this.graph.node.port.onmessage = null;
    safeDisconnect(this.graph.source);
    safeDisconnect(this.graph.node);
    safeDisconnect(this.graph.mute);
    safeDisconnect(this.graph.analyser);
    this.graph = {};
    if (this.audioCtx) {
      try {
        const closing = this.audioCtx.close?.();
        closing?.catch?.(() => {});
      } catch {
        // Already closed.
      }
      this.audioCtx = null;
    }
    stopTracks(this.stream);
    this.stream = null;
    this.vad.reset();
    this._set({ micOpen: false, level: 0, capturing: false });
  }

  _onTrackEnded() {
    if (!this.stream) return;
    this._fail("ended", translate("messages.voiceSession.micEnded"));
    this.session = null;
    this.pushActive = false;
    this.holders.clear();
    this._closeStream();
    this._set({ session: null, push: false });
  }

  // ------------------------------------------------------------------ frames

  _onFrame(level) {
    const now = this.deps.now();
    if (now - this.lastLevelAt >= LEVEL_INTERVAL_MS) {
      this.lastLevelAt = now;
      this._set({ level });
    }
    if (!this.stream || this.pushActive || !this.session) return;
    if (now < this.chimeGuardUntil) return;

    if (this.playbackActive) {
      if (!this.settings.bargeIn) return;
      if (this.bargeVad.push(level, now)?.type === "speech-start") {
        this.stopSpeaking();
      }
      return;
    }

    if (!this.recorder) this._startRecorder();
    const event = this.vad.push(level, now);
    if (!event) {
      if (!this.vad.speaking && !this.vad.pending && this.recorder &&
          now - this.recorder.startedAt > IDLE_RECYCLE_MS) {
        this._discardEntry(this._takeRecorder());
        this._startRecorder();
      }
      return;
    }
    if (event.type === "speech-start") {
      this._clearNoSpeechTimer();
      this._set({ capturing: true });
    } else if (event.type === "discard") {
      this._set({ capturing: false });
      this._discardEntry(this._takeRecorder());
      this._startRecorder();
      if (this.session === "utterance") this._armNoSpeechTimer();
    } else if (event.type === "speech-end") {
      this._set({ capturing: false });
      this._finishUtterance();
    }
  }

  _finishUtterance() {
    const entry = this._takeRecorder();
    const kind = this.session;
    // Stop the recorder before the mic closes so its last chunk is kept.
    this._transcribeEntry(entry, { wake: this._wakeApplies() });
    if (kind === "utterance") {
      this.session = null;
      this._set({ session: null });
      this._release("session");
    } else if (kind === "handsfree" && !this.playbackActive) {
      this._startRecorder();
    }
  }

  _wakeApplies() {
    return this.session === "handsfree" && this.settings.wakeWordEnabled;
  }

  // ---------------------------------------------------------------- recorder

  _pickMime() {
    return MIME_CANDIDATES.find((type) => this.deps.isTypeSupported(type)) || "";
  }

  _startRecorder() {
    if (!this.stream || this.recorder) return;
    const mimeType = this._pickMime();
    let rec;
    try {
      rec = this.deps.createRecorder(this.stream, mimeType ? { mimeType } : undefined);
    } catch (err) {
      this._fail("recorder", translate("messages.voiceSession.recorderUnsupported", { reason: err?.message || err }));
      this.stop({ flush: false });
      return;
    }
    const entry = { rec, chunks: [], startedAt: this.deps.now(), mimeType: rec.mimeType || mimeType || "audio/webm" };
    rec.ondataavailable = (event) => {
      if (event?.data && event.data.size > 0) entry.chunks.push(event.data);
    };
    rec.onerror = (event) => {
      this._notice(translate("messages.voiceSession.recordingError", { reason: event?.error?.message || translate("messages.voiceSession.unknownReason") }));
    };
    try {
      rec.start(RECORDER_TIMESLICE_MS);
    } catch (err) {
      this._fail("recorder", translate("messages.voiceSession.recorderStartFailed", { reason: err?.message || err }));
      this.stop({ flush: false });
      return;
    }
    this.recorder = entry;
  }

  _takeRecorder() {
    const entry = this.recorder;
    this.recorder = null;
    return entry;
  }

  /** Stop a recorder and resolve to its audio (null when it holds none). */
  _stopEntry(entry) {
    return new Promise((resolve) => {
      if (!entry) {
        resolve(null);
        return;
      }
      const finish = () =>
        resolve(entry.chunks.length ? new Blob(entry.chunks, { type: entry.mimeType }) : null);
      if (entry.rec.state === "inactive") {
        finish();
        return;
      }
      entry.rec.onstop = finish;
      try {
        entry.rec.stop();
      } catch {
        finish();
      }
    });
  }

  _discardEntry(entry) {
    if (!entry) return;
    entry.rec.ondataavailable = null;
    try {
      if (entry.rec.state !== "inactive") entry.rec.stop();
    } catch {
      // Already stopped.
    }
  }

  // ------------------------------------------------------- transcribe + route

  /**
   * Queue a finished recording for transcription. `wake` means the hands-free
   * wake-phrase rules apply to it; whether the session is passive is read when
   * its turn comes, so "Hey <name>" in one clip opens the next.
   */
  _transcribeEntry(entry, { wake }) {
    if (!entry) return this.sttChain;
    const blobReady = this._stopEntry(entry);
    this._set((s) => ({ pendingStt: s.pendingStt + 1 }));
    // One request at a time keeps turns in the order they were spoken.
    this.sttChain = this.sttChain
      .then(async () => {
        const blob = await blobReady;
        await this._transcribeAndRoute(blob, { wake });
      })
      .catch(() => {})
      .finally(() => {
        this._set((s) => ({ pendingStt: Math.max(0, s.pendingStt - 1) }));
      });
    return this.sttChain;
  }

  async _transcribeAndRoute(blob, { wake }) {
    if (!blob || blob.size < MIN_BLOB_BYTES || this.shutDown) return;
    const controller = new AbortController();
    this.sttControllers.add(controller);
    let result;
    try {
      result = await this.deps.transcribe(blob, { signal: controller.signal });
    } catch (err) {
      this._onSttError(err);
      return;
    } finally {
      this.sttControllers.delete(controller);
    }
    if (this.shutDown) return;
    const text = String(result?.text || result?.transcribed_text || "").trim();
    if (text) await this._route(text, { wake });
  }

  _onSttError(err) {
    if (err?.name === "AbortError") return;
    const status = err?.status;
    const message = err?.message || translate("messages.voiceSession.sttFailed");
    if (status === 400 && /no speech/i.test(message)) return;
    if (status === 409 || err?.data?.code === "SPEECH_MODEL_MISSING") {
      this._fail("model", message);
      this.stop({ flush: false });
      return;
    }
    if (status === 429) {
      this._notice(translate("messages.voiceSession.rateLimited"));
      return;
    }
    if (status === 503) {
      this._notice(translate("messages.voiceSession.busy"));
      return;
    }
    if (err?.backendOffline) {
      this._notice(translate("messages.voiceSession.backendOffline"));
      return;
    }
    this._notice(translate("messages.voiceSession.transcribeFailed", { reason: message }));
  }

  async _route(text, { wake }) {
    const at = this.deps.now();
    const usesWake = wake && this.settings.wakeWordEnabled;
    if (usesWake && this.store.getState().listenMode === "passive") {
      const wake = checkForWakeWord(text, this.systemName);
      if (!wake.detected) {
        this._record({ text, at, outcome: "ignored" });
        return;
      }
      this._activateWake();
      if (!wake.remainder) {
        this._record({ text, at, outcome: "wake" });
        return;
      }
      await this._deliver(wake.remainder, at, text);
      return;
    }
    if (usesWake) this._armWakeWindow();
    await this._deliver(text, at, text);
  }

  async _deliver(message, at, heard) {
    const sink = this.activeSink();
    if (!sink) {
      this._record({ text: heard, at, outcome: "unsent" });
      this._notice(translate("messages.voiceSession.noChatOpen"));
      return;
    }
    const turn = { id: `voice_${at}_${++this.turnSeq}`, text: message, at };
    this._set({ sending: true });
    try {
      await sink.deliver(turn);
      this._record({ text: heard, at, outcome: "sent" });
      this._set({ sending: false, awaitingReply: true });
      clearTimeout(this.replyTimer);
      this.replyTimer = setTimeout(() => this.replyFinished(), AWAIT_REPLY_TIMEOUT_MS);
    } catch (err) {
      this._record({ text: heard, at, outcome: "failed" });
      this._set({ sending: false });
      this._notice(translate("messages.voiceSession.sendFailed", { reason: err?.message || err }));
    }
  }

  // --------------------------------------------------------------- wake word

  _activateWake() {
    this._set({ listenMode: "active" });
    this.chimeGuardUntil = this.deps.now() + CHIME_GUARD_MS;
    this.deps.chime(this.audioCtx);
    this._armWakeWindow();
  }

  _armWakeWindow() {
    this._clearWakeTimer();
    this.wakeTimer = setTimeout(() => {
      this.wakeTimer = null;
      if (this.session !== "handsfree" || !this.settings.wakeWordEnabled) return;
      const s = this.store.getState();
      // A reply still on its way or being spoken keeps the conversation open.
      if (s.awaitingReply || this.playbackActive || s.pendingStt > 0 || this.vad.speaking) {
        this._armWakeWindow();
        return;
      }
      this._set({ listenMode: "passive" });
    }, this.settings.activeListeningDuration);
  }

  _clearWakeTimer() {
    clearTimeout(this.wakeTimer);
    this.wakeTimer = null;
  }

  // ---------------------------------------------------------------- playback

  _endPlayback() {
    this.playbackActive = false;
    this.vad.reset();
    this._set({ speaking: false });
    if (this.session === "utterance") this._armNoSpeechTimer();
    if (this.session === "handsfree" && this.stream) {
      this._startRecorder();
      if (this.settings.wakeWordEnabled && this.store.getState().listenMode === "active") {
        this._armWakeWindow();
      }
    }
  }

  // ------------------------------------------------------------------ timers

  _armNoSpeechTimer() {
    this._clearNoSpeechTimer();
    this.noSpeechTimer = setTimeout(() => {
      this.noSpeechTimer = null;
      if (this.session !== "utterance" || this.vad.speaking) return;
      this.stop({ flush: false });
      this._notice(translate("messages.voiceSession.noSpeechTimeout"));
    }, NO_SPEECH_TIMEOUT_MS);
  }

  _clearNoSpeechTimer() {
    clearTimeout(this.noSpeechTimer);
    this.noSpeechTimer = null;
  }

  // ------------------------------------------------------------------- state

  _set(patch) {
    this.store.setState((s) => {
      const next = { ...s, ...(typeof patch === "function" ? patch(s) : patch) };
      next.phase = derivePhase(next);
      return next;
    });
  }

  _fail(code, message) {
    this._set({ error: message, errorCode: code });
  }

  _notice(message) {
    clearTimeout(this.noticeTimer);
    this._set({ notice: { message, at: this.deps.now() } });
    this.noticeTimer = setTimeout(() => {
      this.noticeTimer = null;
      this._set({ notice: null });
    }, NOTICE_MS);
  }

  _record(entry) {
    this._set((s) => ({
      lastTranscript: entry,
      transcripts: [entry, ...s.transcripts].slice(0, TRANSCRIPT_HISTORY),
    }));
  }
}

export default VoiceSessionEngine;
