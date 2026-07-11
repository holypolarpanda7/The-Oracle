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
import character_display
import game_session


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
# Entry channel id (preferred, stable across renames). Falls back to the name.
ENTRY_CHANNEL_ID = os.getenv("ORACLE_DM_ENTRY_CHANNEL_ID", "1447775459533262868")

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
bot.entry_channel_id = ENTRY_CHANNEL_ID
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
async def on_wavelink_track_exception(payload):
    await event_handlers.on_wavelink_track_exception_handler(payload)


@bot.event
async def on_wavelink_websocket_closed(payload):
    await event_handlers.on_wavelink_websocket_closed_handler(payload)


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


@bot.command(name="leave", aliases=["endsession", "logout"])
async def leave_session(ctx: commands.Context):
    """End the play session tied to this channel (session owner or admin only)."""
    session = game_session.active_sessions.get(ctx.channel.id)
    if not session:
        await ctx.send("There's no active session in this channel to leave.")
        return
    if str(ctx.author.id) != session["owner_id"] and not dm_commands.is_admin(ctx.author, ADMIN_ID):
        await ctx.send("Only the session's player (or the Oracle) can end this session.")
        return
    char_name = session["participants"].get(session["owner_id"], {}).get("character_name", "your character")
    await ctx.send(f"🌙 Ending **{char_name}**'s session — this channel will close shortly. Safe travels!")
    await game_session.end_session_for_channel(ctx.channel.id, bot, reason="Player ended session")


@bot.command(name="cancelcc", aliases=["endcc", "abortcc"])
async def cancel_character_creation(ctx: commands.Context):
    """Immediately close an active character-creation session (owner/admin)."""
    voice_id, session = character_creation.find_cc_session_by_text_channel(ctx.channel.id)
    if voice_id is None or not session:
        await ctx.send("There's no active character-creation session in this channel.")
        return

    owner_id = str(session.get("user_id", ""))
    requester_id = str(ctx.author.id)
    if requester_id != owner_id and not dm_commands.is_admin(ctx.author, ADMIN_ID):
        await ctx.send("Only the session owner (or the Oracle) can cancel this creation session.")
        return

    await ctx.send("🛑 Ending character creation now. Closing voice and chat...")
    await character_creation.cleanup_ephemeral_channel(
        ctx.guild,
        voice_id,
        owner_id,
        reason=f"Cancelled by {ctx.author.display_name}",
    )


@bot.command(name="sheet")
async def sheet(ctx: commands.Context, *, character_name: str = None):
    """Show your character sheet (rendered from your live character record)."""
    user_id = str(ctx.author.id)
    chosen, characters = await backend_integration.resolve_character(
        user_id, CHECK_CHARACTER_URL, character_name)
    if not chosen:
        if characters and character_name:
            names = ", ".join(c.get("name", "?") for c in characters)
            await ctx.send(f"❓ I couldn't find a character named **{character_name}**. "
                           f"You have: {names}")
        else:
            await ctx.send("📜 You don't have a character yet. Use `!enterworld` to create one.")
        return
    async with ctx.typing():
        data = await backend_integration.get_character_sheet(chosen["id"], BACKEND_URL)
    if not data:
        await ctx.send("⚠️ I couldn't retrieve that character sheet right now.")
        return
    embed, portrait_file = character_display.build_sheet_embed(data)
    if portrait_file:
        await ctx.send(embed=embed, file=portrait_file)
    else:
        await ctx.send(embed=embed)


@bot.command(name="inventory", aliases=["inv"])
async def inventory(ctx: commands.Context, *, character_name: str = None):
    """Show your character's inventory."""
    user_id = str(ctx.author.id)
    chosen, characters = await backend_integration.resolve_character(
        user_id, CHECK_CHARACTER_URL, character_name)
    if not chosen:
        if characters and character_name:
            names = ", ".join(c.get("name", "?") for c in characters)
            await ctx.send(f"❓ I couldn't find a character named **{character_name}**. "
                           f"You have: {names}")
        else:
            await ctx.send("🎒 You don't have a character yet. Use `!enterworld` to create one.")
        return
    async with ctx.typing():
        data = await backend_integration.get_inventory(chosen["id"], BACKEND_URL)
    if not data:
        await ctx.send("⚠️ I couldn't retrieve that inventory right now.")
        return
    await ctx.send(embed=character_display.build_inventory_embed(data))


@bot.command(name="portrait")
async def portrait(ctx: commands.Context, *, description: str = None):
    """Set your character's portrait: attach an image to upload one, or provide a
    description to have one generated. With no argument, shows the current portrait."""
    user_id = str(ctx.author.id)
    chosen, characters = await backend_integration.resolve_character(
        user_id, CHECK_CHARACTER_URL, None)
    if not chosen:
        await ctx.send("🖼️ You don't have a character yet. Use `!enterworld` to create one.")
        return
    char_id, char_name = chosen["id"], chosen.get("name", "Your character")

    # 1) Uploaded image attachment -> store it as the portrait.
    image_att = next(
        (a for a in ctx.message.attachments
         if (a.content_type or "").startswith("image/")
         or a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))),
        None,
    )
    if image_att:
        async with ctx.typing():
            raw = await image_att.read()
            import base64 as _b64
            result = await backend_integration.upload_portrait(
                char_id, BACKEND_URL, _b64.b64encode(raw).decode("ascii"),
                caption=f"{char_name} (portrait)")
        if result.get("error"):
            await ctx.send(f"⚠️ Couldn't save that portrait: {result['error']}")
            return
        embed, portrait_file = character_display.build_portrait_embed(result, char_name)
        if portrait_file:
            await ctx.send("✅ Portrait saved!", embed=embed, file=portrait_file)
        else:
            await ctx.send("✅ Portrait saved!")
        return

    # 2) Description provided -> generate a portrait.
    if description:
        await ctx.send("🎨 Painting your portrait — this can take a moment...")
        async with ctx.typing():
            result = await backend_integration.generate_portrait(
                char_id, BACKEND_URL, description=description)
        if result.get("error"):
            await ctx.send(f"⚠️ Couldn't generate a portrait: {result['error']}")
            return
        embed, portrait_file = character_display.build_portrait_embed(result, char_name)
        view = character_display.PortraitView(
            char_id, char_name, BACKEND_URL, ctx.author.id, description=description)
        if portrait_file:
            await ctx.send(embed=embed, file=portrait_file, view=view)
        else:
            await ctx.send(embed=embed, view=view)
        return

    # 3) No argument -> show the current portrait if one exists.
    data = await backend_integration.get_portrait(char_id, BACKEND_URL)
    if not data:
        await ctx.send(
            f"🖼️ **{char_name}** has no portrait yet. Attach an image with `!portrait`, "
            f"or use `!portrait <description>` to have one generated.")
        return
    embed, portrait_file = character_display.build_portrait_embed(data, char_name)
    if portrait_file:
        await ctx.send(embed=embed, file=portrait_file)
    else:
        await ctx.send(embed=embed)


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
