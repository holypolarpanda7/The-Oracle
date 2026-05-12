import os
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import discord
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp
import wavelink

# Import music player module
import music_player

# Load env vars from cred.env in the same folder
load_dotenv("cred.env")

TOKEN = os.getenv("ORACLE_DM_TOKEN")
ADMIN_ID = os.getenv("ORACLE_DM_ADMIN_ID")
BACKEND_URL = os.getenv("ORACLE_DM_BACKEND_URL")
RESET_URL = BACKEND_URL.rsplit("/", 1)[0] + "/reset"
ENTER_URL = BACKEND_URL.rsplit("/", 1)[0] + "/enterworld"

if not TOKEN:
    raise RuntimeError("ORACLE_DM_TOKEN not found in cred.env!")
if not ADMIN_ID:
    raise RuntimeError("ORACLE_DM_ADMIN_ID not found in cred.env!")
if not BACKEND_URL:
    raise RuntimeError("ORACLE_DM_BACKEND_URL not found in cred.env!")

ADMIN_ID = str(ADMIN_ID)

# Entry channel name (the one where players type to start character creation)
ENTRY_CHANNEL_NAME = "enter-the-world-of-gatvorhain🛖"
CHARACTER_CREATION_URL = BACKEND_URL.rsplit("/", 1)[0] + "/register_character"
CHECK_CHARACTER_URL = BACKEND_URL.rsplit("/", 1)[0] + "/check_character"
DM_CHAT_URL = BACKEND_URL.rsplit("/", 1)[0] + "/chat"

intents = discord.Intents.default()
intents.message_content = True  # Needed to read chat messages
intents.voice_states = True  # Needed for voice channels
intents.members = True  # Needed to access guild members list

bot = commands.Bot(command_prefix="!", intents=intents)

# Keep track of which channels have DM mode enabled
active_dm_channels = set()

# Track ephemeral character creation channels: channel_id -> {user_id, created_at, last_message_at}
ephemeral_cc_channels: Dict[int, Dict] = {}

# Track cleanup tasks so we can cancel them if needed
cleanup_tasks: Dict[int, asyncio.Task] = {}

# Track guided character creation state: channel_id -> {user_id, step, char_data, session_id}
guided_cc_state: Dict[int, Dict] = {}

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


async def toggle_music(voice_channel_id: int, enabled: bool) -> bool:
    """Toggle music on/off for a voice channel. Returns True if state changed."""
    if voice_channel_id not in music_preferences:
        print(f"[music] No music preferences found for channel {voice_channel_id}")
        return False
    
    current_state = music_preferences[voice_channel_id]["enabled"]
    if current_state == enabled:
        return False  # No change
    
    music_preferences[voice_channel_id]["enabled"] = enabled
    
    if enabled:
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


def is_admin(user: discord.abc.User) -> bool:
    """Return True if this user is allowed to control DM mode."""
    return str(user.id) == ADMIN_ID


async def check_character_in_db(user_id: str) -> bool:
    """Check if a user has a character registered in the backend."""
    payload = {"discord_user_id": user_id}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(CHECK_CHARACTER_URL, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("has_character", False)
        except Exception as e:
            print(f"[check_character error] {e}")
    return False


async def cleanup_ephemeral_channel(guild: discord.Guild, channel_id: int, user_id: str, reason: str = "Inactivity (1 hour)"):
    """Delete an ephemeral character creation channel and notify the player."""
    try:
        # Stop music if playing
        await music_player.stop_music_in_channel(channel_id)
        
        channel = guild.get_channel(channel_id)
        if channel:
            # Try to DM the user first
            member = guild.get_member(int(user_id))
            if member:
                try:
                    await member.send(f"Your character creation session was closed due to {reason}.")
                except Exception:
                    pass
            # Delete the voice and text channels
            await channel.delete(reason=reason)
            print(f"[cleanup] Deleted ephemeral channel {channel_id} ({reason})")
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
    """Schedule a channel for deletion after a delay if no activity."""
    try:
        await asyncio.sleep(delay_seconds)
        await cleanup_ephemeral_channel(guild, channel_id, user_id, reason="Inactivity (1 hour)")
    except asyncio.CancelledError:
        print(f"[cleanup] Task cancelled for channel {channel_id}")
    except Exception as e:
        print(f"[cleanup task error] {e}")


async def extract_character_from_avrae_embed(message: discord.Message) -> Optional[Dict]:
    """Extract character data from Avrae's embed when a character is imported."""
    if not message.embeds:
        return None
    
    # Avrae sends embeds with character data; extract key fields
    embed = message.embeds[0]
    char_data = {
        "name": None,
        "level": 1,
        "race": None,
        "char_class": None,
        "stats": {},
        "avrae_import_text": message.content or "",
        "ddb_url": embed.url if embed.url else None,
    }
    
    # Parse embed title or fields for character info
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
            except Exception:
                pass
    
    # Parse fields for additional data (stats, etc.)
    for field in embed.fields:
        field_name = field.name.lower()
        field_value = field.value.lower()
        
        if "str" in field_name or "strength" in field_name:
            try:
                char_data["stats"]["str"] = int(field_value.split()[0])
            except Exception:
                pass
        elif "dex" in field_name or "dexterity" in field_name:
            try:
                char_data["stats"]["dex"] = int(field_value.split()[0])
            except Exception:
                pass
        elif "con" in field_name or "constitution" in field_name:
            try:
                char_data["stats"]["con"] = int(field_value.split()[0])
            except Exception:
                pass
        elif "int" in field_name or "intelligence" in field_name:
            try:
                char_data["stats"]["int"] = int(field_value.split()[0])
            except Exception:
                pass
        elif "wis" in field_name or "wisdom" in field_name:
            try:
                char_data["stats"]["wis"] = int(field_value.split()[0])
            except Exception:
                pass
        elif "cha" in field_name or "charisma" in field_name:
            try:
                char_data["stats"]["cha"] = int(field_value.split()[0])
            except Exception:
                pass
    
    return char_data if char_data["name"] else None


async def validate_and_register_character(message: discord.Message, char_data: Dict, user_id: str) -> bool:
    """Validate character and register it in the backend. `message` is the Avrae message detected."""
    channel = message.channel
    # Validate level
    if char_data["level"] < 1 or char_data["level"] > 20:
        await channel.send(f"❌ Invalid character level: {char_data['level']} (allowed: 1-20).")
        return False

    payload = {
        "discord_user_id": user_id,
        "name": char_data["name"],
        "race": char_data.get("race"),
        "char_class": char_data.get("char_class"),
        "level": char_data["level"],
        "stats": char_data.get("stats"),
        "ddb_url": char_data.get("ddb_url"),
        "avrae_import_text": char_data.get("avrae_import_text", ""),
        "approve": True,
        "home_region": "Gatvorhain",
    }

    result = await register_character_backend(payload)
    if result.get("ok"):
        await channel.send(f"✅ Character **{char_data['name']}** imported and approved! You can now enter the world with `!enterworld`.")
        
        # Switch to celebration/completion music if in a CC voice channel
        for voice_id, session_data in ephemeral_cc_channels.items():
            if session_data.get("text_channel_id") == channel.id:
                voice_channel = channel.guild.get_channel(voice_id)
                if voice_channel and voice_id in music_preferences:
                    await switch_music(voice_channel, "character_complete")
                break
        
        return True
    else:
        await channel.send(f"❌ Failed to register character. Server error.")
        return False

async def get_dm_guidance(session_id: str, username: str, message: str) -> str:
    """Call the DM brain for guidance during character creation."""
    payload = {
        "session_id": session_id,
        "user_id": "dm_guide",
        "username": username,
        "message": message,
        "channel_id": "cc_guide",
        "guild_id": "cc_guide",
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(DM_CHAT_URL, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("reply", "The Oracle is silent.")
                else:
                    return "The Oracle struggles to speak... (backend error)"
        except Exception as e:
            print(f"[dm_guidance error] {e}")
            return "The Oracle is briefly silent..."


async def register_character_backend(payload: Dict) -> Dict:
    """POST to backend /register_character and return JSON result or error dict."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(CHARACTER_CREATION_URL, json=payload) as resp:
                text = await resp.text()
                try:
                    data = await resp.json()
                except Exception:
                    data = {"status": "error", "message": text}
                return {"ok": resp.status == 200, "status": resp.status, "data": data}
        except Exception as e:
            print(f"[register_character_backend exception] {e}")
            return {"ok": False, "status": None, "data": {"message": str(e)}}


async def start_guided_character_creation(text_channel: discord.TextChannel, voice_channel_id: int, user_id: str, username: str):
    """Start guided character creation conversation via the DM brain."""
    import uuid
    
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
    
    guidance = await get_dm_guidance(session_id, username, opening_prompt)
    await text_channel.send(f"🎭 The Oracle begins: {guidance}")

    guided_cc_state[text_channel.id]["waiting_for_input"] = True


async def process_guided_cc_input(channel: discord.TextChannel, message: discord.Message):
    """Process player input during guided character creation."""
    if channel.id not in guided_cc_state:
        return
    
    state = guided_cc_state[channel.id]
    user_text = message.content.strip()
    
    # Get next guidance from the DM
    guidance = await get_dm_guidance(state["session_id"], state["username"], user_text)
    await channel.send(f"🎭 The Oracle: {guidance}")
    
    # Update character data based on conversation (simple extraction)
    lower_text = user_text.lower()
    
    # Try to extract character name
    if state["char_data"]["name"] is None and len(user_text) > 1 and not any(verb in lower_text for verb in ["is", "are", "like", "want", "choose", "pick", "class", "race"]):
        # Assume it's a name
        state["char_data"]["name"] = user_text.strip()
    
    # Try to extract race
    races = ["human", "elf", "dwarf", "halfling", "dragonborn", "gnome", "half-orc", "tiefling", "goliath", "aasimar"]
    for race in races:
        if race in lower_text:
            state["char_data"]["race"] = race.title()
            break
    
    # Try to extract class
    classes = ["barbarian", "bard", "cleric", "druid", "fighter", "monk", "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard"]
    for char_class in classes:
        if char_class in lower_text:
            state["char_data"]["char_class"] = char_class.title()
            break
    
    # Check if character is complete (has name, race, class)
    if state["char_data"]["name"] and state["char_data"]["race"] and state["char_data"]["char_class"]:
        # Character is ready
        await finalize_guided_character(channel, message.author)


async def finalize_guided_character(channel: discord.TextChannel, player: discord.User):
    """Register the guided character and clean up."""
    if channel.id not in guided_cc_state:
        return

    state = guided_cc_state[channel.id]
    char_data = state["char_data"]
    user_id = state["user_id"]
    
    # Validate (must have name, race, class, level = 1)
    if not all([char_data["name"], char_data["race"], char_data["char_class"]]):
        await channel.send("❌ Character incomplete. Let's continue building...")
        return
    
    # Register character
    payload = {
        "discord_user_id": user_id,
        "name": char_data["name"],
        "race": char_data["race"],
        "char_class": char_data["char_class"],
        "level": 1,  # Level 1 for all guided creations
        "stats": char_data.get("stats", {}),
        "ddb_url": char_data.get("ddb_url"),
        "avrae_import_text": None,
        "approve": True,
        "home_region": "Gatvorhain",
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(CHARACTER_CREATION_URL, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    await channel.send(f"✅ Character **{char_data['name']}** created! You can now enter the world with `!enterworld`.")
                    # Clean up: remove the voice channel using the stored voice_channel_id
                    await asyncio.sleep(2)
                    voice_id = state.get("voice_channel_id")
                    if voice_id:
                        await cleanup_ephemeral_channel(channel.guild, voice_id, user_id, reason="Character successfully created")
                    if channel.id in guided_cc_state:
                        del guided_cc_state[channel.id]
                    return
                else:
                    text = await resp.text()
                    print(f"[register error] HTTP {resp.status}: {text}")
                    await channel.send(f"❌ Failed to register character.")
        except Exception as e:
            print(f"[register exception] {e}")
            await channel.send(f"❌ Could not contact backend to register character.")
    
    


async def create_character_creation_session(ctx_or_msg):
    """Create ephemeral voice channel with linked text chat for character creation.
    Accepts either a `commands.Context` or a `discord.Message` (used from on_message).
    """
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
            "last_message_at": datetime.now(timezone.utc),
            "text_channel_id": None,
        }
        
        # Initialize music preferences (enabled by default)
        music_preferences[voice_channel.id] = {
            "enabled": True,
            "current_playlist": "cc_menu",
        }

        # Schedule cleanup task (1 hour = 3600 seconds)
        task = asyncio.create_task(schedule_cleanup_task(guild, voice_channel.id, user_id, delay_seconds=3600))
        cleanup_tasks[voice_channel.id] = task

        # Step 3: Post invite link + notification for player to join (single message)
        await send_target.send(
            f"🔗 {author.mention}, your character creation session is ready: {voice_channel.mention}\n"
            f"Click the voice channel to join — instructions will appear in the voice channel's text chat once you join."
        )


    except discord.Forbidden as e:
        print(f"[create_cc_session error] {e}")
        # Inform user with actionable steps
        await send_target.send(
            "❌ Could not create character creation session. The bot is missing required permissions. "
            "Please grant the bot the `Manage Channels` permission (and ensure the bot's role is above the roles of any members it sets channel overwrites for)."
        )
    except Exception as e:
        print(f"[create_cc_session error] {e}")
        await send_target.send(f"❌ Could not create character creation session. Check bot permissions.")





@bot.event
async def on_ready():
    print(f"Bot is online as {bot.user} (ID: {bot.user.id})")
    
    # Connect to Lavalink via music_player module
    await music_player.setup_lavalink(bot)
    
    print("Ready to DM!")


@bot.event
async def on_wavelink_node_ready(payload: wavelink.NodeReadyEventPayload):
    """Called when Lavalink node is ready."""
    await music_player.on_wavelink_node_ready(payload)


@bot.event
async def on_wavelink_track_start(payload: wavelink.TrackStartEventPayload):
    """Called when a track starts playing."""
    await music_player.on_wavelink_track_start(payload)


@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    """Called when a track ends - play next track in queue."""
    await music_player.on_wavelink_track_end(payload)


@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    """Handle reactions on character creation instructions."""
    # Ignore bot's own reactions
    if user.bot:
        return
    
    # Find the ephemeral CC session matching this text channel (we store sessions keyed by voice channel id)
    cc_voice_id = None
    cc_data = None
    for v_id, data in ephemeral_cc_channels.items():
        if data.get("text_channel_id") == reaction.message.channel.id:
            cc_voice_id = v_id
            cc_data = data
            break

    if cc_data is None:
        return

    if str(user.id) != cc_data["user_id"]:
        return

    # ✅ reaction = ready to import (already handled by Avrae listener)
    if str(reaction.emoji) == "✅":
        await reaction.message.channel.send("Waiting for your Avrae import... Type `!import [D&D Beyond link]`")

    # ❌ reaction = create from scratch (AI-guided)
    elif str(reaction.emoji) == "❌":
        await reaction.message.channel.send("🎨 No problem! Let's build your character together. I'll ask you some questions.\n\nWhat's your character's name?")
        # Start guided character creation via DM brain; pass text channel and voice channel id
        await start_guided_character_creation(reaction.message.channel, cc_voice_id, cc_data["user_id"], user.display_name)

    # 🔇 reaction = turn music OFF
    elif str(reaction.emoji) == "🔇":
        if await toggle_music(cc_voice_id, False):
            await reaction.message.channel.send("🔇 Music has been turned off.")
        else:
            await reaction.message.channel.send("🔇 Music is already off.")

    # 🔊 reaction = turn music ON
    elif str(reaction.emoji) == "🔊":
        if await toggle_music(cc_voice_id, True):
            await reaction.message.channel.send("🔊 Music has been turned back on.")
        else:
            await reaction.message.channel.send("🔊 Music is already on.")





@bot.command(name="startdm")
async def start_dm(ctx: commands.Context):
    """Enable DM mode in this channel (admin only)."""
    if not is_admin(ctx.author):
        await ctx.send("⛔ Only the Oracle may invoke DM mode.")
        return

    channel_id = ctx.channel.id
    active_dm_channels.add(channel_id)
    await ctx.send("🧙‍♂️ DM mode **enabled** in this channel. Speak, adventurers.")


@bot.command(name="stopdm")
async def stop_dm(ctx: commands.Context):
    """Disable DM mode in this channel (admin only)."""
    if not is_admin(ctx.author):
        await ctx.send("⛔ Only the Oracle may dismiss the DM.")
        return

    channel_id = ctx.channel.id
    if channel_id in active_dm_channels:
        active_dm_channels.remove(channel_id)
        await ctx.send("🧙‍♂️ DM mode **disabled** in this channel. The DM falls silent.")
    else:
        await ctx.send("DM mode is not currently enabled in this channel.")


@bot.command(name="resetdm")
async def reset_dm(ctx: commands.Context):
    """Reset the DM story/state for this channel (admin only)."""
    if not is_admin(ctx.author):
        await ctx.send("⛔ Only the Oracle may rewrite the threads of fate.")
        return

    session_id = f"{ctx.guild.id}:{ctx.channel.id}"

    status_message = await reset_backend_session(session_id)

    await ctx.send(f"🧵 The Oracle severs the current thread of fate for this channel.\n{status_message}")


@bot.command(name="enterworld")
async def enter_world(ctx: commands.Context, *, character_name: str = None):
    """Player asks to enter the world. Creates a private session channel if character exists."""
    payload = {
        "user_id": str(ctx.author.id),
        "username": ctx.author.display_name,
        "guild_id": str(ctx.guild.id),
        "character_name": character_name,
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(ENTER_URL, json=payload) as resp:
                data = await resp.json()
        except Exception as e:
            print(f"[EnterWorld backend exception] {e}")
            await ctx.send("⚠ The Oracle cannot reach the backend to begin your session.")
            return

    if not data:
        await ctx.send("⚠ Unexpected response from backend when trying to enter the world.")
        return

    if data.get("status") == "no_character":
        # Guide the user to create a character via DM
        dm_text = data.get("message") or "No characters found. Send `!createcharacter` to begin."
        try:
            await ctx.author.send(dm_text)
            await ctx.send(f"{ctx.author.mention}, I sent you a DM with next steps to create a character.")
        except Exception:
            await ctx.send(f"{ctx.author.mention}, I couldn't DM you. Please enable DMs from server members and try again.")
        return

    # Successful session creation
    session_id = data.get("session_id")
    intro = data.get("intro", "The Oracle is silent.")

    # Build a sanitized channel name
    safe_name = ctx.author.display_name.lower().replace(" ", "-")
    short = session_id[-6:] if session_id else "sess"
    channel_name = f"session-{safe_name}-{short}"

    # Create a private channel for this user and the Oracle (bot) + admin
    overwrites = {
        ctx.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        ctx.author: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        bot.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
    }

    # Also allow the configured admin to see the channel if present in guild
    try:
        admin_member = ctx.guild.get_member(int(ADMIN_ID))
        if admin_member:
            overwrites[admin_member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
    except Exception:
        pass

    try:
        channel = await ctx.guild.create_text_channel(channel_name, overwrites=overwrites, reason="AI DM session channel")
    except Exception as e:
        print(f"[channel create error] {e}")
        await ctx.send("⚠ Could not create a private session channel. Check bot permissions.")
        return

    # Mark channel active for DM mode
    active_dm_channels.add(channel.id)

    # Post the intro in the new channel and notify the player
    try:
        await channel.send(intro)
        await ctx.author.send(f"Your private session has been created: {channel.mention}")
        await ctx.send(f"{ctx.author.mention}, your private session is ready: {channel.mention}")
    except Exception:
        # If DM blocked, still notify in gateway channel (less private)
        await ctx.send(f"{ctx.author.mention}, your private session is ready: {channel.mention} (could not DM you)")


async def call_backend(message: discord.Message) -> str:
    """Send the message + context to the Oracle DM backend and return its reply text."""
    payload = {
        "session_id": f"{message.guild.id}:{message.channel.id}",
        "user_id": str(message.author.id),
        "username": message.author.display_name,
        "message": message.content,
        "channel_id": str(message.channel.id),
        "guild_id": str(message.guild.id),
    }

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(BACKEND_URL, json=payload) as resp:
                if resp.status != 200:
                    # Log error-like info and fall back to a generic message
                    text = await resp.text()
                    print(f"[Backend error] HTTP {resp.status}: {text}")
                    return "⚠ The DM hesitates, sensing a disturbance in the ether (backend error)."

                data = await resp.json()
        except Exception as e:
            print(f"[Backend exception] {e}")
            return "⚠ The DM is briefly silent (cannot reach backend)."

    return data.get("reply", "⚠ The DM has no words (empty reply from backend).")


async def reset_backend_session(session_id: str) -> str:
    """
    Ask the backend to reset the story/session for this session_id.
    Returns a human-readable status string.
    """
    payload = {"session_id": session_id}

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(RESET_URL, json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    print(f"[Backend reset error] HTTP {resp.status}: {text}")
                    return "⚠ The Oracle falters while trying to clear its vision (reset error)."

                data = await resp.json()
        except Exception as e:
            print(f"[Backend reset exception] {e}")
            return "⚠ The Oracle cannot reach its own memory to reset it."

    # Backend returns {status, message}
    return data.get("message", "The Oracle's memories shift, but it is unclear what changed.")


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    """Detect when a player joins a character creation voice channel and post instructions."""
    # Only care if they moved into a channel (not out of one)
    if after.channel is None:
        return
    
    # Check if this channel is one of our ephemeral CC channels
    if after.channel.id not in ephemeral_cc_channels:
        return
    
    session_data = ephemeral_cc_channels[after.channel.id]
    user_id = int(session_data["user_id"])
    
    # Only post instructions if it's the player who joined (not a bot or someone else)
    if member.id != user_id:
        return
    
    print(f"[on_voice_state_update] {member.display_name} (ID: {member.id}) joined CC voice channel {after.channel.name} (ID: {after.channel.id})")
    
    # Player joined the voice channel! Post instructions to the voice channel's text chat
    try:
        # First, check if we already have a text channel for this voice channel
        text_channel_id = session_data.get("text_channel_id")
        text_channel = None
        
        if text_channel_id:
            print(f"[on_voice_state_update] Looking for existing text channel ID: {text_channel_id}")
            text_channel = after.channel.guild.get_channel(text_channel_id)
            if text_channel:
                print(f"[on_voice_state_update] Found existing text channel: {text_channel.name}")
        
        # If no text channel yet, try to find one with matching name
        if not text_channel:
            print(f"[on_voice_state_update] Searching for text channel with name: {after.channel.name}")
            for ch in after.channel.guild.text_channels:
                if ch.name == after.channel.name:
                    text_channel = ch
                    print(f"[on_voice_state_update] Found matching text channel: {text_channel.name}")
                    break
        
        # If still no text channel, create one with proper permissions
        if not text_channel:
            print(f"[on_voice_state_update] No text channel found, creating one...")
            try:
                # Use the same permission overwrites as the voice channel
                guild = after.channel.guild
                player = member
                
                overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
                    player: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True),
                    bot.user: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True),
                }
                
                # Add Avrae if it exists
                avrae_member = discord.utils.find(lambda m: m.name.lower() == "avrae", guild.members)
                
                if avrae_member:
                    overwrites[avrae_member] = discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True)
                
                # Create the text channel in the same category as the voice channel
                text_channel = await guild.create_text_channel(
                    after.channel.name,
                    category=after.channel.category,
                    overwrites=overwrites,
                    reason="Text chat for character creation session"
                )
                print(f"[on_voice_state_update] Created new text channel: {text_channel.name}")
                
            except discord.Forbidden as e:
                print(f"[on_voice_state_update] Permission denied creating text channel: {e}")
                return
            except Exception as e:
                print(f"[on_voice_state_update] Error creating text channel: {e}")
                return
        
        # Send character creation instructions to the text channel
        print(f"[on_voice_state_update] Sending instructions to text channel: {text_channel.name}")
        
        # Start music in the voice channel (if enabled)
        if after.channel.id in music_preferences and music_preferences[after.channel.id]["enabled"]:
            await music_player.play_music_in_channel(after.channel, "cc_menu")
        
        instructions_msg = await text_channel.send(
            f"🎭 **Welcome, {member.display_name}!** 🎭\n\n"
            f"🎵 Background music is now playing in the voice channel.\n"
            f"• React with 🔇 to turn music OFF\n"
            f"• React with 🔊 to turn music back ON\n\n"
            f"Choose your character creation path:\n\n"
            f"**✅ Import from D&D Beyond**\n"
            f"If you already have a character sheet on D&D Beyond, use Avrae to import it.\n"
            f"Type `!import [link]` to begin.\n\n"
            f"**❌ Create with AI Guidance**\n"
            f"Let the Oracle guide you through character creation step by step.\n\n"
            f"React to this message with your choice:"
        )
        
        # Add reactions to the instructions message
        await instructions_msg.add_reaction("✅")
        await instructions_msg.add_reaction("❌")
        await instructions_msg.add_reaction("🔇")
        await instructions_msg.add_reaction("🔊")
        
        # Update the session data to track this text channel
        ephemeral_cc_channels[after.channel.id]["text_channel_id"] = text_channel.id
        
        print(f"[on_voice_state_update] Successfully posted instructions to {text_channel.name}")
        
    except discord.Forbidden as e:
        print(f"[on_voice_state_update Forbidden error] {e} (likely text channel permission issue)")
    except Exception as e:
        print(f"[on_voice_state_update error] {e}")


@bot.event
async def on_message(message: discord.Message):
    # Always ignore bots, including ourselves
    if message.author.bot:
        return

    # Let commands (like !startdm, !stopdm) run first
    await bot.process_commands(message)

    # Check if message is in the entry channel
    if message.channel.name == ENTRY_CHANNEL_NAME:
        # Check if player has a character in DB
        has_char = await check_character_in_db(str(message.author.id))
        if not has_char:
            # Start character creation flow
            await create_character_creation_session(message)
        else:
            # Player has a character; they can enter the world
            await message.reply("🌍 You already have a character! Use `!enterworld` to begin your adventure.")
        return

    # Check if this is an ephemeral character creation channel (look up by voice->text mapping)
    session_voice_id = None
    session_data = None
    if message.channel.id in ephemeral_cc_channels:
        # (unlikely for text channels) direct hit
        session_voice_id = message.channel.id
        session_data = ephemeral_cc_channels.get(session_voice_id)
    else:
        # Look for a session where this message was posted in the session's text channel
        for v_id, data in ephemeral_cc_channels.items():
            if data.get("text_channel_id") == message.channel.id:
                session_voice_id = v_id
                session_data = data
                break

    if session_data:
        # Update last message time for inactivity tracking
        ephemeral_cc_channels[session_voice_id]["last_message_at"] = datetime.now(timezone.utc)

        # Check if this is guided character creation in progress (keyed by text channel id)
        if message.channel.id in guided_cc_state:
            await process_guided_cc_input(message.channel, message)
            return

        # Check if this is Avrae posting a character import
        if message.author.name.lower() == "avrae" and (message.embeds or "imported" in message.content.lower()):
            user_id = str(session_data["user_id"])
            char_data = await extract_character_from_avrae_embed(message)

            if char_data:
                success = await validate_and_register_character(message, char_data, user_id)
                if success:
                    # Schedule cleanup to delete the voice channel after a short delay
                    await asyncio.sleep(2)
                    await cleanup_ephemeral_channel(message.guild, session_voice_id, user_id, reason="Character successfully created")
                    return
        return

    # If this message is a command (starts with the prefix), do NOT treat it as in-world text
    if message.content.strip().startswith(bot.command_prefix):
        return

    # If this channel is not in DM mode, ignore non-command messages
    if message.channel.id not in active_dm_channels:
        return

    user_text = message.content.strip()
    if not user_text:
        return

    # Call the backend stub for the DM reply
    dm_reply = await call_backend(message)

    await message.channel.send(dm_reply)


if __name__ == "__main__":
    # Start Lavalink server
    if music_player.start_lavalink_server():
        # Wait for Lavalink to fully start
        import time
        time.sleep(10)
    
    try:
        # Run the bot
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n[Bot] Shutting down...")
    finally:
        # Ensure Lavalink stops when bot exits
        music_player.stop_lavalink_server()
