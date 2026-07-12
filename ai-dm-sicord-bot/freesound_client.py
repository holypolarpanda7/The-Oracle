"""
Freesound.org search client for ambient D&D music.

Returns HQ preview MP3 URLs (direct HTTPS links) that the voice-service sidecar
can stream directly through ffmpeg without yt-dlp. Requires a free API key from
https://freesound.org/apiv2/apply — sign up, create an application, copy the
Client secret as your FREESOUND_API_KEY.
"""
import os
from typing import Optional

import asyncio
import aiohttp

FREESOUND_API_KEY: str = os.getenv("FREESOUND_API_KEY", "")
_SEARCH_URL = "https://freesound.org/apiv2/search/text/"

# Mood → Freesound search query. Freesound ANDs every term, so queries are kept
# short (2-3 words) to avoid zero-result matches on the ambient/music library.
MOOD_QUERIES: dict[str, str] = {
    "tavern":             "medieval tavern music",
    "combat":             "battle fantasy music",
    "dungeon":            "dark dungeon ambient",
    "town":               "medieval village ambient",
    "desert":             "desert wind ambient",
    "character_complete": "triumphant fanfare",
    "cc_menu":            "fantasy ambient music",
}

# Broader per-mood queries tried in order when the primary query comes up short
# (Freesound ANDs all terms, so specific queries can easily return 0-2 hits).
MOOD_FALLBACK_QUERIES: dict[str, list[str]] = {
    "tavern":             ["tavern ambience", "medieval lute", "folk fiddle jig"],
    "combat":             ["epic battle drums", "orchestral action"],
    "dungeon":            ["cave drone", "dark ambient drone"],
    "town":               ["medieval market", "village ambience"],
    "desert":             ["desert ambience", "middle eastern oud"],
    "character_complete": ["victory fanfare", "orchestral triumph"],
    "cc_menu":            ["fantasy harp", "calm medieval music"],
}


async def search_tracks(
    query: str,
    *,
    api_key: str = "",
    max_results: int = 8,
) -> list[str]:
    """Search Freesound and return HQ preview MP3 URLs (direct streamable links).

    These URLs end in .mp3 and are handled by the sidecar's direct ffmpeg pipeline
    — no yt-dlp involved, instant start.
    """
    key = api_key or FREESOUND_API_KEY
    if not key:
        print("[freesound] FREESOUND_API_KEY not set — skipping search")
        return []

    params = {
        "query": query,
        "token": key,
        "fields": "id,name,previews",
        # At least 10s long so we're not playing short SFX clips.
        "filter": "duration:[10 TO 600]",
        "sort": "score",
        "page_size": max_results,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                _SEARCH_URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    print(f"[freesound] search HTTP {resp.status} for: {query!r}")
                    return []
                data = await resp.json()
                results = data.get("results", [])
                urls = [
                    r["previews"]["preview-hq-mp3"]
                    for r in results
                    if r.get("previews", {}).get("preview-hq-mp3")
                ]
                print(f"[freesound] '{query}' -> {len(urls)} tracks")
                return urls
    except Exception as e:
        print(f"[freesound] search failed: {e}")
        return []


async def get_mood_tracks(
    mood: str, *, api_key: str = "", min_results: int = 5
) -> list[str]:
    """Return Freesound preview URLs for a named D&D mood.

    Tries the primary mood query first, then broader fallback queries until at
    least ``min_results`` unique tracks are collected (or queries run out).
    """
    queries = [MOOD_QUERIES.get(mood, mood)] + MOOD_FALLBACK_QUERIES.get(mood, [])
    urls: list[str] = []
    seen: set[str] = set()
    for query in queries:
        if len(urls) >= min_results:
            break
        for url in await search_tracks(query, api_key=api_key):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


async def download_track(url: str, dest_path: str, *, retries: int = 3) -> bool:
    """Download a Freesound preview MP3 to a local file. Returns True on success.

    Retries transient failures and removes partial files so a failed download
    never leaves a corrupt track behind.
    """
    for attempt in range(1, retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=120)
                ) as resp:
                    if resp.status != 200:
                        print(f"[freesound] download HTTP {resp.status}: {url}")
                        return False
                    with open(dest_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            f.write(chunk)
            # Guard against truncated/empty writes.
            if os.path.getsize(dest_path) > 1024:
                return True
            raise IOError("downloaded file too small")
        except Exception as e:
            print(f"[freesound] download attempt {attempt}/{retries} failed {url}: {e}")
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
            except OSError:
                pass
            await asyncio.sleep(1)
    return False
