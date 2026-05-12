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


async def play_music_in_channel(voice_channel: discord.VoiceChannel, playlist_name: str = "cc_menu"):
    """Connect to voice channel and play music from playlist."""
    try:
        # Check if already connected
        if voice_channel.id in active_players:
            print(f"[music] Already playing in {voice_channel.name}")
            return
        
        # Load playlist
        urls = await load_playlist(playlist_name)
        if not urls:
            print(f"[music] No tracks in playlist {playlist_name}")
            return
        
        # Connect to voice channel
        player: wavelink.Player = await voice_channel.connect(cls=wavelink.Player)
        active_players[voice_channel.id] = player
        current_playlists[voice_channel.id] = playlist_name
        
        print(f"[music] Connected to {voice_channel.name}, loading tracks from '{playlist_name}'...")
        
        # Load and play tracks
        for url in urls:
            try:
                tracks = await wavelink.Playable.search(url)
                if tracks:
                    await player.queue.put_wait(tracks[0] if isinstance(tracks, list) else tracks)
                    print(f"[music] Queued: {tracks[0].title if isinstance(tracks, list) else tracks.title}")
            except Exception as e:
                print(f"[music] Error loading track {url}: {e}")
        
        # Start playback if not already playing
        if not player.playing:
            await player.play(player.queue.get())
            print(f"[music] Started playback in {voice_channel.name}")
        
        # Set volume to 30%
        await player.set_volume(30)
        
    except Exception as e:
        print(f"[music] Error in play_music_in_channel: {e}")
        if voice_channel.id in active_players:
            del active_players[voice_channel.id]


async def stop_music_in_channel(voice_channel_id: int):
    """Stop music and disconnect from voice channel."""
    if voice_channel_id in active_players:
        try:
            player = active_players[voice_channel_id]
            await player.disconnect()
            del active_players[voice_channel_id]
            if voice_channel_id in current_playlists:
                del current_playlists[voice_channel_id]
            print(f"[music] Disconnected from voice channel {voice_channel_id}")
        except Exception as e:
            print(f"[music] Error stopping music: {e}")


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
    """Called when a track ends - play next track in queue."""
    player = payload.player
    
    # If there are more tracks in queue, play next
    if not player.queue.is_empty:
        next_track = player.queue.get()
        await player.play(next_track)
        print(f"[music] Playing next track: {next_track.title}")
    else:
        # Queue is empty, loop the playlist
        if player.channel and player.channel.id in active_players:
            voice_channel = player.channel
            playlist_name = current_playlists.get(voice_channel.id, "cc_menu")
            print(f"[music] Playlist '{playlist_name}' ended, restarting loop...")
            await player.disconnect()
            del active_players[voice_channel.id]
            if voice_channel.id in current_playlists:
                del current_playlists[voice_channel.id]
            # Restart the same playlist
            await play_music_in_channel(voice_channel, playlist_name)
