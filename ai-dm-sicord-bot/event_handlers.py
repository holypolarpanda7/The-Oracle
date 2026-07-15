"""
Event Handlers Module - Discord event handlers (on_ready, on_message, on_reaction_add, etc.)
"""
import asyncio
import base64
import binascii
import io
import os
import re
import urllib.parse
from datetime import datetime, timezone

import discord
from discord.ext import commands
from discord.ui import View, Button, button

import music_player
import music_control
import character_creation
import backend_integration
import dm_commands
import character_display


class CharacterCreationView(View):
    """Button view for character creation options."""
    
    def __init__(self, cc_voice_id: int, cc_data: dict, bot):
        super().__init__()
        self.cc_voice_id = cc_voice_id
        self.cc_data = cc_data
        self.bot = bot
        self.timeout = 3600  # 1 hour timeout
    
    @button(label="Import from D&D Beyond", style=discord.ButtonStyle.success, emoji="📥", custom_id="cc_import")
    async def import_button(self, interaction: discord.Interaction, button: Button):
        """Handle import from D&D Beyond."""
        await interaction.response.defer()
        await interaction.followup.send("Waiting for your Avrae import... Type `!import [D&D Beyond link]`")

    @button(label="Create Character", style=discord.ButtonStyle.secondary, emoji="⚒️", custom_id="cc_guided")
    async def guided_button(self, interaction: discord.Interaction, button: Button):
        """Deterministic character creation: menus + real dice, no LLM rules."""
        import cc_wizard
        await interaction.response.send_modal(cc_wizard.NameModal(
            self.cc_voice_id,
            self.cc_data["user_id"],
            interaction.user.display_name,
            self.bot.backend_url,
        ))

    @button(label="Music", style=discord.ButtonStyle.secondary, emoji="🎵", custom_id="cc_music")
    async def music_button(self, interaction: discord.Interaction, button: Button):
        """Toggle background music in the linked voice channel."""
        await interaction.response.defer()
        changed = await music_control.toggle_music(self.cc_voice_id, self.bot)
        now_on = music_control.music_preferences.get(
            self.cc_voice_id, {}).get("enabled", False)
        if changed:
            note = "🔊 Music is back on." if now_on else "🔇 Music is off."
        else:
            note = "🔊 Music is already on." if now_on else "🔇 Music is already off."
        await interaction.followup.send(note, ephemeral=True)

    @button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌", custom_id="cc_cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: Button):
        """Cancel character creation session."""
        await interaction.response.defer()
        voice_channel_id = self.cc_voice_id
        guild = interaction.guild
        user_id = self.cc_data["user_id"]
        
        # Send cancel message BEFORE cleanup
        try:
            await interaction.followup.send("❌ Character creation session cancelled.")
        except Exception:
            pass  # Channel may be deleted, ignore
        
        # Then clean up the session
        await character_creation.cleanup_ephemeral_channel(
            guild, 
            voice_channel_id, 
            user_id, 
            reason="Cancelled by user"
        )



async def on_ready_handler(bot):
    """Called when bot connects to Discord."""
    print(f"Bot is online as {bot.user} (ID: {bot.user.id})")

    # Connect to the DAVE-capable voice sidecar (replaces Lavalink).
    voice_ok = await music_player.setup_voice_service(bot)
    if not voice_ok:
        print("[on_ready] Voice service not ready; music retries will occur on demand.")

    print("Ready to DM!")


async def on_reaction_add_handler(reaction: discord.Reaction, user: discord.User, bot):
    """Handle reactions on character creation instructions."""
    # Ignore bot's own reactions
    if user.bot:
        return
    
    # Find the ephemeral CC session matching this text channel
    cc_voice_id = None
    cc_data = None
    for v_id, data in character_creation.ephemeral_cc_channels.items():
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

    # ❌ reaction (legacy messages): creation is button-driven now.
    elif str(reaction.emoji) == "❌":
        await reaction.message.channel.send(
            "⚒️ Press the **Create Character** button to begin.")

    # 🔇 reaction = turn music OFF
    elif str(reaction.emoji) == "🔇":
        if await music_control.toggle_music(cc_voice_id, bot):
            await reaction.message.channel.send("🔇 Music has been turned off.")
        else:
            await reaction.message.channel.send("🔇 Music is already off.")

    # 🔊 reaction = turn music ON
    elif str(reaction.emoji) == "🔊":
        if await music_control.toggle_music(cc_voice_id, bot):
            await reaction.message.channel.send("🔊 Music has been turned back on.")
        else:
            await reaction.message.channel.send("🔊 Music is already on.")


# Natural-language triggers for showing the structured character sheet / inventory
# instead of routing the message to the DM. Kept tight to avoid hijacking roleplay.
_SHEET_PATTERNS = [
    r"\b(show|view|see|open|display|pull up|check)\b.{0,20}\b(character\s*sheet|char\s*sheet|my\s*sheet|my\s*stats|my\s*character)\b",
    r"\bcharacter\s*sheet\b",
]
_INVENTORY_PATTERNS = [
    r"\b(show|view|see|open|display|check|list)\b.{0,20}\b(inventory|my\s*(items|gear|pack|bag|backpack|belongings|equipment))\b",
    r"\b(what('?s| is| do i have)|whats)\b.{0,25}\b(inventory|carrying|in my (pack|bag|backpack|pockets)|on me)\b",
    r"\b(my\s*inventory)\b",
]


def _matches_any(text: str, patterns) -> bool:
    return any(re.search(p, text) for p in patterns)


def _is_cc_cancel_request(text: str) -> bool:
    """True when a player asks in plain language to cancel character creation."""
    t = (text or "").strip().lower()
    if not t:
        return False
    # Explicit phrases first (high confidence). Intentionally avoid ambiguous
    # "done" wording so completion chatter doesn't accidentally cancel.
    phrases = (
        "cancel",
        "cancel creation",
        "cancel character creation",
        "stop creation",
        "stop character creation",
        "end creation",
        "abort creation",
        "cancel cc",
        "stop cc",
        "abort cc",
        "i want to cancel",
        "i want to stop",
        "not right now",
        "let's do this later",
        "lets do this later",
        "do this later",
        "come back later",
        "cancel for now",
        "stop for now",
        "never mind",
        "nevermind",
        "quit creation",
    )
    if any(p in t for p in phrases):
        return True

    # Flexible intent match: a cancel-ish verb plus character-creation context.
    # Example: "can we pause character creation" or "end cc for now".
    intent_words = ("cancel", "abort", "quit", "stop", "end", "pause")
    cc_words = ("character creation", "creation", "cc", "import")
    has_intent = any(w in t for w in intent_words)
    has_cc_context = any(w in t for w in cc_words)
    if has_intent and has_cc_context:
        return True

    return False


async def _maybe_handle_character_query(message, bot) -> bool:
    """If the player asked to see their sheet or inventory, render it and return True.

    Returns False when the message isn't a structured character query so normal DM
    handling proceeds.
    """
    text = message.content.strip().lower()
    if len(text) > 120:   # long free-form roleplay is never a UI request
        return False

    want_sheet = _matches_any(text, _SHEET_PATTERNS)
    want_inv = _matches_any(text, _INVENTORY_PATTERNS)
    if not (want_sheet or want_inv):
        return False

    user_id = str(message.author.id)
    chosen, _ = await backend_integration.resolve_character(
        user_id, bot.check_character_url, None)
    if not chosen:
        return False   # no character -> let the DM respond naturally

    if want_sheet:
        data = await backend_integration.get_character_sheet(chosen["id"], bot.backend_url)
        if not data:
            return False
        embed, portrait_file = character_display.build_sheet_embed(data)
        if portrait_file:
            await message.channel.send(embed=embed, file=portrait_file)
        else:
            await message.channel.send(embed=embed)
        return True

    # inventory
    data = await backend_integration.get_inventory(chosen["id"], bot.backend_url)
    if not data:
        return False
    await message.channel.send(embed=character_display.build_inventory_embed(data))
    return True


# Channel that receives fallen characters' memorials (the hall of the dead).
MEMORIAL_CHANNEL_ID = int(os.getenv("MEMORIAL_CHANNEL_ID", "1447789198039060600"))


async def post_memorial(bot, memorial) -> None:
    """Post a fallen PC's one-page life record to the memorial channel.

    Best-effort: a missing channel or permissions problem is logged, never
    raised — the death is already canon in the world graph regardless.
    """
    if not memorial or not memorial.get("text"):
        return
    try:
        channel = bot.get_channel(MEMORIAL_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(MEMORIAL_CHANNEL_ID)
        embed = discord.Embed(
            title=memorial.get("title", "⚰️ In Memoriam"),
            description=memorial.get("text", "")[:4000],
            color=discord.Color.dark_grey(),
        )
        embed.set_footer(text="Their tale is told. The world remembers.")
        await channel.send(embed=embed)
        print(f"[memorial] posted for {memorial.get('character', '?')} "
              f"to #{getattr(channel, 'name', MEMORIAL_CHANNEL_ID)}")
    except Exception as e:
        print(f"[memorial] failed to post: {e}")


async def send_paced(channel, text: str, files=None) -> None:
    """Deliver a long narration paragraph-by-paragraph with the typing
    indicator between beats — the Discord approximation of streaming text.

    Short replies go out in one message; attachments ride the final part.
    """
    text = (text or "").strip()
    parts = [p for p in text.split("\n\n") if p.strip()]
    if len(parts) <= 1 or len(text) < 700:
        await channel.send(text or "...", files=files if files else None)
        return
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        try:
            await channel.send(part, files=files if (files and last) else None)
        except discord.HTTPException as e:
            print(f"[paced send error] {e}")
            return
        if not last:
            # Pause scaled to the next paragraph's length, like a storyteller
            # drawing breath (capped so pacing never drags).
            async with channel.typing():
                await asyncio.sleep(min(2.5, 0.5 + len(parts[i + 1]) / 400))


def _build_scene_files(images) -> list:
    """Turn backend image payloads (base64 WebP) into discord.File attachments."""
    files = []
    if not images:
        return files
    for idx, img in enumerate(images):
        b64 = (img or {}).get("b64")
        if not b64:
            continue
        try:
            data = base64.b64decode(b64)
        except (binascii.Error, ValueError) as e:
            print(f"[imagery] bad base64 image payload: {e}")
            continue
        caption = (img.get("caption") or "scene")
        safe = "".join(c for c in caption if c.isalnum() or c in ("_", "-", " ")).strip()
        safe = safe.replace(" ", "_")[:60] or "scene"
        files.append(discord.File(io.BytesIO(data), filename=f"{safe}_{idx}.webp"))
        # Discord allows up to 10 attachments per message.
        if len(files) >= 10:
            break
    return files


class ActivityLaunchView(View):
    """Entry-channel launcher for the web Activity (the new play surface).

    Embedded Discord Activities always run *inside a voice call*, so there's no
    way to open the Activities tray straight from a text channel. Instead this
    button mints an embedded-application invite for the player's current voice
    channel; clicking that invite launches The Oracle in that call. Character
    creation, resume, and play all happen in the web UI — the old component
    wizard and per-character session channels are no longer routed to."""

    def __init__(self, bot):
        super().__init__(timeout=None)
        self.bot = bot

    @button(label="Enter the Oracle", style=discord.ButtonStyle.primary,
            emoji="🔮", custom_id="oracle_activity_launch")
    async def enter(self, interaction: discord.Interaction, _btn: Button):
        client_id = os.getenv("ORACLE_DM_CLIENT_ID", "").strip()
        if not client_id:
            await interaction.response.send_message(
                "⚠️ The Activity isn't configured yet (missing `ORACLE_DM_CLIENT_ID`).",
                ephemeral=True)
            return

        voice = getattr(interaction.user, "voice", None)
        if not voice or not voice.channel:
            await interaction.response.send_message(
                "🔊 Join a **voice channel** first, then press **Enter the Oracle** again — "
                "the Activity opens inside your voice call.",
                ephemeral=True)
            return

        try:
            invite = await voice.channel.create_invite(
                target_type=discord.InviteTarget.embedded_application,
                target_application_id=int(client_id),
                max_age=86400, unique=True,
                reason="Launch The Oracle Activity")
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I need **Create Invite** permission on that voice channel.",
                ephemeral=True)
            return
        except Exception as e:
            print(f"[activity launch] invite failed: {e}")
            await interaction.response.send_message(
                "❌ Couldn't open the Activity — check my permissions and try again.",
                ephemeral=True)
            return

        launch = View()
        launch.add_item(Button(label="Launch The Oracle",
                               style=discord.ButtonStyle.link,
                               url=invite.url, emoji="🔮"))
        await interaction.response.send_message(
            f"🔮 Your table awaits in **{voice.channel.name}** — click to open The Oracle:",
            view=launch, ephemeral=True)


async def on_message_handler(message: discord.Message, bot, active_dm_channels: set):
    """Handle incoming messages."""
    # Ignore messages from bots (including ourselves)
    if message.author.bot:
        return

    # Process commands first
    await bot.process_commands(message)

    # The remaining routing is guild-specific. DMs can still invoke commands,
    # but should not be treated as entry/session/world messages.
    if message.guild is None:
        return

    # Check if message is in the entry channel
    entry_id = getattr(bot, "entry_channel_id", None)
    is_entry = (getattr(message.channel, "name", None) == bot.entry_channel_name
                or (entry_id and message.channel.id == int(entry_id)))
    if is_entry:
        # The web Activity is now the single entry point: character creation,
        # resume, and play all live there. We no longer post per-character
        # buttons or the old component-wizard "Create" flow here.
        embed = discord.Embed(
            title="🌍 The World of Gatvorhain",
            description=(
                "Step into the living world. Forge a new character or resume your "
                "tale — all inside **The Oracle**."),
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="🔊 One step first",
            value="Join a **voice channel**, then press **Enter the Oracle** below.",
            inline=False,
        )
        embed.add_field(
            name="💬 Just want to chat?",
            value=("Head to **🌌tavern-between-worlds** to mingle with other "
                   "adventurers out-of-character!"),
            inline=False,
        )
        await message.reply(embed=embed, view=ActivityLaunchView(bot))
        return

    # Check if this is in an ephemeral CC text channel
    session_voice_id = None
    session_data = None
    for voice_id, data in character_creation.ephemeral_cc_channels.items():
        if data.get("text_channel_id") == message.channel.id:
            session_voice_id = voice_id
            session_data = data
            break

    if session_data:
        # Update last_message_at timestamp
        from datetime import datetime, timezone
        character_creation.ephemeral_cc_channels[session_voice_id]["last_message_at"] = datetime.now(timezone.utc)

        owner_id = str(session_data.get("user_id"))
        is_owner = str(message.author.id) == owner_id

        # Conversational cancel in CC chat (owner only):
        # "I want to cancel creation", "never mind", etc.
        if is_owner and _is_cc_cancel_request(message.content):
            await message.channel.send("🛑 Understood. Ending character creation now and closing this session.")
            await character_creation.cleanup_ephemeral_channel(
                message.guild,
                session_voice_id,
                owner_id,
                reason="Cancelled by player request",
            )
            return

        # Deterministic wizard sessions: components do the work; typed text is
        # either a D&D Beyond link (import) or gets a gentle nudge.
        import cc_wizard
        if await cc_wizard.handle_wizard_message(message.channel, message, bot.backend_url):
            return

        # Check if we're in guided character creation mode (legacy LLM flow —
        # only reachable for sessions started before the wizard existed).
        if message.channel.id in character_creation.guided_cc_state:
            await character_creation.process_guided_cc_input(message.channel, message, bot.backend_url)
            return

        # Owner chatting in the CC channel before starting: point at the buttons
        # (creation is deterministic now — the Oracle narrates, it doesn't adjudicate).
        if is_owner and not message.content.strip().startswith("!"):
            await message.channel.send(
                "⚒️ Press **Create Character** above to begin — every choice is a "
                "click, and all dice are real. Or paste a D&D Beyond link to import.")
            return

        # Check if this is Avrae posting a character import
        if message.author.name.lower() == "avrae" and (message.embeds or "imported" in message.content.lower()):
            user_id = str(session_data["user_id"])
            char_data = await character_creation.extract_character_from_avrae_embed(message)

            if char_data:
                success = await character_creation.validate_and_register_character(message, char_data, user_id, bot.character_creation_url)
                if success:
                    # Schedule cleanup to delete the voice channel after a short delay
                    import asyncio
                    await asyncio.sleep(2)
                    await character_creation.cleanup_ephemeral_channel(message.guild, session_voice_id, user_id, reason="Character successfully created")
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

    # Short-circuit structured requests ("show my character sheet", "what's in my
    # inventory?") to a rendered display instead of a DM narration.
    try:
        if await _maybe_handle_character_query(message, bot):
            return
    except Exception as e:
        print(f"[character query error] {e}")

    # Call the backend for the DM reply
    session_id = f"dm:{message.channel.id}"
    user_id = str(message.author.id)
    username = message.author.display_name
    
    # Typing indicator while the Oracle thinks (local 14B narration takes a
    # moment) — the waiting feels alive instead of dead air.
    async with message.channel.typing():
        result = await backend_integration.call_backend(user_text, session_id, user_id, username, bot.backend_url)
    dm_reply = result.get("reply", "The Oracle is silent...")
    music_query = result.get("music")

    # Decode any scene pictures the backend produced into Discord attachments.
    files = _build_scene_files(result.get("images"))
    await send_paced(message.channel, dm_reply, files=files)

    # A fallen character's life record goes to the memorial channel.
    if result.get("memorial"):
        await post_memorial(bot, result["memorial"])

    # If the DM recommended scene music and the player is in a voice channel,
    # play a matching ambient track there (looped until the scene changes).
    if music_query:
        voice_state = getattr(message.author, "voice", None)
        voice_channel = voice_state.channel if voice_state else None
        if voice_channel is not None:
            try:
                await music_player.play_query_in_channel(voice_channel, music_query, bot=bot)
            except Exception as e:
                print(f"[music] Failed to play scene music '{music_query}': {e}")
