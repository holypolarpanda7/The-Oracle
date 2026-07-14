/** Tiny synthesized UI sounds (WebAudio, no assets). All gated behind the
    landing-page toggle, persisted in localStorage. */

const KEY = "oracle-sound";
let ctx: AudioContext | null = null;

export function soundEnabled(): boolean {
  return localStorage.getItem(KEY) !== "off";
}

export function setSoundEnabled(on: boolean) {
  localStorage.setItem(KEY, on ? "on" : "off");
}

function ac(): AudioContext | null {
  if (!soundEnabled()) return null;
  if (!ctx) {
    try { ctx = new AudioContext(); } catch { return null; }
  }
  if (ctx.state === "suspended") void ctx.resume();
  return ctx;
}

function tone(freq: number, dur: number, gain: number, type: OscillatorType,
              when = 0) {
  const a = ac();
  if (!a) return;
  const o = a.createOscillator();
  const g = a.createGain();
  o.type = type;
  o.frequency.value = freq;
  g.gain.setValueAtTime(gain, a.currentTime + when);
  g.gain.exponentialRampToValueAtTime(0.0001, a.currentTime + when + dur);
  o.connect(g).connect(a.destination);
  o.start(a.currentTime + when);
  o.stop(a.currentTime + when + dur + 0.02);
}

let lastBlip = 0;
/** Very quiet parchment-scratch blip while narration types. */
export function typeBlip() {
  const now = performance.now();
  if (now - lastBlip < 90) return;
  lastBlip = now;
  tone(1400 + Math.random() * 500, 0.015, 0.012, "triangle");
}

/** Dice hitting the table. */
export function rollThunk(success?: boolean) {
  tone(140, 0.09, 0.14, "square");
  tone(90, 0.14, 0.12, "square", 0.03);
  if (success === true) tone(660, 0.18, 0.05, "sine", 0.12);
  if (success === false) tone(180, 0.3, 0.06, "sawtooth", 0.12);
}

/** Level-up chime. */
export function levelChime() {
  tone(523, 0.25, 0.07, "sine");
  tone(659, 0.25, 0.07, "sine", 0.12);
  tone(784, 0.4, 0.08, "sine", 0.24);
}

/** Soft brass tick for UI selection. */
export function uiTick() {
  tone(880, 0.04, 0.03, "triangle");
}
