"""Tell MUSIC from AMBIENCE in the cached mood tracks.

Freesound is a sound-effects library first and a music library second, so a
query padded with "tavern ambience" comes back with a field recording of a
busy room. That is fine for a table mid-scene and wrong for a menu you sit on
for ten minutes, and nothing in the pipeline could tell the two apart — the
files are named `tavern_02.mp3` either way.

Two measurements separate them, and neither needs an ear:

* **Spectral flatness** — the ratio of the geometric to the arithmetic mean of
  the power spectrum. A tone concentrates its energy in a few bins (flatness
  near 0); crowd babble and wind spread it across all of them (flatness toward
  1). This is the same statistic a noise gate uses to decide "is this signal
  or hiss". Measured over 100 Hz - 8 kHz ONLY: an MP3 low-passes everything
  above ~16 kHz to near-silence, and a geometric mean over bins that are all
  but zero collapses to zero for music and noise alike — measured full-band,
  every track in the library scores 0.000 and the statistic says nothing.
* **Beat strength** — the peak of the autocorrelation of the onset envelope
  over 60-180 BPM. Music has a pulse and repeats on it; a room of people does
  not.

Neither is conclusive alone (an organ drone is tonal and beatless; a drum
circle is noisy and rhythmic), so the verdict takes both and reports the
numbers, because the call on a borderline track belongs to whoever is
listening, not to this script.

Usage:
    uv run --with soundfile python scripts/audio_classify.py [mood ...]
    uv run --with soundfile python scripts/audio_classify.py --quarantine tavern

``--quarantine`` moves the beatless tracks of a MUSIC mood into
``voice-service/audio/<mood>/ambience/``. Nothing is deleted, and the loader
needs no change: ``load_playlist`` globs ``*.mp3`` non-recursively, so a
subfolder is already invisible to it. Move a file back to undo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
AUDIO_ROOT = ROOT / "voice-service" / "audio"

# Sampled from the middle of the file — intros fade in, and the last seconds of
# a preview are often silence.
SAMPLE_SECONDS = 30
FRAME = 2048
HOP = 512

# Above this, the spectrum is closer to noise than to notes.
FLAT_NOISE = 0.28
# Below this, the onset envelope has no pulse worth calling a beat.
BEAT_WEAK = 0.22


def _load(path: Path) -> tuple[np.ndarray, int]:
    import soundfile as sf
    info = sf.info(str(path))
    sr = info.samplerate
    want = SAMPLE_SECONDS * sr
    start = max(0, (info.frames - want) // 2)
    data, sr = sf.read(str(path), start=start, frames=min(want, info.frames),
                       dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


def _spectrogram(y: np.ndarray) -> np.ndarray:
    n = 1 + (len(y) - FRAME) // HOP
    if n < 4:
        return np.zeros((FRAME // 2 + 1, 1))
    win = np.hanning(FRAME).astype(np.float32)
    frames = np.lib.stride_tricks.as_strided(
        y, shape=(n, FRAME), strides=(y.strides[0] * HOP, y.strides[0]))
    return np.abs(np.fft.rfft(frames * win, axis=1)).T


def spectral_flatness(mag: np.ndarray, sr: int) -> float:
    """Geometric / arithmetic mean of power over 100 Hz - 8 kHz. 0=tonal, 1=noise."""
    freqs = np.fft.rfftfreq(FRAME, 1.0 / sr)
    band = (freqs >= 100.0) & (freqs <= 8000.0)
    p = mag[band] ** 2 + 1e-12
    geo = np.exp(np.mean(np.log(p), axis=0))
    ari = np.mean(p, axis=0)
    return float(np.median(geo / ari))


def beat_strength(mag: np.ndarray, sr: int) -> float:
    """Peak autocorrelation of the onset envelope within 60-180 BPM."""
    # Onset envelope: positive frame-to-frame spectral change (spectral flux).
    flux = np.maximum(0.0, np.diff(mag, axis=1)).sum(axis=0)
    if flux.size < 64 or not np.any(flux):
        return 0.0
    flux = flux - flux.mean()
    ac = np.correlate(flux, flux, mode="full")[flux.size - 1:]
    if ac[0] <= 0:
        return 0.0
    ac = ac / ac[0]
    fps = sr / HOP
    lo = max(1, int(fps * 60.0 / 180.0))      # 180 BPM
    hi = min(ac.size - 1, int(fps * 60.0 / 60.0))   # 60 BPM
    return float(ac[lo:hi].max()) if hi > lo else 0.0


def classify(path: Path) -> dict:
    y, sr = _load(path)
    mag = _spectrogram(y)
    flat = spectral_flatness(mag, sr)
    beat = beat_strength(mag, sr)
    noisy = flat >= FLAT_NOISE
    pulsed = beat >= BEAT_WEAK
    if noisy and not pulsed:
        verdict = "AMBIENCE"
    elif not noisy and pulsed:
        verdict = "MUSIC"
    elif pulsed:
        verdict = "music?"     # noisy but pulsed — percussion, or a busy mix
    else:
        verdict = "ambience?"  # tonal but beatless — a drone, or a slow pad
    return {"file": path.name, "flatness": flat, "beat": beat,
            "verdict": verdict}


def main(argv: list[str]) -> int:
    quarantine = "--quarantine" in argv
    moods = [a for a in argv if not a.startswith("-")]
    moods = moods or sorted(p.name for p in AUDIO_ROOT.iterdir() if p.is_dir())
    print(f"{'file':34} {'flatness':>9} {'beat':>7}  verdict")
    print("-" * 68)
    moved = 0
    for mood in moods:
        d = AUDIO_ROOT / mood
        if not d.is_dir():
            print(f"[skip] no such mood: {mood}")
            continue
        print(f"\n[{mood}]")
        for f in sorted(d.glob("*.mp3")) + sorted(d.glob("*.ogg")):
            try:
                r = classify(f)
            except Exception as e:  # noqa: BLE001
                print(f"  {f.name:32} — unreadable ({e})")
                continue
            note = ""
            if quarantine and r["verdict"].startswith("ambience"):
                dest = d / "ambience"
                dest.mkdir(exist_ok=True)
                f.rename(dest / f.name)
                moved += 1
                note = "  -> ambience/"
            print(f"  {r['file']:32} {r['flatness']:9.3f} {r['beat']:7.3f}  "
                  f"{r['verdict']}{note}")
    print("\nflatness >= %.2f is noise-like; beat >= %.2f is a pulse."
          % (FLAT_NOISE, BEAT_WEAK))
    if quarantine:
        print(f"quarantined {moved} track(s) — move them back to undo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
