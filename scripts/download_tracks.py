"""
Seed the local audio cache (voice-service/audio/<mood>/) with Freesound tracks.

Run once (or whenever you want to refresh) to pre-download ambient MP3s for each
D&D mood so the bot never needs to hit Freesound at runtime.

Every track is CHECKED before it is kept. Freesound is a sound-effects library
first, so even a music query returns the occasional field recording of a room —
and a room recording in a music playlist is what turned a character-creation
screen into four minutes of tavern babble. `scripts/audio_classify.py` measures
the difference (music has a pulse; a crowd does not), and a download that fails
that test is deleted and the next candidate tried. Quarantined tracks in
`<mood>/ambience/` are not counted toward the target, so re-running tops a mood
back up after a `--quarantine` pass.

Usage:
    uv run --with soundfile python scripts/download_tracks.py [mood ...]

Requires FREESOUND_API_KEY in oracle-dm-backend/backend-cred.env or the environment.
Downloads ~5 tracks per mood into voice-service/audio/<mood>/*.mp3.
"""
import asyncio
import hashlib
import os
import sys
from pathlib import Path

# Allow importing from the bot directory.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ai-dm-sicord-bot"))

from dotenv import load_dotenv
load_dotenv(ROOT / "oracle-dm-backend" / "backend-cred.env")

import freesound_client  # noqa: E402  (after sys.path patch)

sys.path.insert(0, str(ROOT / "scripts"))
import audio_classify  # noqa: E402  (after sys.path patch)

AUDIO_ROOT = ROOT / "voice-service" / "audio"
TRACKS_PER_MOOD = 5
# How many candidates to try per mood before giving up on filling it — a music
# query can still be mostly ambience, and each rejection costs one download.
MAX_CANDIDATES = 16


async def seed_mood(mood: str, api_key: str) -> int:
    dest_dir = AUDIO_ROOT / mood
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Only loose files count. Anything already quarantined into <mood>/ambience/
    # was judged not-music once; counting it would leave the mood permanently
    # "full" of tracks the loader deliberately ignores.
    existing = sorted(dest_dir.glob("*.mp3")) + sorted(dest_dir.glob("*.ogg"))
    if len(existing) >= TRACKS_PER_MOOD:
        print(f"[seed] '{mood}' already has {len(existing)} files — skipping")
        return 0

    print(f"[seed] Searching Freesound for '{mood}'...")
    urls = await freesound_client.get_mood_tracks(
        mood, api_key=api_key, min_results=MAX_CANDIDATES)
    if not urls:
        print(f"[seed] No results for '{mood}'")
        return 0

    # Content hashes of everything already here, QUARANTINE INCLUDED. Freesound
    # returns the same top hits for the same query, so topping a mood back up
    # re-downloads the tracks it already has — and re-downloads the ambience it
    # just rejected, every single run. Identity is the bytes, not the filename.
    def _digest(path: Path) -> str:
        return hashlib.md5(path.read_bytes()).hexdigest()

    seen_hashes = set()
    for prior in list(dest_dir.glob("*.mp3")) + list(dest_dir.glob("*.ogg")) \
            + list((dest_dir / "ambience").glob("*.*")):
        try:
            seen_hashes.add(_digest(prior))
        except OSError:
            pass

    taken = {p.name for p in existing}
    def _next_slot() -> str:
        for i in range(1, 99):
            name = f"{mood}_{i:02d}.mp3"
            if name not in taken and not (dest_dir / name).exists():
                return name
        raise RuntimeError("no free slot")

    kept = 0
    for url in urls[:MAX_CANDIDATES]:
        if len(existing) + kept >= TRACKS_PER_MOOD:
            break
        filename = _next_slot()
        dest = dest_dir / filename
        print(f"  [dl]   {filename} <- {url}")
        if not await freesound_client.download_track(url, str(dest)):
            print(f"  [fail] {filename}")
            continue
        digest = _digest(dest)
        if digest in seen_hashes:
            print(f"  [drop] {filename} — already have this track")
            dest.unlink(missing_ok=True)
            continue
        seen_hashes.add(digest)
        # The check that stops a crowd recording becoming background music.
        try:
            verdict = audio_classify.classify(dest)
        except Exception as e:  # noqa: BLE001  (unreadable = not usable)
            print(f"  [drop] {filename} — unreadable ({e})")
            dest.unlink(missing_ok=True)
            continue
        if not audio_classify.is_music(verdict["verdict"]):
            print(f"  [drop] {filename} — {verdict['verdict']}, not music "
                  f"(beat {verdict['beat']:.3f})")
            dest.unlink(missing_ok=True)
            continue
        print(f"  [keep] {filename} — {verdict['verdict']} "
              f"(beat {verdict['beat']:.3f})")
        taken.add(filename)
        kept += 1

    print(f"[seed] '{mood}' -> {kept} new file(s) in {dest_dir}")
    return kept


async def main() -> None:
    api_key = os.getenv("FREESOUND_API_KEY", "")
    if not api_key:
        print("ERROR: FREESOUND_API_KEY not set.")
        print("  Get a free key at https://freesound.org/apiv2/apply")
        print("  Then add FREESOUND_API_KEY=<key> to oracle-dm-backend/backend-cred.env")
        sys.exit(1)

    moods = [a for a in sys.argv[1:] if not a.startswith("-")] \
        or list(freesound_client.MOOD_QUERIES.keys())
    print(f"Seeding {len(moods)} moods into {AUDIO_ROOT}\n")
    total = 0
    for mood in moods:
        total += await seed_mood(mood, api_key)

    print(f"\nDone. {total} tracks downloaded across {len(moods)} moods.")
    print("Restart the bot - playlists will now use local files automatically.")


if __name__ == "__main__":
    asyncio.run(main())
