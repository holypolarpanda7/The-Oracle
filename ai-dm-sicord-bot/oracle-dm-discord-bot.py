"""
Oracle DM Discord Bot - Main Entry Point
Modular D&D session manager with character creation, music, and AI DM.
"""
import asyncio
import os
from dotenv import load_dotenv
import discord
from discord.ext import commands, tasks

# Import modules
import music_player
import music_control
import character_creation
import session_channels
import backend_integration
import dm_commands
import event_handlers
import character_display


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
    if not music_cue_poll.is_running():
        music_cue_poll.start()


# ---- Scene music: poll the backend for the DM's cues and switch playlists -----
# The Activity plays via the browser and can't reach the bot's voice player, so
# the backend records per-channel music cues and we poll them for active tables.
_music_cue_seq: dict[int, int] = {}


@tasks.loop(seconds=6.0)
async def music_cue_poll():
    for channel_id in list(session_channels.ephemeral_session_channels.keys()):
        try:
            since = _music_cue_seq.get(channel_id, 0)
            cue = await backend_integration.get_activity_music_cue(
                BACKEND_URL, channel_id, since)
            if not cue:
                continue
            seq, query = cue.get("seq", since), cue.get("query")
            if query and seq > since:
                _music_cue_seq[channel_id] = seq
                vc = bot.get_channel(channel_id)
                if isinstance(vc, discord.VoiceChannel):
                    mood = await music_control.apply_music_cue(vc, query)
                    if mood:
                        print(f"[music cue] {vc.name}: '{query}' -> {mood}")
        except Exception as e:
            print(f"[music cue loop] {e}")


@music_cue_poll.before_loop
async def _before_music_cue_poll():
    await bot.wait_until_ready()


@bot.event
async def on_reaction_add(reaction: discord.Reaction, user: discord.User):
    await event_handlers.on_reaction_add_handler(reaction, user, bot)


@bot.event
async def on_message(message: discord.Message):
    await event_handlers.on_message_handler(message, bot, active_dm_channels)


@bot.event
async def on_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
):
    # Ephemeral session tables: DM joiners the launch button, sweep empty tables.
    await session_channels.handle_voice_state_update(member, before, after, bot)


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
    """Legacy entry point. Character creation and play now live in the web
    Activity; this command just points players there."""
    entry = getattr(bot, "entry_channel_name", "enter-the-world-of-gatvorhain")
    await ctx.send(
        f"🔮 The Oracle now runs as an in-Discord **Activity**. Head to "
        f"**#{entry}**, join a voice channel, and press **Enter the Oracle** — "
        f"you'll create a character or resume your tale right there.")


@bot.command(name="duel")
async def duel(ctx: commands.Context, target: discord.Member = None, *, terms: str = "to-yield"):
    """Challenge another player to a SANCTIONED duel (they must accept).
    Usage: !duel @player [first-blood|to-yield|to-the-death]"""
    await dm_commands.duel_command(ctx, target, terms, BACKEND_URL)


@bot.command(name="voicetest")
async def voice_test(ctx: commands.Context):
    """Play ~15s of test music in your current voice channel via the DAVE sidecar."""
    if not getattr(ctx.author, "voice", None) or not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("Join a voice channel first, then run `!voicetest`.")
        return

    voice_channel = ctx.author.voice.channel
    if voice_channel.id in music_player.active_players:
        await music_player.stop_music_in_channel(voice_channel.id)

    await ctx.send("🔊 Running a 15-second voice test through the sidecar...")
    ok = await music_player.play_music_in_channel(
        voice_channel, "cc_menu", bot=bot, volume=60)
    if not ok:
        await ctx.send(
            "Voice test failed to start. Check `voice-service/voice-service.log` "
            "(is the sidecar running and installed?)."
        )
        return

    async def _auto_stop():
        await asyncio.sleep(15)
        await music_player.stop_music_in_channel(voice_channel.id)

    asyncio.create_task(_auto_stop())


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


@bot.command(name="dnr")
async def dnr(ctx: commands.Context, setting: str = "on", *, character_name: str = None):
    """Set your character's Do-Not-Resuscitate wish. Usage: !dnr on|off [character]"""
    on = str(setting).strip().lower() not in ("off", "false", "no", "0", "clear", "lift")
    user_id = str(ctx.author.id)
    chosen, characters = await backend_integration.resolve_character(
        user_id, CHECK_CHARACTER_URL, character_name)
    if not chosen:
        await ctx.send("📜 I couldn't find that character. Use `!sheet` to see yours.")
        return
    res = await backend_integration.set_character_dnr(chosen["id"], on, BACKEND_URL)
    if res.get("error"):
        await ctx.send(f"⚠️ {res['error']}")
    elif on:
        await ctx.send(f"🕯️ **{chosen['name']}** now bears a Do-Not-Resuscitate wish — "
                       f"they do not wish to be called back from death. Revivers, beware.")
    else:
        await ctx.send(f"🕯️ **{chosen['name']}**'s Do-Not-Resuscitate wish is lifted.")


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
    # Start the DAVE-capable Node voice sidecar (replaces Lavalink).
    if music_player.start_voice_service(TOKEN):
        import time
        print("[Bot] Waiting for voice sidecar to initialize...")
        time.sleep(5)

    try:
        # Run the bot
        bot.run(TOKEN)
    except KeyboardInterrupt:
        print("\n[Bot] Shutting down...")
    finally:
        # Ensure the voice sidecar stops when the bot exits
        music_player.stop_voice_service()
