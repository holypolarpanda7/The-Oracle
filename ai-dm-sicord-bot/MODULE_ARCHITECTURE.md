# Oracle DM Bot - Modular Architecture

## 📁 Module Structure

The bot has been refactored into logical modules for better maintainability and separation of concerns:

```
ai-dm-sicord-bot/
├── oracle-dm-discord-bot-refactored.py  # Main entry point (NEW)
├── oracle-dm-discord-bot.py             # Legacy monolithic file (OLD)
├── character_creation.py                # Character creation logic ⭐ NEW
├── music_control.py                     # Music preferences & switching ⭐ NEW
├── music_player.py                      # Lavalink/wavelink integration
├── backend_integration.py               # HTTP calls to FastAPI backend ⭐ NEW
├── dm_commands.py                       # Command handlers ⭐ NEW
├── event_handlers.py                    # Discord event handlers ⭐ NEW
├── playlists/                           # Music playlist files
└── cred.env                             # Environment variables
```

---

## 🎯 Module Responsibilities

### `oracle-dm-discord-bot-refactored.py` - Main Entry Point (167 lines)
**Purpose:** Bot initialization, configuration, and command/event registration

**Contents:**
- Bot setup with intents
- Environment variable loading
- Command decorators (`@bot.command`)
- Event decorators (`@bot.event`)
- Lavalink lifecycle management
- DM-enabled channels tracking

**Delegates to:** All other modules for actual logic

---

### `character_creation.py` - Character Creation (411 lines)
**Purpose:** Complete character creation workflow management

**Key Functions:**
- `create_character_creation_session()` - Create ephemeral voice/text channels
- `extract_character_from_avrae_embed()` - Parse Avrae imports
- `validate_and_register_character()` - Validate & register to backend
- `start_guided_character_creation()` - AI-guided creation flow
- `process_guided_cc_input()` - Handle player inputs during guided creation
- `finalize_guided_character()` - Complete guided creation
- `cleanup_ephemeral_channel()` - Delete channels and cleanup
- `schedule_cleanup_task()` - 1-hour inactivity timer

**State:**
- `ephemeral_cc_channels` - Active creation sessions
- `cleanup_tasks` - Scheduled cleanup coroutines
- `guided_cc_state` - AI-guided creation progress

**Dependencies:** `music_control`, `backend_integration`, `music_player`

---

### `music_control.py` - Music Control (60 lines)
**Purpose:** Music preference management and playlist switching

**Key Functions:**
- `switch_music()` - Change to different playlist
- `toggle_music()` - Turn music on/off

**State:**
- `music_preferences` - Per-channel music settings (`{enabled, current_playlist}`)

**Dependencies:** `music_player`

---

### `music_player.py` - Lavalink Integration (213 lines)
**Purpose:** Low-level music playback and Lavalink server management

**Key Functions:**
- `start_lavalink_server()` - Launch Lavalink subprocess
- `stop_lavalink_server()` - Gracefully stop Lavalink
- `setup_lavalink()` - Connect wavelink to Lavalink
- `load_playlist()` - Read URLs from playlist files
- `play_music_in_channel()` - Start playback
- `stop_music_in_channel()` - Stop and disconnect
- `on_wavelink_*` - Event handlers for track management

**State:**
- `active_players` - Active wavelink players by channel
- `current_playlists` - Currently playing playlist names
- `lavalink_process` - Subprocess handle

**Dependencies:** None (fully self-contained)

---

### `backend_integration.py` - HTTP Backend (95 lines)
**Purpose:** All communication with FastAPI backend

**Key Functions:**
- `check_character_in_db()` - Check if user has character
- `register_character_backend()` - POST character data
- `call_backend()` - Send message to DM AI
- `reset_backend_session()` - Clear conversation history
- `enter_world_backend()` - Initialize game session

**Dependencies:** None (pure HTTP client)

---

### `dm_commands.py` - Command Handlers (70 lines)
**Purpose:** Command logic for DM mode and world entry

**Key Functions:**
- `is_admin()` - Check admin permissions
- `start_dm_command()` - Enable DM mode
- `stop_dm_command()` - Disable DM mode
- `reset_dm_command()` - Reset conversation
- `enter_world_command()` - Enter game world

**Dependencies:** `backend_integration`, `character_creation`

---

### `event_handlers.py` - Discord Events (254 lines)
**Purpose:** All Discord event handling logic

**Key Functions:**
- `on_ready_handler()` - Bot startup
- `on_reaction_add_handler()` - Handle emoji reactions (CC path selection, music toggle)
- `on_voice_state_update_handler()` - Player joins CC voice channel
- `on_message_handler()` - Message routing (CC, DM mode, Avrae detection)
- `on_wavelink_*_handler()` - Music event forwarding

**Dependencies:** All modules (orchestrates interactions)

---

## 🔄 Module Interaction Flow

### Character Creation Flow
```
User: /enterworld
    ↓
dm_commands.enter_world_command()
    ↓
backend_integration.check_character_in_db()
    ↓ (no character)
character_creation.create_character_creation_session()
    ↓ (creates voice + text channels)
music_control.music_preferences initialized
    ↓
User joins voice channel
    ↓
event_handlers.on_voice_state_update_handler()
    ↓
music_player.play_music_in_channel()
    ↓
User reacts to ✅ or ❌
    ↓
event_handlers.on_reaction_add_handler()
    ↓
character_creation.start_guided_character_creation() or Avrae import
    ↓
character_creation.validate_and_register_character()
    ↓
backend_integration.register_character_backend()
    ↓
music_control.switch_music("character_complete")
    ↓
character_creation.cleanup_ephemeral_channel()
```

### DM Conversation Flow
```
Admin: !startdm
    ↓
dm_commands.start_dm_command()
    ↓ (adds channel to active_dm_channels)
User: "I explore the forest"
    ↓
event_handlers.on_message_handler()
    ↓
backend_integration.call_backend()
    ↓ (HTTP POST to FastAPI)
Backend: Returns DM response
    ↓
Bot: Sends reply to channel
```

### Music Toggle Flow
```
User: Reacts with 🔇
    ↓
event_handlers.on_reaction_add_handler()
    ↓
music_control.toggle_music(voice_channel_id, False)
    ↓
music_player.stop_music_in_channel()
    ↓
Music stops
```

---

## 🚀 Migration Guide

### To use the new modular structure:

**Option 1: Switch main file (Recommended)**
```bash
# Rename old file as backup
mv oracle-dm-discord-bot.py oracle-dm-discord-bot-old.py

# Rename new file to main
mv oracle-dm-discord-bot-refactored.py oracle-dm-discord-bot.py

# Run bot normally
uv run python oracle-dm-discord-bot.py
```

**Option 2: Run refactored file directly**
```bash
uv run python oracle-dm-discord-bot-refactored.py
```

### No breaking changes!
- All functionality preserved
- Same commands, same behavior
- Same environment variables
- No database changes needed

---

## 🧩 Adding New Features

### Best Practices:

1. **Create new module if feature is substantial** (100+ lines)
   - Example: `combat_system.py`, `inventory_management.py`

2. **Add to existing module if feature is related**
   - Music commands → `music_control.py`
   - New backend endpoint → `backend_integration.py`

3. **Always separate concerns:**
   - HTTP calls → `backend_integration.py`
   - Discord events → `event_handlers.py`
   - Business logic → Feature-specific module
   - State management → Module with relevant state

### Example: Adding Combat System

```python
# combat_system.py
"""Combat System Module - Turn-based combat management."""
import discord
from typing import Dict

# State tracking
active_combats: Dict[int, Dict] = {}  # channel_id -> combat state

async def start_combat(channel, participants):
    """Initialize combat encounter."""
    ...

async def process_combat_action(channel, action):
    """Handle player combat action."""
    ...
```

Then in `oracle-dm-discord-bot-refactored.py`:
```python
import combat_system

@bot.command(name="attack")
async def attack(ctx, target):
    await combat_system.process_combat_action(ctx.channel, {"action": "attack", "target": target})
```

---

## 📊 Module Size Comparison

| Module | Lines | Purpose |
|--------|-------|---------|
| **OLD: oracle-dm-discord-bot.py** | **1,003** | Monolithic (everything) |
| **NEW: oracle-dm-discord-bot-refactored.py** | **167** | Main entry (83% reduction ✅) |
| character_creation.py | 411 | Character creation |
| event_handlers.py | 254 | Event handling |
| music_player.py | 213 | Lavalink integration |
| backend_integration.py | 95 | HTTP client |
| dm_commands.py | 70 | Command handlers |
| music_control.py | 60 | Music preferences |
| **TOTAL (modular)** | **1,270** | Organized, maintainable |

**Benefits:**
- ✅ 83% smaller main file
- ✅ Clear separation of concerns
- ✅ Easier to test individual modules
- ✅ Better code reusability
- ✅ Reduced merge conflicts in team development
- ✅ Faster to locate bugs
- ✅ Easier onboarding for new developers

---

## 🧪 Testing Strategy

Each module can now be tested independently:

```python
# Example: Test character creation
import character_creation

# Mock discord objects
mock_message = ...
mock_guild = ...

# Test extraction
char_data = await character_creation.extract_character_from_avrae_embed(mock_message)
assert char_data["name"] == "Expected Name"
```

---

## 🔮 Future Enhancements

Suggested new modules for future features:

1. **`combat_system.py`** - Turn-based combat, initiative, damage rolls
2. **`inventory_management.py`** - Item tracking, trading, equipment
3. **`quest_system.py`** - Quest tracking, rewards, progression
4. **`npc_manager.py`** - NPC dialogue, AI personalities, relationships
5. **`world_state.py`** - Location tracking, time system, weather
6. **`party_management.py`** - Party formation, shared resources
7. **`dice_roller.py`** - Custom dice rolls, advantage/disadvantage
8. **`economy_system.py`** - Currency, shops, pricing

Each can be added without touching existing code!
