"""
Music player module using Lavalink/wavelink for Discord voice channels.
"""
import asyncio
import os
import subprocess
import atexit
from pathlib import Path
from typing import Dict, Optional

import discord
import wavelink


# Track active music players: voice_channel_id -> wavelink.Player
active_players: Dict[int, wavelink.Player] = {}

# Track current playlist for each channel: voice_channel_id -> playlist_name
current_playlists: Dict[int, str] = {}

# Global Lavalink process
lavalink_process: Optional[subprocess.Popen] = None


def start_lavalink_server() -> bool:
    """Start Lavalink server as a subprocess."""
    global lavalink_process
    
    lavalink_jar = os.path.join(os.path.dirname(__file__), "Lavalink.jar")
    
    if not os.path.exists(lavalink_jar):
        print("[Lavalink] WARNING: Lavalink.jar not found!")
        print(f"[Lavalink] Expected location: {lavalink_jar}")
        print("[Lavalink] Download from: https://github.com/lavalink-devs/Lavalink/releases")
        return False
    
    # Check if application.yml exists
    app_yml = os.path.join(os.path.dirname(__file__), "application.yml")
    if not os.path.exists(app_yml):
        print(f"[Lavalink] WARNING: application.yml not found at {app_yml}")
        print("[Lavalink] Lavalink requires application.yml configuration file")
        return False
    
    try:
        print("[Lavalink] Starting Lavalink server...")
        print(f"[Lavalink] Working directory: {os.path.dirname(__file__)}")
        print(f"[Lavalink] JAR location: {lavalink_jar}")
        
        # Start Lavalink with output redirected to files for debugging
        log_file = open(os.path.join(os.path.dirname(__file__), "lavalink.log"), "w")
        error_file = open(os.path.join(os.path.dirname(__file__), "lavalink-error.log"), "w")
        
        lavalink_process = subprocess.Popen(
            ["java", "-jar", lavalink_jar],
            stdout=log_file,
            stderr=error_file,
            cwd=os.path.dirname(__file__)
        )
        print(f"[Lavalink] Server started with PID {lavalink_process.pid}")
        print("[Lavalink] Logs: lavalink.log and lavalink-error.log")
        print("[Lavalink] Waiting 10 seconds for server to initialize...")
        return True
    except FileNotFoundError:
        print("[Lavalink] ERROR: Java not found! Please install Java 17 or higher.")
        return False
    except Exception as e:
        print(f"[Lavalink] ERROR starting server: {e}")
        import traceback
        traceback.print_exc()
        return False


def stop_lavalink_server():
    """Stop the Lavalink server subprocess."""
    global lavalink_process
    
    if lavalink_process:
        print("[Lavalink] Stopping server...")
        try:
            lavalink_process.terminate()
            lavalink_process.wait(timeout=5)
            print("[Lavalink] Server stopped gracefully")
        except subprocess.TimeoutExpired:
            print("[Lavalink] Server didn't stop gracefully, forcing...")
            lavalink_process.kill()
            lavalink_process.wait()
            print("[Lavalink] Server force-stopped")
        except Exception as e:
            print(f"[Lavalink] Error stopping server: {e}")
        finally:
            lavalink_process = None


# Register cleanup handler
atexit.register(stop_lavalink_server)


async def setup_lavalink(bot: discord.Client) -> bool:
    """
    Connect to Lavalink server with retry logic.
    Returns True if successful, False otherwise.
    """
    import asyncio
    
    max_retries = 10
    retry_delay = 3  # seconds
    
    for attempt in range(1, max_retries + 1):
        try:
            node = wavelink.Node(
                uri="http://127.0.0.1:2333",
                password="youshallnotpass"
            )
            await wavelink.Pool.connect(client=bot, nodes=[node])
            print(f"[Lavalink] Connected successfully on attempt {attempt}")
            return True
        except Exception as e:
            if attempt < max_retries:
                print(f"[Lavalink] Connection attempt {attempt}/{max_retries} failed: {e}")
                print(f"[Lavalink] Retrying in {retry_delay} seconds...")
                await asyncio.sleep(retry_delay)
            else:
                print(f"[Lavalink] All {max_retries} connection attempts failed")
                print("[Lavalink] Make sure Lavalink server is running on port 2333")
                return False


async def ensure_lavalink_ready(bot: Optional[discord.Client]) -> bool:
    """Best-effort check that at least one Lavalink node is ready.

    If no nodes are currently connected and a bot client is available, attempt a
    reconnect via ``setup_lavalink`` so first-play in a fresh/test session still
    works even after transient WS disconnects.
    """
    try:
        nodes = getattr(wavelink.Pool, "nodes", None)
        if nodes:
            return True
    except Exception:
        pass

    if bot is None:
        return False

    print("[Lavalink] No active node detected; attempting reconnect...")
    try:
        return await setup_lavalink(bot)
    except Exception as e:
        print(f"[Lavalink] Reconnect failed: {e}")
        return False


async def load_playlist(playlist_name: str) -> list[str]:
    """Load a playlist from the playlists directory."""
    playlist_path = Path(__file__).parent / "playlists" / f"{playlist_name}.txt"
    if not playlist_path.exists():
        print(f"[playlist] Playlist file not found: {playlist_path}")
        return []
    
    urls = []
    with open(playlist_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                urls.append(line)
    
    print(f"[playlist] Loaded {len(urls)} tracks from {playlist_name}")
    return urls


def _normalize_search_identifier(value: str) -> str:
    """Normalize a user/playlist music query for Lavalink search.

    Wavelink + Lavalink v4 can produce odd results when fed pre-prefixed values
    like ``ytsearch:foo`` (it can become ``ytmsearch:ytsearch:foo``). To keep
    behavior stable, collapse any ytsearch/ytmsearch prefix to a clean search
    term and emit exactly one ``ytmsearch:`` prefix.
    """
    s = (value or "").strip()
    if not s:
        return s
    # Direct URLs (YouTube/http/etc.) should pass through untouched.
    if "://" in s:
        return s

    low = s.lower()
    for prefix in ("ytsearch:", "ytmsearch:"):
        if low.startswith(prefix):
            s = s[len(prefix):].strip()
            break

    return f"ytmsearch:{s}" if s else s


async def play_music_in_channel(
    voice_channel: discord.VoiceChannel,
    playlist_name: str = "cc_menu",
    *,
    bot: Optional[discord.Client] = None,
    volume: int = 50,
) -> bool:
    """Connect to voice channel and play music from playlist.

    Returns True when playback starts (or is already active in the channel),
    otherwise False.
    """
    try:
        if not await ensure_lavalink_ready(bot):
            print("[music] Lavalink is not ready; cannot start playback")
            return False

        # Check if already connected
        if voice_channel.id in active_players:
            print(f"[music] Already playing in {voice_channel.name}")
            return True

        # Load playlist
        urls = await load_playlist(playlist_name)
        if not urls:
            print(f"[music] No tracks in playlist {playlist_name}")
            return False

        # Clean up any lingering voice connection in this guild first. Discord
        # needs a moment to tear down the old voice session before we can open a
        # new one; skipping this causes "Unable to connect" on the next connect.
        existing = voice_channel.guild.voice_client
        if existing is not None:
            try:
                await existing.disconnect(force=True)
            except Exception as e:
                print(f"[music] Could not clean up old voice client: {e}")
            await asyncio.sleep(1.0)

        # Connect to voice channel, retrying to survive voice-session races.
        player: Optional[wavelink.Player] = None
        last_err: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                player = await voice_channel.connect(cls=wavelink.Player, timeout=20.0)
                break
            except Exception as e:
                last_err = e
                print(f"[music] Connect attempt {attempt}/3 failed: {e}")
                await asyncio.sleep(1.5)
        if player is None:
            raise last_err or RuntimeError("Unable to connect to voice channel")

        active_players[voice_channel.id] = player
        current_playlists[voice_channel.id] = playlist_name
        await player.set_volume(max(0, min(100, volume)))

        print(f"[music] Connected to {voice_channel.name}, loading tracks from '{playlist_name}'...")

        # Load and play tracks
        for url in urls:
            try:
                ident = _normalize_search_identifier(url)
                if not ident:
                    continue
                tracks = await wavelink.Playable.search(ident)
                if tracks:
                    await player.queue.put_wait(tracks[0] if isinstance(tracks, list) else tracks)
                    print(f"[music] Queued: {tracks[0].title if isinstance(tracks, list) else tracks.title}")
            except Exception as e:
                print(f"[music] Error loading track {url}: {e}")

        # Start playback if not already playing
        if not player.playing and not player.queue.is_empty:
            await player.play(player.queue.get())
            print(f"[music] Started playback in {voice_channel.name}")

        return bool(player.playing or not player.queue.is_empty)

    except Exception as e:
        print(f"[music] Error in play_music_in_channel: {e}")
        # Roll back any partial state so a retry can reconnect cleanly.
        active_players.pop(voice_channel.id, None)
        current_playlists.pop(voice_channel.id, None)
        stale = voice_channel.guild.voice_client
        if stale is not None:
            try:
                await stale.disconnect(force=True)
            except Exception:
                pass
        return False


async def play_query_in_channel(
    voice_channel: discord.VoiceChannel,
    query: str,
    *,
    volume: int = 30,
    bot: Optional[discord.Client] = None,
) -> bool:
    """Play a single searched track (looped) for an AI-recommended scene.

    Unlike ``play_music_in_channel`` (which loads a fixed playlist file), this
    plays one track resolved from a search query and loops it until the scene
    changes. If already connected to this channel, it swaps to the new track
    without disconnecting; otherwise it connects first.
    """
    try:
        if not await ensure_lavalink_ready(bot):
            print("[music] Lavalink is not ready; cannot start scene music")
            return False

        search = _normalize_search_identifier(query)
        if not search:
            print("[music] Empty scene query; skipping")
            return False

        # Ensure we have a live player in this channel.
        player = active_players.get(voice_channel.id)
        if player is None or not player.connected:
            existing = voice_channel.guild.voice_client
            if existing is not None:
                try:
                    await existing.disconnect(force=True)
                except Exception as e:
                    print(f"[music] Could not clean up old voice client: {e}")
                await asyncio.sleep(1.0)

            player = None
            last_err: Optional[Exception] = None
            for attempt in range(1, 4):
                try:
                    player = await voice_channel.connect(cls=wavelink.Player, timeout=20.0)
                    break
                except Exception as e:
                    last_err = e
                    print(f"[music] Connect attempt {attempt}/3 failed: {e}")
                    await asyncio.sleep(1.5)
            if player is None:
                raise last_err or RuntimeError("Unable to connect to voice channel")

            active_players[voice_channel.id] = player
            await player.set_volume(volume)

        # Resolve the query into a playable track.
        tracks = await wavelink.Playable.search(search)
        if not tracks:
            print(f"[music] No results for scene query '{search}'")
            return False
        track = tracks[0] if isinstance(tracks, list) else tracks

        # Swap to the new scene track: clear the queue and replace playback.
        current_playlists[voice_channel.id] = f"scene:{query.strip()}"
        try:
            player.queue.clear()
        except Exception:
            pass
        await player.play(track)
        print(f"[music] Scene music -> {track.title}  (query: {query.strip()})")
        return True

    except Exception as e:
        print(f"[music] Error in play_query_in_channel: {e}")
        return False


async def stop_music_in_channel(voice_channel_id: int):
    """Stop music and disconnect from voice channel."""
    if voice_channel_id in active_players:
        try:
            player = active_players[voice_channel_id]
            await player.disconnect(force=True)
            del active_players[voice_channel_id]
            if voice_channel_id in current_playlists:
                del current_playlists[voice_channel_id]
            print(f"[music] Disconnected from voice channel {voice_channel_id}")
            # Give Discord time to fully close the voice session so a later
            # reconnect (e.g. toggling music back on) doesn't race.
            await asyncio.sleep(1.0)
        except Exception as e:
            print(f"[music] Error stopping music: {e}")
            active_players.pop(voice_channel_id, None)
            current_playlists.pop(voice_channel_id, None)


def get_active_player(voice_channel_id: int) -> Optional[wavelink.Player]:
    """Get the active player for a voice channel, if any."""
    return active_players.get(voice_channel_id)


async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload):
    """Called when Lavalink node is ready."""
    print(f"[Lavalink] Node {payload.node.identifier} is ready!")


async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload):
    """Called when a track starts playing."""
    player = payload.player
    track = payload.track
    print(f"[music] Now playing: {track.title} in channel {player.channel.id if player.channel else 'unknown'}")


async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    """Called when a track ends - loop the playlist in place (no reconnect)."""
    player = payload.player
    if player is None or player.channel is None:
        return

    voice_channel_id = player.channel.id
    # If we intentionally stopped/disconnected this channel, do nothing.
    if voice_channel_id not in active_players:
        return

    # Only loop when a track ended naturally. Ignore "replaced" (scene swap),
    # "stopped", and failure reasons so scene changes don't re-queue stale tracks.
    reason = getattr(payload, "reason", "finished")
    if str(reason).lower() != "finished":
        return

    # Re-queue the finished track at the end so the current track/playlist loops forever
    # without ever disconnecting from voice (which caused reconnect races).
    if payload.track is not None:
        try:
            await player.queue.put_wait(payload.track)
        except Exception as e:
            print(f"[music] Error re-queuing track: {e}")

    # Advance playback (manual control keeps looping deterministic and never
    # disconnects, so the voice session stays alive between tracks).
    if not player.playing and not player.queue.is_empty:
        try:
            await player.play(player.queue.get())
            print(f"[music] Looping playlist in channel {voice_channel_id}")
        except Exception as e:
            print(f"[music] Error continuing playback: {e}")
