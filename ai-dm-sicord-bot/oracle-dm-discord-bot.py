"""
Oracle DM Discord Bot - Main Entry Point
Modular D&D session manager with character creation, music, and AI DM.
"""
import os
from dotenv import load_dotenv
import discord
from discord.ext import commands

# Import modules
import music_player
import music_control
import character_creation
import backend_integration
import dm_commands
import event_handlers


# Load env vars
load_dotenv("cred.env")

TOKEN = os.getenv("ORACLE_DM_TOKEN")
ADMIN_ID = os.getenv("ORACLE_DM_ADMIN_ID")
BACKEND_URL = os.getenv("ORACLE_DM_BACKEND_URL")
RESET_URL = BACKEND_URL.rsplit("/", 1)[0] + "/reset"
ENTER_URL = BACKEND_URL.rsplit("/", 1)[0] + "/enterworld"
CHECK_CHARACTER_URL = BACKEND_URL.rsplit("/", 1)[0] + "/check_character"
CHARACTER_CREATION_URL = BACKEND_URL.rsplit("/", 1)[0] + "/register_character"

# Entry channel name (where players type to start character creation)
ENTRY_CHANNEL_NAME = "enter-the-world-of-gatvorhain🛖"

if not TOKEN:
    raise RuntimeError("ORACLE_DM_TOKEN not found in cred.env!")
if not ADMIN_ID:
    raise RuntimeError("ORACLE_DM_ADMIN_ID not found in cred.env!")
if not BACKEND_URL:
    raise RuntimeError("ORACLE_DM_BACKEND_URL not found in cred.env!")


# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.reactions = True
intents.members = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Store URLs on bot instance for module access
bot.backend_url = BACKEND_URL
bot.reset_url = RESET_URL
bot.enter_url = ENTER_URL
bot.check_character_url = CHECK_CHARACTER_URL
bot.character_creation_url = CHARACTER_CREATION_URL
bot.entry_channel_name = ENTRY_CHANNEL_NAME
bot.admin_id = ADMIN_ID

# Track DM-enabled channels
active_dm_channels: set[int] = set()


# ==================== EVENT HANDLERS ====================

@bot.event
async def on_ready():
    await event_handlers.on_ready_handler(bot)


@bot.event
async def on_wavelink_node_ready(payload):
    await event_handlers.on_wavelink_node_ready_handler(payload)


@bot.event
async def on_wavelink_track_start(payload):
    await event_handlers.on_wavelink_track_start_handler(payload)


@bot.event
async def on_wavelink_track_end(payload):
    await event_handlers.on_wavelink_track_end_handler(payload)


@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    await event_handlers.on_reaction_add_handler(reaction, user, bot)


@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    await event_handlers.on_voice_state_update_handler(member, before, after, bot)


@bot.event
async def on_message(message: discord.Message):
    await event_handlers.on_message_handler(message, bot, active_dm_channels)


# ==================== COMMANDS ====================

@bot.command(name="startdm")
async def start_dm(ctx: commands.Context):
    """Enable DM mode in this channel (admin only)."""
    if not dm_commands.is_admin(ctx.author, ADMIN_ID):
        await ctx.send("⛔ Only the Oracle may invoke DM mode.")
        return
    await dm_commands.start_dm_command(ctx, active_dm_channels)


@bot.command(name="stopdm")
async def stop_dm(ctx: commands.Context):
    """Disable DM mode in this channel (admin only)."""
    if not dm_commands.is_admin(ctx.author, ADMIN_ID):
        await ctx.send("⛔ Only the Oracle may dismiss the DM.")
        return
    await dm_commands.stop_dm_command(ctx, active_dm_channels)


@bot.command(name="resetdm")
async def reset_dm(ctx: commands.Context):
    """Reset the DM conversation for this channel (admin only)."""
    if not dm_commands.is_admin(ctx.author, ADMIN_ID):
        await ctx.send("⛔ Only the Oracle may reset the DM.")
        return
    await dm_commands.reset_dm_command(ctx, RESET_URL)


@bot.command(name="enterworld")
async def enter_world(ctx: commands.Context, *, character_name: str = None):
    """Enter the world with your character."""
    user_id = str(ctx.author.id)
    
    # Check if user has a character
    has_character = await backend_integration.check_character_in_db(user_id, CHECK_CHARACTER_URL)
    
    if not has_character:
        # Start character creation flow
        await ctx.send(f"👋 Welcome, {ctx.author.display_name}! Let's create your character.")
        await character_creation.create_character_creation_session(ctx, bot)
    else:
        # Enter world
        await dm_commands.enter_world_command(ctx, character_name, CHECK_CHARACTER_URL, ENTER_URL)


# ==================== MAIN ====================

if __name__ == "__main__":
    # Start Lavalink server
    if music_player.start_lavalink_server():
        # Wait for Lavalink to start initializing (retry logic in setup_lavalink will handle the rest)
        import time
        print("[Bot] Waiting for Lavalink to start initializing...")
        time.sleep(5)
    
    try:
        # Run the bot
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n[Bot] Shutting down...")
    finally:
        # Ensure Lavalink stops when bot exits
        music_player.stop_lavalink_server()
