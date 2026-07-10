"""
Event Handlers Module - Discord event handlers (on_ready, on_message, on_reaction_add, etc.)
"""
import base64
import binascii
import io
import re

import discord
from discord.ext import commands
import wavelink

import music_player
import music_control
import character_creation
import backend_integration
import dm_commands
import character_display


async def on_ready_handler(bot):
    """Called when bot connects to Discord."""
    print(f"Bot is online as {bot.user} (ID: {bot.user.id})")
    
    # Connect to Lavalink via music_player module
    await music_player.setup_lavalink(bot)
    
    print("Ready to DM!")


async def on_wavelink_node_ready_handler(payload: wavelink.NodeReadyEventPayload):
    """Called when Lavalink node is ready."""
    await music_player.on_wavelink_node_ready(payload)


async def on_wavelink_track_start_handler(payload: wavelink.TrackStartEventPayload):
    """Called when a track starts playing."""
    await music_player.on_wavelink_track_start(payload)


async def on_wavelink_track_end_handler(payload: wavelink.TrackEndEventPayload):
    """Called when a track ends - play next track in queue."""
    await music_player.on_wavelink_track_end(payload)


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

    # ❌ reaction = create from scratch (AI-guided)
    elif str(reaction.emoji) == "❌":
        await reaction.message.channel.send("🎨 No problem! Let's build your character together. I'll ask you some questions.\n\nWhat's your character's name?")
        # Start guided character creation
        backend_url = bot.backend_url
        await character_creation.start_guided_character_creation(reaction.message.channel, cc_voice_id, cc_data["user_id"], user.display_name, backend_url)

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


async def on_voice_state_update_handler(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState, bot):
    """Handle player joining character creation voice channels."""
    # Ignore bot's own voice state changes
    if member.bot:
        return

    # Check if the member joined a voice channel
    if after.channel is None:
        return  # Member left or was moved, ignore

    # Check if this voice channel is tracked as ephemeral CC session
    if after.channel.id not in character_creation.ephemeral_cc_channels:
        return

    session_data = character_creation.ephemeral_cc_channels[after.channel.id]

    # Verify it's the owner joining
    if str(member.id) != session_data["user_id"]:
        return

    print(f"[on_voice_state_update] {member.display_name} joined character creation voice channel: {after.channel.name}")

    # Check if text channel already exists
    text_channel_id = session_data.get("text_channel_id")
    text_channel = None

    if text_channel_id:
        text_channel = after.channel.guild.get_channel(text_channel_id)

    if not text_channel:
        # Create a linked text channel
        overwrites = {
            after.channel.guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
            member: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True),
            bot.user: discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True),
        }

        # Add Avrae permissions
        avrae_member = discord.utils.find(lambda m: m.name.lower() == "avrae", after.channel.guild.members)
        if avrae_member:
            overwrites[avrae_member] = discord.PermissionOverwrite(view_channel=True, read_messages=True, send_messages=True)

        # Find category
        category = discord.utils.find(
            lambda c: isinstance(c, discord.CategoryChannel) and c.name == "Character Creation",
            after.channel.guild.channels
        )

        text_channel_name = after.channel.name.replace("cc-", "cc-chat-")

        try:
            text_channel = await after.channel.guild.create_text_channel(
                text_channel_name,
                category=category,
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
    
    try:
        # Send character creation instructions to the text channel
        print(f"[on_voice_state_update] Sending instructions to text channel: {text_channel.name}")
        
        # Start music in the voice channel (if enabled)
        if after.channel.id in music_control.music_preferences and music_control.music_preferences[after.channel.id]["enabled"]:
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
        character_creation.ephemeral_cc_channels[after.channel.id]["text_channel_id"] = text_channel.id
        
        print(f"[on_voice_state_update] Successfully posted instructions to {text_channel.name}")
        
    except discord.Forbidden as e:
        print(f"[on_voice_state_update Forbidden error] {e} (likely text channel permission issue)")
    except Exception as e:
        print(f"[on_voice_state_update error] {e}")


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


async def on_message_handler(message: discord.Message, bot, active_dm_channels: set):
    """Handle incoming messages."""
    # Ignore messages from bots (including ourselves)
    if message.author.bot:
        return

    # Process commands first
    await bot.process_commands(message)

    # Check if message is in the entry channel
    if message.channel.name == bot.entry_channel_name:
        # Check if player has characters in DB
        has_char, characters = await backend_integration.check_character_in_db(str(message.author.id), bot.check_character_url)
        
        # Create button view
        view = discord.ui.View(timeout=300)
        
        if has_char:
            # Show existing characters as buttons + create new option
            embed = discord.Embed(
                title="🌍 Welcome to the World of Gatvorhain!",
                description="Select a character to enter the world, or create a new one.",
                color=discord.Color.gold()
            )
            
            for char in characters:
                char_label = f"{char['name']} (Lvl {char['level']} {char['char_class'] or 'Adventurer'})"
                button = discord.ui.Button(
                    label=char_label[:80],  # Discord button label limit
                    style=discord.ButtonStyle.primary,
                    custom_id=f"select_char_{char['id']}"
                )
                
                async def char_callback(interaction: discord.Interaction, char_id=char['id'], char_name=char['name']):
                    if interaction.user.id != message.author.id:
                        await interaction.response.send_message("This isn't your character selection!", ephemeral=True)
                        return
                    await interaction.response.send_message(f"✨ Loading {char_name}... Use `!enterworld` to begin your adventure!", ephemeral=True)
                    # TODO: Store selected character for enterworld command
                
                button.callback = char_callback
                view.add_item(button)
            
            # Add "Create New Character" button
            create_btn = discord.ui.Button(
                label="✨ Create New Character",
                style=discord.ButtonStyle.success,
                custom_id="create_new_char"
            )
            
            async def create_callback(interaction: discord.Interaction):
                if interaction.user.id != message.author.id:
                    await interaction.response.send_message("This isn't your character creation!", ephemeral=True)
                    return
                await interaction.response.send_message("Starting character creation...", ephemeral=True)
                await character_creation.create_character_creation_session(message, bot)
            
            create_btn.callback = create_callback
            view.add_item(create_btn)
        else:
            # No characters - only show create button
            embed = discord.Embed(
                title="🌟 Welcome, New Adventurer!",
                description="You don't have any characters yet. Let's create your first one!",
                color=discord.Color.green()
            )
            
            create_btn = discord.ui.Button(
                label="✨ Create Your Character",
                style=discord.ButtonStyle.success,
                custom_id="create_first_char"
            )
            
            async def create_first_callback(interaction: discord.Interaction):
                if interaction.user.id != message.author.id:
                    await interaction.response.send_message("This isn't your character creation!", ephemeral=True)
                    return
                await interaction.response.send_message("Starting character creation...", ephemeral=True)
                await character_creation.create_character_creation_session(message, bot)
            
            create_btn.callback = create_first_callback
            view.add_item(create_btn)
        
        # Add tavern info footer
        embed.add_field(
            name="💬 Just want to chat?",
            value="Head over to the **🌌tavern-between-worlds** channel to mingle with other adventurers out-of-character!",
            inline=False
        )
        
        await message.reply(embed=embed, view=view)
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

        # Check if we're in guided character creation mode
        if message.channel.id in character_creation.guided_cc_state:
            await character_creation.process_guided_cc_input(message.channel, message, bot.backend_url)
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
    
    result = await backend_integration.call_backend(user_text, session_id, user_id, username, bot.backend_url)
    dm_reply = result.get("reply", "The Oracle is silent...")
    music_query = result.get("music")

    # Decode any scene pictures the backend produced into Discord attachments.
    files = _build_scene_files(result.get("images"))
    await message.channel.send(dm_reply, files=files if files else None)

    # If the DM recommended scene music and the player is in a voice channel,
    # play a matching ambient track there (looped until the scene changes).
    if music_query:
        voice_state = getattr(message.author, "voice", None)
        voice_channel = voice_state.channel if voice_state else None
        if voice_channel is not None:
            try:
                await music_player.play_query_in_channel(voice_channel, music_query)
            except Exception as e:
                print(f"[music] Failed to play scene music '{music_query}': {e}")
