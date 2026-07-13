"""
Music player module — client for the DAVE-capable Node voice sidecar.

Discord enforces the DAVE (E2EE) voice protocol; clients without it are kicked
from voice with close code 4017. discord.py and Lavalink both lack DAVE, so voice
playback now lives in the ``voice-service/`` Node process (built on
``@discordjs/voice`` + ``@snazzah/davey``). This module keeps the same public API
the rest of the bot already uses (``play_music_in_channel``,
``play_query_in_channel``, ``stop_music_in_channel``) but drives the sidecar over
a small localhost HTTP API instead of connecting to voice directly.
"""
import asyncio
import atexit
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Optional

import aiohttp
import discord


# Best-effort local mirror of sidecar state so callers can cheaply check whether
# a channel is "active" (e.g. the bot's !voicetest / toggle logic).
# voice_channel_id -> {"playlist": str, "guild_id": int}
active_players: Dict[int, dict] = {}

# voice_channel_id -> playlist_name (kept for backward compatibility)
current_playlists: Dict[int, str] = {}

# voice_channel_id -> guild_id, so stop() can address the sidecar (which is
# keyed by guild) even though callers only pass a channel id.
_channel_guild: Dict[int, int] = {}

# The spawned Node sidecar process (when the bot manages its lifecycle).
_voice_process: Optional[subprocess.Popen] = None

# --- Sidecar connection config -------------------------------------------------

_DEFAULT_HOST = os.getenv("VOICE_SERVICE_HOST", "127.0.0.1")
_DEFAULT_PORT = os.getenv("VOICE_SERVICE_PORT", "8790")
VOICE_SERVICE_URL = os.getenv(
    "VOICE_SERVICE_URL", f"http://{_DEFAULT_HOST}:{_DEFAULT_PORT}"
).rstrip("/")
_SECRET = os.getenv("VOICE_SERVICE_SECRET", "")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VOICE_DIR = _REPO_ROOT / "voice-service"

_ready = False


def _headers() -> dict:
    return {"X-Voice-Token": _SECRET} if _SECRET else {}


async def _post(path: str, payload: dict, *, timeout: float = 30.0) -> Optional[dict]:
    """POST JSON to the sidecar; returns the parsed body or None on failure."""
    url = f"{VOICE_SERVICE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status >= 400:
                    print(f"[music] sidecar {path} -> {resp.status}: {data}")
                    return None
                return data
    except Exception as e:
        print(f"[music] sidecar {path} request failed: {e}")
        return None


async def _get(path: str, params: dict, *, timeout: float = 10.0) -> Optional[dict]:
    url = f"{VOICE_SERVICE_URL}{path}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, params=params, headers=_headers(),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                return await resp.json(content_type=None)
    except Exception as e:
        print(f"[music] sidecar GET {path} failed: {e}")
        return None


# --- Sidecar lifecycle ---------------------------------------------------------

def start_voice_service(token: Optional[str] = None) -> bool:
    """Spawn the Node voice sidecar as a subprocess (mirrors the old Lavalink
    starter). Runs ``npm install`` first if dependencies are missing. Returns
    True if the process was started."""
    global _voice_process

    if not _VOICE_DIR.exists():
        print(f"[voice-service] Directory not found: {_VOICE_DIR}")
        return False

    node = shutil.which("node")
    if not node:
        print("[voice-service] ERROR: Node.js not found on PATH (need >= 22.12).")
        return False

    # One-time dependency install.
    if not (_VOICE_DIR / "node_modules").exists():
        npm = shutil.which("npm")
        if not npm:
            print("[voice-service] ERROR: node_modules missing and npm not found. "
                  "Run `npm install` in voice-service/ manually.")
            return False
        print("[voice-service] Installing dependencies (first run, this may take a while)...")
        try:
            with open(_VOICE_DIR / "npm-install.log", "w") as log:
                subprocess.run(
                    [npm, "install"], cwd=str(_VOICE_DIR),
                    stdout=log, stderr=subprocess.STDOUT, check=True, shell=False,
                )
            print("[voice-service] Dependencies installed.")
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"[voice-service] npm install failed ({e}); see voice-service/npm-install.log")
            return False

    env = os.environ.copy()
    if token:
        env["DISCORD_TOKEN"] = token
    env.setdefault("VOICE_SERVICE_HOST", _DEFAULT_HOST)
    env.setdefault("VOICE_SERVICE_PORT", str(_DEFAULT_PORT))
    if _SECRET:
        env["VOICE_SERVICE_SECRET"] = _SECRET

    try:
        # Append mode keeps prior runs for postmortems; line buffering helps
        # logs appear promptly during active debugging.
        log_file = open(_VOICE_DIR / "voice-service.log", "a", encoding="utf-8", buffering=1)
        _voice_process = subprocess.Popen(
            [node, "index.js"], cwd=str(_VOICE_DIR),
            stdout=log_file, stderr=subprocess.STDOUT, env=env,
            text=True,
        )
        print(f"[voice-service] Started (PID {_voice_process.pid}); logs: voice-service/voice-service.log")
        return True
    except Exception as e:
        print(f"[voice-service] Failed to start: {e}")
        return False


def stop_voice_service() -> None:
    """Terminate the sidecar subprocess if we started it."""
    global _voice_process
    if _voice_process:
        print("[voice-service] Stopping...")
        try:
            _voice_process.terminate()
            _voice_process.wait(timeout=5)
        except Exception:
            try:
                _voice_process.kill()
                _voice_process.wait()
            except Exception:
                pass
        finally:
            _voice_process = None


atexit.register(stop_voice_service)


async def setup_voice_service(bot: Optional[discord.Client] = None, *, retries: int = 20) -> bool:
    """Wait for the sidecar HTTP API to report ready. Returns True once healthy."""
    global _ready
    for attempt in range(1, retries + 1):
        data = await _get("/health", {}, timeout=5.0)
        if data and data.get("ok") and data.get("ready"):
            _ready = True
            print(f"[voice-service] Ready (guilds={data.get('guilds')}, dave={data.get('dave')})")
            return True
        await asyncio.sleep(1.0)
    print(f"[voice-service] Not ready after {retries}s; music will retry on demand.")
    return False


async def ensure_voice_service_ready(bot: Optional[discord.Client] = None) -> bool:
    """Best-effort readiness check before a play call."""
    global _ready
    if _ready:
        return True
    data = await _get("/health", {}, timeout=5.0)
    if data and data.get("ok") and data.get("ready"):
        _ready = True
        return True
    return False


# --- Playlist loading ----------------------------------------------------------

async def load_playlist(playlist_name: str) -> list[str]:
    """Load tracks for a mood in priority order:

    1. ``playlists/<name>.txt`` — explicit overrides (YouTube URLs, custom links).
       Lines starting with ``#`` are comments.  If the file is non-empty, it wins.
    2. ``voice-service/audio/<name>/`` — local pre-downloaded MP3/OGG files.
       Tracks are passed to the sidecar as ``localfile:<abs_path>`` so the Node
       process streams them through ffmpeg with no yt-dlp at all.
    3. Freesound API — searches by mood keyword, returns direct HTTPS preview MP3
       URLs.  Requires ``FREESOUND_API_KEY`` in the environment.
    """
    # --- 1. Explicit .txt override -------------------------------------------
    playlist_path = Path(__file__).parent / "playlists" / f"{playlist_name}.txt"
    if playlist_path.exists():
        urls: list[str] = []
        with open(playlist_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)
        if urls:
            print(f"[playlist] Loaded {len(urls)} tracks from {playlist_name}.txt")
            return urls

    # --- 2. Local audio folder -----------------------------------------------
    audio_dir = _REPO_ROOT / "voice-service" / "audio" / playlist_name
    if audio_dir.exists():
        local_files = sorted(audio_dir.glob("*.mp3")) + sorted(audio_dir.glob("*.ogg"))
        if local_files:
            tracks = [f"localfile:{p.as_posix()}" for p in local_files]
            print(f"[playlist] Loaded {len(tracks)} local files for '{playlist_name}'")
            return tracks

    # --- 3. Freesound fallback -----------------------------------------------
    freesound_key = os.getenv("FREESOUND_API_KEY", "")
    if freesound_key:
        try:
            from freesound_client import get_mood_tracks
            tracks = await get_mood_tracks(playlist_name, api_key=freesound_key)
            if tracks:
                print(f"[playlist] Loaded {len(tracks)} Freesound tracks for '{playlist_name}'")
                return tracks
        except Exception as e:
            print(f"[playlist] Freesound fallback failed: {e}")

    print(f"[playlist] No tracks found for '{playlist_name}'")
    return []


# --- Public playback API (sidecar-backed) --------------------------------------

async def play_music_in_channel(
    voice_channel: discord.VoiceChannel,
    playlist_name: str = "cc_menu",
    *,
    bot: Optional[discord.Client] = None,
    volume: int = 50,
) -> bool:
    """Play a looping playlist in ``voice_channel`` via the voice sidecar.

    Returns True when the sidecar reports playback started.
    """
    if not await ensure_voice_service_ready(bot):
        print("[music] Voice service not ready; cannot start playback")
        return False

    tracks = await load_playlist(playlist_name)
    if not tracks:
        print(f"[music] No tracks in playlist {playlist_name}")
        return False

    data = await _post("/play", {
        "guildId": str(voice_channel.guild.id),
        "channelId": str(voice_channel.id),
        "tracks": tracks,
        "loop": True,
        "volume": max(0, min(100, volume)),
    })
    if data and data.get("ok") and data.get("playing"):
        active_players[voice_channel.id] = {
            "playlist": playlist_name, "guild_id": voice_channel.guild.id}
        current_playlists[voice_channel.id] = playlist_name
        _channel_guild[voice_channel.id] = voice_channel.guild.id
        print(f"[music] Playing '{playlist_name}' in {voice_channel.name}")
        return True

    print(f"[music] Sidecar did not start playback for '{playlist_name}'")
    return False


async def play_query_in_channel(
    voice_channel: discord.VoiceChannel,
    query: str,
    *,
    volume: int = 30,
    bot: Optional[discord.Client] = None,
) -> bool:
    """Play a single searched track (looped) for an AI-recommended scene."""
    if not await ensure_voice_service_ready(bot):
        print("[music] Voice service not ready; cannot start scene music")
        return False

    q = (query or "").strip()
    if not q:
        print("[music] Empty scene query; skipping")
        return False

    # Prefer Freesound: direct MP3 URLs stream through ffmpeg with no yt-dlp.
    # Fall back to the raw query (sidecar resolves it as a YouTube search).
    tracks: list[str] = [q]
    freesound_key = os.getenv("FREESOUND_API_KEY", "")
    if freesound_key:
        try:
            from freesound_client import search_tracks
            found = await search_tracks(q, api_key=freesound_key)
            if found:
                tracks = found
        except Exception as e:
            print(f"[music] Freesound scene search failed ({e}); using yt-dlp fallback")

    data = await _post("/play", {
        "guildId": str(voice_channel.guild.id),
        "channelId": str(voice_channel.id),
        "tracks": tracks,
        "loop": True,
        "volume": max(0, min(100, volume)),
    })
    if data and data.get("ok") and data.get("playing"):
        active_players[voice_channel.id] = {
            "playlist": f"scene:{q}", "guild_id": voice_channel.guild.id}
        current_playlists[voice_channel.id] = f"scene:{q}"
        _channel_guild[voice_channel.id] = voice_channel.guild.id
        print(f"[music] Scene music -> {q} in {voice_channel.name}")
        return True

    print(f"[music] Sidecar did not start scene music for '{q}'")
    return False


async def stop_music_in_channel(voice_channel_id: int) -> None:
    """Stop music and disconnect the sidecar from the channel's guild."""
    guild_id = _channel_guild.get(voice_channel_id)
    payload: dict = {"channelId": str(voice_channel_id)}
    if guild_id is not None:
        payload["guildId"] = str(guild_id)
    await _post("/stop", payload)
    active_players.pop(voice_channel_id, None)
    current_playlists.pop(voice_channel_id, None)
    _channel_guild.pop(voice_channel_id, None)
    print(f"[music] Stopped music in channel {voice_channel_id}")


async def set_volume_in_channel(voice_channel_id: int, volume: int) -> bool:
    """Adjust playback volume (0-100) for an active channel."""
    guild_id = _channel_guild.get(voice_channel_id)
    if guild_id is None:
        return False
    data = await _post("/volume", {
        "guildId": str(guild_id), "volume": max(0, min(100, volume))})
    return bool(data and data.get("ok"))


def get_active_player(voice_channel_id: int) -> Optional[dict]:
    """Return the local state entry for a channel, if any."""
    return active_players.get(voice_channel_id)
