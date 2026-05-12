"""
Music Control Module - Handles music preferences and playlist switching.
Coordinates with music_player.py for playback control.
"""
from typing import Dict
import discord
import music_player


# Track music preferences per voice channel: voice_channel_id -> {enabled: bool, current_playlist: str}
music_preferences: Dict[int, Dict] = {}


async def switch_music(voice_channel: discord.VoiceChannel, new_playlist: str):
    """Switch to a different playlist in the given voice channel."""
    if voice_channel.id not in music_preferences:
        print(f"[music] No music preferences found for channel {voice_channel.id}")
        return
    
    # Check if music is enabled
    if not music_preferences[voice_channel.id]["enabled"]:
        print(f"[music] Music is disabled for channel {voice_channel.id}")
        return
    
    # Stop current music
    await music_player.stop_music_in_channel(voice_channel.id)
    
    # Update preference
    music_preferences[voice_channel.id]["current_playlist"] = new_playlist
    
    # Start new playlist
    await music_player.play_music_in_channel(voice_channel, new_playlist)
    print(f"[music] Switched to playlist '{new_playlist}' in {voice_channel.name}")


async def toggle_music(voice_channel_id: int, bot) -> bool:
    """Toggle music on/off for a voice channel. Returns True if state changed."""
    if voice_channel_id not in music_preferences:
        print(f"[music] No music preferences found for channel {voice_channel_id}")
        return False
    
    enabled = music_preferences[voice_channel_id]["enabled"]
    new_state = not enabled
    
    if enabled == new_state:
        return False  # No change
    
    music_preferences[voice_channel_id]["enabled"] = new_state
    
    if new_state:
        # Turn music back on with current playlist
        voice_channel = bot.get_channel(voice_channel_id)
        if voice_channel:
            playlist = music_preferences[voice_channel_id]["current_playlist"]
            await music_player.play_music_in_channel(voice_channel, playlist)
            print(f"[music] Enabled music in channel {voice_channel_id}")
    else:
        # Turn music off
        await music_player.stop_music_in_channel(voice_channel_id)
        print(f"[music] Disabled music in channel {voice_channel_id}")
    
    return True
