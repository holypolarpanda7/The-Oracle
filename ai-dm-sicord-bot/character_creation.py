"""
Character Creation Module - Handles all character creation logic.
Includes session management, Avrae imports, and AI-guided creation.
"""
import asyncio
import re
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


class CCControlsView(discord.ui.View):
    """Session controls that follow the whole CC conversation.

    Attached to every Oracle message during guided creation so the player can
    toggle music, switch to a D&D Beyond import, or cancel at any point
    without scrolling back to the welcome message.
    """

    def __init__(self, voice_channel_id: int, user_id: str):
        super().__init__(timeout=3600)
        self.voice_channel_id = voice_channel_id
        self.user_id = user_id

    async def _owner_only(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "This isn't your character creation!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Music", style=discord.ButtonStyle.secondary, emoji="🎵")
    async def music_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_only(interaction):
            return
        await interaction.response.defer()
        import music_control
        changed = await music_control.toggle_music(self.voice_channel_id, interaction.client)
        now_on = music_control.music_preferences.get(
            self.voice_channel_id, {}).get("enabled", False)
        if changed:
            note = "🔊 Music is back on." if now_on else "🔇 Music is off."
        else:
            note = "🔊 Music is already on." if now_on else "🔇 Music is already off."
        await interaction.followup.send(note, ephemeral=True)

    @discord.ui.button(label="Use D&D Beyond sheet", style=discord.ButtonStyle.secondary, emoji="📥")
    async def ddb_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_only(interaction):
            return
        await interaction.response.send_message(
            "📥 Paste your **public** D&D Beyond character link right here and "
            "I'll import it — you can do this at any point in the conversation.",
            ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._owner_only(interaction):
            return
        await interaction.response.defer()
        try:
            await interaction.followup.send("❌ Character creation session cancelled.")
        except Exception:
            pass  # Channel may already be gone
        await cleanup_ephemeral_channel(
            interaction.guild, self.voice_channel_id, self.user_id,
            reason="Cancelled by user")


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
            # Local LLMs can legitimately take >30s on long CC prompts. If the
            # bot gives up early the backend still finishes and records the
            # reply in session history — the player just never sees it, and the
            # next turn confusingly "remembers" things they weren't shown. So
            # wait as long as the backend's own LLM timeout allows.
            async with session.post(backend_url, json=payload, timeout=aiohttp.ClientTimeout(total=120)) as resp:
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
        "Respond in English ONLY — never switch to any other language mid-reply. "
        "First, greet them and ask their character's name."
    )
    
    guidance = await get_dm_guidance(session_id, username, opening_prompt, backend_url)
    await text_channel.send(
        f"🎭 The Oracle begins: {guidance}",
        view=CCControlsView(voice_channel_id, user_id))

    guided_cc_state[text_channel.id]["waiting_for_input"] = True


def _build_cc_progress_embed(char_data: Dict) -> discord.Embed:
    """The live 'sheet so far' panel shown/updated after every CC exchange."""
    def val(v):  # noqa: E731-ish helper
        return str(v) if v else "❓ —"
    embed = discord.Embed(
        title=f"📜 Character sheet — {char_data.get('name') or 'unnamed'}",
        description="Taking shape as you speak...",
        color=discord.Color.gold(),
    )
    embed.add_field(name="Race", value=val(char_data.get("race")), inline=True)
    embed.add_field(name="Class", value=val(char_data.get("char_class")), inline=True)
    embed.add_field(name="Background", value=val(char_data.get("background")), inline=True)
    stats = char_data.get("stats") or {}
    if stats:
        embed.add_field(
            name="Abilities",
            value=" · ".join(f"{k[:3].upper()} {v}" for k, v in stats.items()),
            inline=False)
    else:
        embed.add_field(name="Abilities", value="❓ not yet rolled/assigned", inline=False)
    todo = [f for f, v in (("name", char_data.get("name")),
                           ("race", char_data.get("race")),
                           ("class", char_data.get("char_class")),
                           ("abilities", stats)) if not v]
    embed.set_footer(text=("Still needed: " + ", ".join(todo)) if todo
                     else "Complete! The Oracle will finalize your character.")
    return embed


async def _update_cc_progress(channel: discord.TextChannel, state: Dict) -> None:
    """Edit the pinned progress sheet in place (or post it the first time)."""
    embed = _build_cc_progress_embed(state["char_data"])
    try:
        msg_id = state.get("sheet_message_id")
        if msg_id:
            msg = await channel.fetch_message(msg_id)
            await msg.edit(embed=embed)
            return
    except (discord.NotFound, discord.HTTPException):
        pass
    try:
        msg = await channel.send(embed=embed)
        state["sheet_message_id"] = msg.id
    except discord.HTTPException as e:
        print(f"[cc sheet panel error] {e}")


async def _show_final_sheet(channel: discord.TextChannel, character_id: int,
                            backend_url: str) -> None:
    """Reflect the fully rendered sheet + starting inventory back to the player."""
    import backend_integration
    import character_display
    try:
        sheet = await backend_integration.get_character_sheet(character_id, backend_url)
        if sheet:
            embed, file = character_display.build_sheet_embed(sheet)
            await channel.send(embed=embed, file=file) if file else \
                await channel.send(embed=embed)
        inv = await backend_integration.get_inventory(character_id, backend_url)
        if inv and inv.get("items"):
            await channel.send(embed=character_display.build_inventory_embed(inv))
    except Exception as e:
        print(f"[cc final sheet error] {e}")


async def _handle_ddb_import(channel: discord.TextChannel, message: discord.Message,
                             state: Dict, url_text: str, backend_url: str) -> None:
    """Import a D&D Beyond sheet mid-CC: validate, report, reflect, follow up."""
    import backend_integration
    await channel.send("🔮 The Oracle peers into D&D Beyond...")
    result = await backend_integration.import_ddb_character(
        str(message.author.id), url_text, backend_url)
    if result.get("status") != "ok":
        await channel.send(
            f"❌ Import failed: {result.get('error', 'unknown error')}\n"
            "Make sure the character is set to **Public** on D&D Beyond, "
            "then paste the link again — or we can keep building here.")
        return

    name = result.get("name", "your character")
    report = result.get("report") or {}
    lines = [f"✅ **{name}** imported from D&D Beyond!"]
    if report.get("dropped"):
        lines.append("**Set aside by the world's rules:**\n" +
                     "\n".join(f"• {d}" for d in report["dropped"]))
    if report.get("warnings"):
        lines.append("**Notes:**\n" + "\n".join(f"• {w}" for w in report["warnings"]))
    await channel.send("\n\n".join(lines)[:1900])

    # The rendered sheet + starting inventory, reflected back.
    await _show_final_sheet(channel, result["character_id"], backend_url)

    # Let the AI DM follow up on gaps and acknowledge what was dropped.
    followup = (
        f"SYSTEM NOTE: the player just imported '{name}' from D&D Beyond. "
        f"Validation report — missing: {report.get('missing') or 'nothing'}; "
        f"dropped: {report.get('dropped') or 'nothing'}; "
        f"notes: {report.get('warnings') or 'none'}. "
        "In character as the Oracle: welcome the character warmly by name. "
        "If anything is missing, ask about it one question at a time. If things "
        "were dropped, briefly explain why (this world starts every tale at "
        "level 1, magic must be earned). Then tell them they're ready for "
        "!enterworld."
    )
    guidance = await get_dm_guidance(state["session_id"], state["username"],
                                     followup, backend_url)
    await channel.send(f"🎭 The Oracle: {guidance}")

    await _offer_portrait_setup(channel, message.author, result, name, backend_url)
    guided_cc_state.pop(channel.id, None)


async def process_guided_cc_input(channel: discord.TextChannel, message: discord.Message, backend_url: str):
    """Process player input during guided character creation."""
    if channel.id not in guided_cc_state:
        return

    state = guided_cc_state[channel.id]
    if not state.get("waiting_for_input"):
        return  # Opening LLM call still in flight; ignore early messages

    user_text = message.content.strip()

    # A D&D Beyond link anywhere in the message switches to the import path.
    if "dndbeyond.com/characters" in user_text.lower() or "ddb.ac/characters" in user_text.lower():
        await _handle_ddb_import(channel, message, state, user_text, backend_url)
        return

    # Get next guidance from the DM. Session controls ride every message so
    # music/cancel/DDB-import are always one click away.
    guidance = await get_dm_guidance(state["session_id"], state["username"], user_text, backend_url)
    await channel.send(
        f"🎭 The Oracle: {guidance}",
        view=CCControlsView(state["voice_channel_id"], state["user_id"]))

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

    # Try to extract background
    backgrounds = ["acolyte", "charlatan", "criminal", "entertainer", "folk hero",
                   "guild artisan", "hermit", "noble", "outlander", "sage",
                   "sailor", "soldier", "urchin"]
    for bg in backgrounds:
        if bg in lower_text:
            state["char_data"]["background"] = bg.title()
            break

    # Try to extract ability scores ("str 15", "dexterity 14", "con: 13" ...)
    _ABILS = {"str": "strength", "dex": "dexterity", "con": "constitution",
              "int": "intelligence", "wis": "wisdom", "cha": "charisma"}
    for m in re.finditer(
            r"\b(str(?:ength)?|dex(?:terity)?|con(?:stitution)?|int(?:elligence)?"
            r"|wis(?:dom)?|cha(?:risma)?)\s*[:=]?\s*(\d{1,2})\b", lower_text):
        ability = _ABILS[m.group(1)[:3]]
        score = int(m.group(2))
        # 3-20: final scores include racial bonuses (e.g. rolled 18 + 2 racial).
        if 3 <= score <= 20:
            state["char_data"].setdefault("stats", {})[ability] = score

    # Keep the live sheet panel in step with the conversation.
    await _update_cc_progress(channel, state)


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
        "background": char_data.get("background"),
        "ddb_url": None,
        "avrae_import_text": None,
        "approve": True,
        "home_region": "Gatvorhain",
    }
    
    result = await register_character_backend(payload, backend_url)
    if _registration_succeeded(result):
        await channel.send(f"✅ Character **{char_data['name']}** created and approved! You can now enter the world with `!enterworld`.")

        # Reflect the finished sheet + starting kit inventory back to the player.
        if result.get("character_id"):
            await _show_final_sheet(channel, result["character_id"], backend_url)

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
