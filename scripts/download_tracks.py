"""
Seed the local audio cache (voice-service/audio/<mood>/) with Freesound tracks.

Run once (or whenever you want to refresh) to pre-download ambient MP3s for each
D&D mood so the bot never needs to hit Freesound at runtime.

Usage:
    uv run python scripts/download_tracks.py

Requires FREESOUND_API_KEY in oracle-dm-backend/backend-cred.env or the environment.
Downloads ~5 tracks per mood into voice-service/audio/<mood>/*.mp3.
"""
import asyncio
import os
import sys
from pathlib import Path

# Allow importing from the bot directory.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ai-dm-sicord-bot"))

from dotenv import load_dotenv
load_dotenv(ROOT / "oracle-dm-backend" / "backend-cred.env")

import freesound_client  # noqa: E402  (after sys.path patch)

AUDIO_ROOT = ROOT / "voice-service" / "audio"
TRACKS_PER_MOOD = 5


async def seed_mood(mood: str, api_key: str) -> int:
    dest_dir = AUDIO_ROOT / mood
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Skip moods that already have enough files.
    existing = list(dest_dir.glob("*.mp3")) + list(dest_dir.glob("*.ogg"))
    if len(existing) >= TRACKS_PER_MOOD:
        print(f"[seed] '{mood}' already has {len(existing)} files — skipping")
        return 0

    print(f"[seed] Searching Freesound for '{mood}'...")
    urls = await freesound_client.get_mood_tracks(mood, api_key=api_key)
    if not urls:
        print(f"[seed] No results for '{mood}'")
        return 0

    downloaded = 0
    for i, url in enumerate(urls[:TRACKS_PER_MOOD], start=1):
        filename = f"{mood}_{i:02d}.mp3"
        dest = dest_dir / filename
        if dest.exists():
            print(f"  [skip] {filename} already exists")
            downloaded += 1
            continue
        print(f"  [dl]   {filename} <- {url}")
        ok = await freesound_client.download_track(url, str(dest))
        if ok:
            downloaded += 1
        else:
            print(f"  [fail] {filename}")

    print(f"[seed] '{mood}' -> {downloaded} files in {dest_dir}")
    return downloaded


async def main() -> None:
    api_key = os.getenv("FREESOUND_API_KEY", "")
    if not api_key:
        print("ERROR: FREESOUND_API_KEY not set.")
        print("  Get a free key at https://freesound.org/apiv2/apply")
        print("  Then add FREESOUND_API_KEY=<key> to oracle-dm-backend/backend-cred.env")
        sys.exit(1)

    moods = list(freesound_client.MOOD_QUERIES.keys())
    print(f"Seeding {len(moods)} moods into {AUDIO_ROOT}\n")
    total = 0
    for mood in moods:
        total += await seed_mood(mood, api_key)

    print(f"\nDone. {total} tracks downloaded across {len(moods)} moods.")
    print("Restart the bot - playlists will now use local files automatically.")


if __name__ == "__main__":
    asyncio.run(main())
