"""
Character Creation Module - Handles all character creation logic.
Includes session management, Avrae imports, and AI-guided creation.
"""
import asyncio
from datetime import datetime, timezone
from typing import Dict, Optional
import uuid

import discord
import aiohttp


# Track ephemeral character creation channels: channel_id ->
# {user_id, created_at, joined_at, last_message_at, text_channel_id}
ephemeral_cc_channels: Dict[int, Dict] = {}

# Track cleanup tasks so we can cancel them if needed
cleanup_tasks: Dict[int, asyncio.Task] = {}

# Track guided character creation state: channel_id -> {user_id, step, char_data, session_id}
guided_cc_state: Dict[int, Dict] = {}


async def cleanup_ephemeral_channel(guild: discord.Guild, channel_id: int, user_id: str, reason: str = "Inactivity (1 hour)"):
    """Delete an ephemeral character creation channel and notify the player."""
    from music_player import stop_music_in_channel
    from music_control import music_preferences
    
    try:
        # Stop music if playing
        await stop_music_in_channel(channel_id)
        
        session_data = ephemeral_cc_channels.get(channel_id, {})
        text_channel_id = session_data.get("text_channel_id")

        # Delete linked CC text channel first, if present.
        if text_channel_id:
            text_channel = guild.get_channel(text_channel_id)
            if text_channel:
                try:
                    await text_channel.delete(reason=f"Character creation cleanup: {reason}")
                    print(f"[cleanup] Deleted linked text channel {text_channel_id} for user {user_id}")
                except Exception as e:
                    print(f"[cleanup text channel error] {e}")

        channel = guild.get_channel(channel_id)
        if channel:
            # Try to DM the user first
            member = guild.get_member(int(user_id))
            if member:
                try:
                    await member.send(f"Your character creation session has been closed. Reason: {reason}")
                except discord.Forbidden:
                    pass
            
            # Delete the channel
            await channel.delete(reason=f"Character creation cleanup: {reason}")
            print(f"[cleanup] Deleted channel {channel_id} for user {user_id}")
    except Exception as e:
        print(f"[cleanup error] {e}")
    finally:
        if channel_id in ephemeral_cc_channels:
            del ephemeral_cc_channels[channel_id]
        if channel_id in cleanup_tasks:
            del cleanup_tasks[channel_id]
        if channel_id in music_preferences:
            del music_preferences[channel_id]
            print(f"[cleanup] Removed music preferences for channel {channel_id}")


async def schedule_cleanup_task(guild: discord.Guild, channel_id: int, user_id: str, delay_seconds: int = 3600):
    """Schedule a channel cleanup after the specified delay."""
    await asyncio.sleep(delay_seconds)
    await cleanup_ephemeral_channel(guild, channel_id, user_id)


def rearm_cleanup_task(guild: discord.Guild, channel_id: int, user_id: str, delay_seconds: int = 3600) -> None:
    """Cancel any existing cleanup timer and start a fresh one."""
    existing = cleanup_tasks.get(channel_id)
    if existing and not existing.done():
        existing.cancel()
    cleanup_tasks[channel_id] = asyncio.create_task(
        schedule_cleanup_task(guild, channel_id, user_id, delay_seconds)
    )


def find_cc_session_by_text_channel(text_channel_id: int) -> tuple[Optional[int], Optional[Dict]]:
    """Return (voice_channel_id, session_data) for a linked CC text channel."""
    for voice_id, data in ephemeral_cc_channels.items():
        if data.get("text_channel_id") == text_channel_id:
            return voice_id, data
    return None, None


async def extract_character_from_avrae_embed(message: discord.Message) -> Optional[Dict]:
    """Parse character data from an Avrae embed or message."""
    char_data = {
        "name": None,
        "race": None,
        "char_class": None,
        "level": 1,
        "stats": {},
        "ddb_url": None,
        "avrae_import_text": message.content,
    }

    # Try to extract D&D Beyond URL from the message content
    if "dndbeyond.com" in message.content.lower():
        parts = message.content.split()
        for part in parts:
            if "dndbeyond.com" in part.lower():
                char_data["ddb_url"] = part.strip("<>")
                break

    # If there's an embed, try to extract character info
    if not message.embeds:
        return char_data

    embed = message.embeds[0]
    
    # Extract character name from the embed title
    if embed.title:
        # Typical format: "Character Name - Level X Class Race"
        parts = embed.title.split(" - ")
        if len(parts) >= 1:
            char_data["name"] = parts[0].strip()
        if len(parts) >= 2:
            # Try to extract level and class
            level_class = parts[1]
            try:
                words = level_class.split()
                for i, word in enumerate(words):
                    if word.lower() == "level" and i + 1 < len(words):
                        char_data["level"] = int(words[i + 1])
                    elif i + 1 < len(words) and words[i].lower() in ["monk", "fighter", "wizard", "rogue", "cleric", "paladin", "ranger", "bard", "sorcerer", "warlock", "barbarian", "druid"]:
                        char_data["char_class"] = words[i]
                        if i + 1 < len(words):
                            char_data["race"] = words[i + 1]
            except Exception as e:
                print(f"[extract_character error parsing level/class] {e}")
    
    # Extract stats from embed fields if available
    if embed.fields:
        for field in embed.fields:
            field_name = field.name.lower()
            if "str" in field_name or "strength" in field_name:
                try:
                    char_data["stats"]["strength"] = int(field.value.split()[0])
                except:
                    pass
            elif "dex" in field_name or "dexterity" in field_name:
                try:
                    char_data["stats"]["dexterity"] = int(field.value.split()[0])
                except:
                    pass
            elif "con" in field_name or "constitution" in field_name:
                try:
                    char_data["stats"]["constitution"] = int(field.value.split()[0])
                except:
                    pass
            elif "int" in field_name or "intelligence" in field_name:
                try:
                    char_data["stats"]["intelligence"] = int(field.value.split()[0])
                except:
                    pass
            elif "wis" in field_name or "wisdom" in field_name:
                try:
                    char_data["stats"]["wisdom"] = int(field.value.split()[0])
                except:
                    pass
            elif "cha" in field_name or "charisma" in field_name:
                try:
                    char_data["stats"]["charisma"] = int(field.value.split()[0])
                except:
                    pass

    return char_data


def _registration_succeeded(result: Dict) -> bool:
    """Robustly detect a successful registration from the backend response.

    The backend returns ``{"status": "ok", "character_id": ...}`` (no ``ok`` key),
    so check for a character id / ok status rather than a single flag.
    """
    if not isinstance(result, dict):
        return False
    if result.get("error"):
        return False
    return bool(result.get("character_id")
                or result.get("status") == "ok"
                or result.get("ok"))


async def _offer_portrait_setup(channel, player, result: Dict, char_name: str, backend_url: str):
    """After creation, invite the player to add a character portrait (DM preferred)."""
    character_id = result.get("character_id") if isinstance(result, dict) else None
    if not character_id:
        return
    try:
        import character_display
    except Exception as e:
        print(f"[portrait setup import error] {e}")
        return
    view = character_display.PortraitSetupView(
        character_id, char_name, backend_url, player.id)
    embed = discord.Embed(
        title="🖼️ Add a portrait?",
        description=(
            f"Would you like a portrait for **{char_name}**?\n\n"
            "• **Generate from description** — describe their appearance and I'll paint one.\n"
            "• **I'll upload one** — send your own image.\n"
            "• **Skip for now** — add one later anytime with `!portrait`."
        ),
        color=0x4A6FA5,
    )
    # Prefer a DM so the prompt survives the ephemeral CC channel's cleanup.
    try:
        await player.send(embed=embed, view=view)
        return
    except Exception:
        pass
    try:
        await channel.send(embed=embed, view=view)
    except Exception as e:
        print(f"[portrait setup send error] {e}")


async def validate_and_register_character(message: discord.Message, char_data: Dict, user_id: str, backend_url: str) -> bool:
    """Validate character and register it in the backend. `message` is the Avrae message detected."""
    from backend_integration import register_character_backend
    from music_control import switch_music
    
    channel = message.channel
    # The Oracle starts every character at level 1; advancement is tracked in-system.
    if char_data.get("level", 1) != 1:
        await channel.send(
            "ℹ️ The Oracle begins all heroes at **level 1** — your character will "
            "enter the world at level 1 and level up in play."
        )
    char_data["level"] = 1

    payload = {
        "discord_user_id": user_id,
        "name": char_data["name"],
        "race": char_data.get("race"),
        "char_class": char_data.get("char_class"),
        "subclass": char_data.get("subclass"),
        "level": 1,
        "stats": char_data.get("stats"),
        "ddb_url": char_data.get("ddb_url"),
        "avrae_import_text": char_data.get("avrae_import_text", ""),
        "approve": True,
        "home_region": "Gatvorhain",
    }

    result = await register_character_backend(payload, backend_url)
    if _registration_succeeded(result):
        await channel.send(f"✅ Character **{char_data['name']}** imported and approved! You can now enter the world with `!enterworld`.")
        
        # Switch to celebration/completion music if in a CC voice channel
        for voice_id, session_data in ephemeral_cc_channels.items():
            if session_data.get("text_channel_id") == channel.id:
                voice_channel = channel.guild.get_channel(voice_id)
                if voice_channel:
                    from music_control import music_preferences
                    if voice_id in music_preferences:
                        await switch_music(voice_channel, "character_complete")
                break

        await _offer_portrait_setup(channel, message.author, result, char_data["name"], backend_url)
        return True
    else:
        await channel.send(f"❌ Failed to register character. Server error.")
        return False


async def get_dm_guidance(session_id: str, username: str, message: str, backend_url: str) -> str:
    """Call the DM brain for guidance during character creation."""
    payload = {
        "session_id": session_id,
        "user_id": "dm_guide",
        "username": username,
        "message": message,
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(backend_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("reply", "The Oracle is silent...")
                else:
                    return "The Oracle is contemplating..."
        except asyncio.TimeoutError:
            return "The Oracle takes time to respond..."
        except Exception as e:
            print(f"[get_dm_guidance error] {e}")
            return "The Oracle's voice is unclear..."


async def start_guided_character_creation(text_channel: discord.TextChannel, voice_channel_id: int, user_id: str, username: str, backend_url: str):
    """Start the AI-guided character creation flow."""
    
    # Create a session for this character creation conversation
    session_id = f"cc_guide:{user_id}:{uuid.uuid4().hex}"

    # Initialize state; keyed by the text channel id so we can handle messages there
    guided_cc_state[text_channel.id] = {
        "user_id": user_id,
        "username": username,
        "session_id": session_id,
        "voice_channel_id": voice_channel_id,
        "step": "starting",
        "char_data": {
            "name": None,
            "race": None,
            "char_class": None,
            "level": 1,
            "stats": {},
            "ddb_url": None,
            "avrae_import_text": None,
        },
        "waiting_for_input": False,
    }
    
    # Get opening guidance from the DM
    opening_prompt = (
        "You are the Oracle DM guiding a player through character creation for D&D. "
        "The player is Level 1 (fixed). Help them navigate D&D Beyond to create a character sheet, "
        "then guide them through choosing race, class, and allocating stats. Be encouraging and ask one question at a time. "
        "First, greet them and ask their character's name."
    )
    
    guidance = await get_dm_guidance(session_id, username, opening_prompt, backend_url)
    await text_channel.send(f"🎭 The Oracle begins: {guidance}")

    guided_cc_state[text_channel.id]["waiting_for_input"] = True


async def process_guided_cc_input(channel: discord.TextChannel, message: discord.Message, backend_url: str):
    """Process player input during guided character creation."""
    if channel.id not in guided_cc_state:
        return
    
    state = guided_cc_state[channel.id]
    user_text = message.content.strip()
    
    # Get next guidance from the DM
    guidance = await get_dm_guidance(state["session_id"], state["username"], user_text, backend_url)
    await channel.send(f"🎭 The Oracle: {guidance}")
    
    # Update character data based on conversation (simple extraction)
    lower_text = user_text.lower()
    
    # Try to extract character name
    if state["char_data"]["name"] is None and len(user_text) > 1 and not any(verb in lower_text for verb in ["is", "are", "like", "want", "choose", "pick", "class", "race"]):
        # Assume it's a name
        state["char_data"]["name"] = user_text.strip()
    
    # Try to extract race
    races = ["human", "elf", "dwarf", "halfling", "dragonborn", "gnome", "half-elf", "half-orc", "tiefling"]
    for race in races:
        if race in lower_text:
            state["char_data"]["race"] = race.capitalize()
            break
    
    # Try to extract class
    classes = ["fighter", "wizard", "rogue", "cleric", "ranger", "paladin", "barbarian", "bard", "druid", "monk", "sorcerer", "warlock"]
    for char_class in classes:
        if char_class in lower_text:
            state["char_data"]["char_class"] = char_class.capitalize()
            break


async def finalize_guided_character(channel: discord.TextChannel, player: discord.User, backend_url: str):
    """Finalize and register the guided character creation."""
    from backend_integration import register_character_backend
    
    if channel.id not in guided_cc_state:
        return
    
    state = guided_cc_state[channel.id]
    char_data = state["char_data"]
    
    # Basic validation
    if not char_data["name"]:
        await channel.send("❌ Character name is required! Please tell me your character's name.")
        return
    
    if not char_data["race"]:
        await channel.send("❌ Character race is required! Please choose a race.")
        return
    
    if not char_data["char_class"]:
        await channel.send("❌ Character class is required! Please choose a class.")
        return
    
    # Register character
    payload = {
        "discord_user_id": str(player.id),
        "name": char_data["name"],
        "race": char_data["race"],
        "char_class": char_data["char_class"],
        "level": 1,
        "stats": char_data.get("stats", {}),
        "ddb_url": None,
        "avrae_import_text": None,
        "approve": True,
        "home_region": "Gatvorhain",
    }
    
    result = await register_character_backend(payload, backend_url)
    if _registration_succeeded(result):
        await channel.send(f"✅ Character **{char_data['name']}** created and approved! You can now enter the world with `!enterworld`.")

        await _offer_portrait_setup(channel, player, result, char_data["name"], backend_url)

        # Clean up state
        del guided_cc_state[channel.id]
        
        # Schedule channel cleanup
        voice_channel_id = state["voice_channel_id"]
        if voice_channel_id in ephemeral_cc_channels:
            await asyncio.sleep(2)
            await cleanup_ephemeral_channel(channel.guild, voice_channel_id, str(player.id), reason="Character successfully created")
    else:
        await channel.send(f"❌ Failed to register character. Please try again.")


async def create_character_creation_session(ctx_or_msg, bot):
    """Create ephemeral voice channel with linked text chat for character creation.
    Accepts either a `commands.Context` or a `discord.Message` (used from on_message).
    """
    from music_control import music_preferences
    
    # Normalize context/message to common variables
    author = getattr(ctx_or_msg, "author", None)
    guild = getattr(ctx_or_msg, "guild", None)
    send_target = getattr(ctx_or_msg, "channel", None)

    if author is None or guild is None or send_target is None:
        # Fallback: try to handle commands.Context-like objects
        try:
            author = ctx_or_msg.author
            guild = ctx_or_msg.guild
            send_target = ctx_or_msg.channel
        except Exception:
            print("[create_cc_session error] invalid context/message passed")
            return

    user_id = str(author.id)
    username = author.display_name

    # Create voice channel (Discord automatically creates linked text chat in some setups)
    channel_name = f"cc-{username.lower().replace(' ', '-')}-{user_id[-6:]}"
    
    # Base overwrites: deny @everyone, allow the player and bot
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        author: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, connect=True, speak=True),
        bot.user: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, connect=True, speak=True),
    }

    # Add Avrae if it's in the guild - grant full permissions
    avrae_member = discord.utils.find(lambda m: m.name.lower() == "avrae", guild.members)

    if avrae_member:
        print(f"[cc_session] Found Avrae bot, granting full permissions")
        overwrites[avrae_member] = discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True, connect=True, speak=True)

    try:
        # Find the "Character Creation" category to place the voice channel there
        category = discord.utils.find(lambda c: isinstance(c, discord.CategoryChannel) and c.name == "Character Creation", guild.channels)
        
        # Create voice channel (may raise Forbidden if bot lacks Manage Channels)
        voice_channel = await guild.create_voice_channel(channel_name, category=category, overwrites=overwrites, reason="Character creation session")

        # Step 1: Voice channel is now created
        print(f"[cc_session] Created voice channel: {voice_channel.name} (ID: {voice_channel.id})")
        
        # Step 2: Track the channel for inactivity cleanup
        ephemeral_cc_channels[voice_channel.id] = {
            "user_id": user_id,
            "created_at": datetime.now(timezone.utc),
            "joined_at": None,
            "last_message_at": datetime.now(timezone.utc),
            "text_channel_id": None,
        }
        
        # Initialize music preferences (enabled by default)
        music_preferences[voice_channel.id] = {
            "enabled": True,
            "current_playlist": "cc_menu",
        }
        
        # Schedule cleanup task for the pre-join waiting window.
        rearm_cleanup_task(guild, voice_channel.id, user_id, 3600)
        
        # Step 3: Notify the user
        await send_target.send(
            f"✅ {author.mention} Your character creation session is ready!\n"
            f"🔊 Join the voice channel: **{voice_channel.name}** to begin.\n"
            f"You have **1 hour** before the session expires."
        )

    except discord.Forbidden:
        print(f"[create_cc_session Forbidden] Bot lacks 'Manage Channels' permission")
        # Inform user with actionable steps
        await send_target.send(
            "❌ Could not create character creation session. The bot is missing required permissions. "
            "Please grant the bot the `Manage Channels` permission (and ensure the bot's role is above the roles of any members it sets channel overwrites for)."
        )
    except Exception as e:
        print(f"[create_cc_session error] {e}")
        await send_target.send(f"❌ Could not create character creation session. Check bot permissions.")
