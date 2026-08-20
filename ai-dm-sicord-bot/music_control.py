"""
Music Control Module - Handles music preferences and playlist switching.
Coordinates with music_player.py for playback control.
"""
import re
from typing import Dict, Optional
import discord
import music_player


# Track music preferences per voice channel: voice_channel_id -> {enabled: bool, current_playlist: str}
music_preferences: Dict[int, Dict] = {}


# The DM emits free-text music cues ([[MUSIC: keywords]]); local audio only
# exists for these named moods, so we snap each cue to the nearest mood by
# keyword hits. Order matters only for readability — scoring picks the best.
_MOOD_KEYWORDS: Dict[str, tuple] = {
    "combat": ("combat", "battle", "fight", "clash", "war", "danger", "ambush",
               "chase", "boss", "duel", "skirmish", "onslaught"),
    "dungeon": ("dungeon", "cave", "crypt", "tomb", "catacomb", "underground",
                "dark", "eerie", "haunt", "ruin", "sewer", "creepy", "dread", "gloom"),
    "tavern": ("tavern", "inn", "pub", "bar", "drink", "feast", "cheer", "bard",
               "song", "celebrat", "merry", "jovial", "festive", "hearth"),
    "town": ("town", "city", "market", "village", "street", "square", "shop",
             "crowd", "settlement", "bustle", "road", "travel"),
    "desert": ("desert", "sand", "dune", "arid", "wasteland", "oasis", "barren", "scorch"),
}
DEFAULT_MOOD = "town"

# The playlist a fresh table opens on. A fresh table is a TITLE SCREEN and a
# character-creation wizard, not a scene — so it gets menu music, not a room.
MENU_PLAYLIST = "cc_menu"
# …and where it lands once play begins with no scene cue to follow.
WORLD_DEFAULT_PLAYLIST = "tavern"


async def leave_menu_music(voice_channel: "discord.VoiceChannel") -> Optional[str]:
    """Move a table off the menu bed once play actually starts.

    Nothing but the DM's own scene cue ever moved a table's playlist, so a
    session the DM never scored would sit on the character-creation music all
    night. No-op unless the table is still on the menu list — anything else
    means a cue already won, and this must not talk over it.
    """
    if voice_channel is None:
        return None
    prefs = music_preferences.get(voice_channel.id)
    if not prefs or not prefs.get("enabled", True):
        return None
    if prefs.get("current_playlist") != MENU_PLAYLIST:
        return None
    await switch_music(voice_channel, WORLD_DEFAULT_PLAYLIST)
    return WORLD_DEFAULT_PLAYLIST


#: How a keyword is allowed to match. A plain substring test scores "a WARm
#: tavern" as combat (war) and "a BARe arena" as tavern (bar) — both real cues
#: this has been handed. So a keyword must start a WORD, and a short one must
#: also END one: the long entries here are deliberate stems ("celebrat",
#: "settlement", "bustle") and want to catch their own endings, while the short
#: ones are whole words that have no business living inside other words.
_STEM_MIN = 5


def _kw_pattern(kw: str) -> "re.Pattern":
    tail = r"[a-z]*" if len(kw) >= _STEM_MIN else r"\b"
    return re.compile(r"\b" + re.escape(kw) + tail)


_MOOD_PATTERNS: Dict[str, tuple] = {
    mood: tuple(_kw_pattern(k) for k in kws)
    for mood, kws in _MOOD_KEYWORDS.items()
}


def mood_for_query(query: str) -> str:
    """Map a DM music cue (free text) to the nearest local mood playlist."""
    q = (query or "").lower()
    best, best_hits = None, 0
    for mood, pats in _MOOD_PATTERNS.items():
        hits = sum(1 for p in pats if p.search(q))
        if hits > best_hits:
            best, best_hits = mood, hits
    return best or DEFAULT_MOOD


async def apply_music_cue(voice_channel: "discord.VoiceChannel", query: str):
    """Switch a table's playlist to match the DM's scene cue.

    Returns the mood applied, or None when it's disabled, unknown, or already
    playing that mood (so we never restart the track for the same mood)."""
    if voice_channel is None or not query:
        return None
    prefs = music_preferences.get(voice_channel.id)
    if not prefs or not prefs.get("enabled", True):
        return None
    mood = mood_for_query(query)
    if prefs.get("current_playlist") == mood:
        return None
    await switch_music(voice_channel, mood)
    return mood


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
            await music_player.play_music_in_channel(voice_channel, playlist, bot=bot)
            print(f"[music] Enabled music in channel {voice_channel_id}")
    else:
        # Turn music off
        await music_player.stop_music_in_channel(voice_channel_id)
        print(f"[music] Disabled music in channel {voice_channel_id}")
    
    return True
