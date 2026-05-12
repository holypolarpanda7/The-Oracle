# Music System Documentation

## Overview
The Oracle bot now includes an integrated music system using Lavalink that automatically plays contextual background music during character creation and gameplay sessions.

## Features

### 1. **Automatic Music Playback**
- Music starts automatically when a player joins their character creation voice channel
- Default: `cc_menu` playlist plays during character creation
- Music transitions to `character_complete` playlist when character is successfully registered

### 2. **Player Controls**
Players can toggle music on/off using reactions in the welcome message:
- 🔇 - Turn music OFF
- 🔊 - Turn music ON

### 3. **Multiple Playlists**
The system supports different playlists for various contexts:

| Playlist | Context | Description |
|----------|---------|-------------|
| `cc_menu` | Character Creation | Background music for character creation menu |
| `character_complete` | Character Registered | Celebration music when character is approved |
| `town` | Town/City | Peaceful ambient music for settlements |
| `tavern` | Tavern/Inn | Warm, social music for gathering places |
| `desert` | Desert Exploration | Atmospheric music for hot climates |
| `dungeon` | Underground | Dark, mysterious music for dungeons |
| `combat` | Battle | Intense music for combat encounters |

### 4. **Playlist Management**
All playlists are stored in `playlists/*.txt` files with one URL per line:

```
# playlists/town.txt
# Town/City ambient music
https://www.youtube.com/watch?v=example1
https://www.youtube.com/watch?v=example2
# Lines starting with # are comments
```

Supported sources: YouTube, SoundCloud, Bandcamp, Twitch, HTTP/HTTPS streams

## Implementation Details

### State Tracking
```python
# Tracks music preferences per voice channel
music_preferences: Dict[int, Dict] = {
    voice_channel_id: {
        "enabled": bool,          # Whether music is on/off
        "current_playlist": str   # Currently playing playlist
    }
}
```

### Key Functions

#### `switch_music(voice_channel, new_playlist)`
Switches to a different playlist in the given voice channel.

```python
# Example: Switch to town music
await switch_music(voice_channel, "town")
```

#### `toggle_music(voice_channel_id, enabled)`
Turns music on/off for a voice channel.

```python
# Turn music off
await toggle_music(voice_channel_id, False)

# Turn music back on
await toggle_music(voice_channel_id, True)
```

## Automatic Music Switching

### Character Creation Flow
1. Player uses `/enterworld` → Voice channel created
2. Player joins voice channel → `cc_menu` music starts automatically
3. Player completes character creation → Music switches to `character_complete`
4. Player leaves/session ends → Music stops, channel deleted

### Future: Context-Based Switching
The system is designed to support automatic music switching based on:
- Player location (town, wilderness, dungeon)
- Game state (exploration, combat, social)
- DM commands to set atmosphere

## DM Controls (Future Enhancement)

You can add slash commands for DMs to manually control music:

```python
@bot.tree.command(name="music")
async def music_control(
    interaction: discord.Interaction,
    action: Literal["play", "stop", "switch"],
    playlist: str = None
):
    """Control background music in voice channels."""
    # Implementation for manual DM control
```

## Configuration

### Volume
Default volume is set to 30%. To change:

Edit `music_player.py`:
```python
await player.set_volume(30)  # Change to desired volume (0-100)
```

### Playlist Loop
Playlists automatically restart when all tracks finish. This behavior is in `music_player.py`:

```python
async def on_wavelink_track_end(payload):
    # ... when queue is empty, restart playlist
    await play_music_in_channel(voice_channel, "cc_menu")
```

## Adding New Playlists

1. Create a new `.txt` file in `playlists/` directory:
   ```bash
   touch playlists/forest.txt
   ```

2. Add URLs (one per line):
   ```
   # Forest exploration music
   https://www.youtube.com/watch?v=example1
   https://www.youtube.com/watch?v=example2
   ```

3. Use the playlist in code:
   ```python
   await music_player.play_music_in_channel(voice_channel, "forest")
   ```

## Troubleshooting

### Music not playing?
- Check Lavalink server is running: `java -jar Lavalink.jar`
- Verify bot has permission to connect to voice channels
- Check playlist file exists and has valid URLs

### Music won't stop?
- Check `music_preferences` state: `music_preferences[voice_channel_id]`
- Verify `toggle_music()` is being called correctly
- Check Lavalink server logs for errors

### Playlists not loading?
- Verify file path: `playlists/<name>.txt`
- Check file format (UTF-8, one URL per line)
- Test URLs manually in browser

## Architecture

```
oracle-dm-discord-bot.py
├── music_preferences{}          # Track enabled/disabled state
├── switch_music()               # Switch playlists
├── toggle_music()               # Enable/disable music
└── on_reaction_add()            # Handle player toggle requests

music_player.py
├── active_players{}             # Track active Lavalink players
├── setup_lavalink()             # Connect to Lavalink server
├── load_playlist()              # Load URLs from .txt files
├── play_music_in_channel()      # Start playback
├── stop_music_in_channel()      # Stop and disconnect
└── on_wavelink_track_end()      # Auto-loop playlists

playlists/
├── cc_menu.txt                  # Character creation
├── character_complete.txt       # Character registered
├── town.txt                     # Town ambient
├── tavern.txt                   # Tavern social
├── desert.txt                   # Desert exploration
├── dungeon.txt                  # Dungeon crawling
└── combat.txt                   # Battle music
```
