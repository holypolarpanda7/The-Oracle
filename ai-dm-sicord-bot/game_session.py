"""
Game Session Module — character "login" + live play sessions.

When a player picks one of THEIR OWN characters in the entry channel they "log
in" as that character: an ephemeral voice channel + a linked text channel are
created (mirroring character creation), the character enters the world through
the backend (`/enterworld`), and inside the session text channel the player
speaks AS their character. Their messages are relayed through a Discord webhook
that wears the character's NAME and PORTRAIT — the Avrae/Tupperbox-style
"active character" identity takeover — and the Oracle answers in-world.

Rules enforced here:
- Only the player who OWNS a character may log in as it (the entry picker only
  ever lists the caller's own characters, and every interaction is owner-checked).
- Inside a session channel, ONLY logged-in participants are treated as in-game.
  Anyone else who posts is told, out-of-character, that they aren't part of this
  game and how to join — their message is never sent to the DM brain.
"""
import asyncio
import base64
import binascii
import io
from datetime import datetime, timezone
from typing import Dict, Optional

import discord

import backend_integration
import music_control
import music_player


# voice_channel_id -> pending login awaiting the owner to join the voice channel.
#   {owner_id, character_id, character_name, text_channel_id, guild_id, created_at}
pending_sessions: Dict[int, Dict] = {}

# text_channel_id -> active play session.
#   {voice_channel_id, text_channel_id, guild_id, owner_id, backend_session_id,
#    participants: {user_id: {character_id, character_name, webhook}},
#    created_at, last_message_at}
active_sessions: Dict[int, Dict] = {}

# voice_channel_id -> idle-cleanup task.
cleanup_tasks: Dict[int, asyncio.Task] = {}

# voice_channel_id -> "owner left the voice channel" grace-timer task. When the
# owner walks out of their session voice channel we give them 2 minutes to come
# back before the session is torn down.
departure_tasks: Dict[int, asyncio.Task] = {}

SESSION_CATEGORY = "Active Sessions"
SESSION_IDLE_TIMEOUT = 3 * 3600   # close a session after 3 hours with no play
DEPARTURE_GRACE_SECONDS = 120     # owner has 2 min to rejoin the VC before it ends
_IDLE_POLL_SECONDS = 300


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slug(text: str) -> str:
    safe = "".join(c if (c.isalnum() or c == " ") else "" for c in (text or "")).strip()
    return safe.lower().replace(" ", "-")[:24] or "hero"


def _decode_b64(b64: Optional[str]) -> Optional[bytes]:
    if not b64:
        return None
    try:
        return base64.b64decode(b64)
    except (binascii.Error, ValueError):
        return None


def _scene_files(images) -> list:
    """Turn backend image payloads (base64 WebP) into discord.File attachments."""
    files = []
    if not images:
        return files
    for idx, img in enumerate(images):
        data = _decode_b64((img or {}).get("b64"))
        if not data:
            continue
        caption = (img.get("caption") or "scene")
        safe = "".join(c for c in caption if c.isalnum() or c in ("_", "-", " ")).strip()
        safe = safe.replace(" ", "_")[:60] or "scene"
        files.append(discord.File(io.BytesIO(data), filename=f"{safe}_{idx}.webp"))
        if len(files) >= 10:
            break
    return files


def _text_for_voice(voice_channel_id: int) -> Optional[int]:
    for tid, s in active_sessions.items():
        if s.get("voice_channel_id") == voice_channel_id:
            return tid
    return None


# ---------------------------------------------------------------------------
# Login: create the ephemeral session voice channel
# ---------------------------------------------------------------------------

async def start_login_session(guild, owner, send_target, bot, character: Dict) -> None:
    """Create an ephemeral session voice channel for `character` and tell the
    owner to join it. `character` is a dict from /check_character with at least
    ``id`` and ``name``. Ownership is assumed already verified by the caller
    (the entry picker only lists the caller's own characters)."""
    user_id = str(owner.id)
    char_name = character.get("name") or "Adventurer"
    char_id = character.get("id")
    if char_id is None:
        await send_target.send("⚠️ That character can't be loaded right now.")
        return

    # Don't let the same player stack multiple live sessions.
    for s in active_sessions.values():
        if s.get("owner_id") == user_id:
            await send_target.send(
                f"⚔️ You already have an active session as "
                f"**{s['participants'].get(user_id, {}).get('character_name', 'your character')}**. "
                f"End it there before starting another."
            )
            return
    for p in pending_sessions.values():
        if p.get("owner_id") == user_id and p.get("character_id") == char_id:
            await send_target.send(
                f"⏳ Your session as **{char_name}** is already waiting — join its voice channel to begin."
            )
            return

    channel_name = f"play-{_slug(char_name)}-{user_id[-4:]}"
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        owner: discord.PermissionOverwrite(
            view_channel=True, read_messages=True, send_messages=True, connect=True, speak=True),
        bot.user: discord.PermissionOverwrite(
            view_channel=True, read_messages=True, send_messages=True, connect=True, speak=True),
    }

    category = discord.utils.find(
        lambda c: isinstance(c, discord.CategoryChannel) and c.name == SESSION_CATEGORY,
        guild.channels,
    )
    if category is None:
        try:
            category = await guild.create_category(SESSION_CATEGORY, reason="Adventure sessions")
        except discord.Forbidden:
            category = None

    try:
        voice_channel = await guild.create_voice_channel(
            channel_name, category=category, overwrites=overwrites,
            reason=f"Adventure session for {char_name}")
    except discord.Forbidden:
        await send_target.send(
            "❌ I need the **Manage Channels** permission to open a session channel.")
        return
    except Exception as e:
        print(f"[start_login_session error] {e}")
        await send_target.send("❌ Could not open a session channel. Check my permissions.")
        return

    pending_sessions[voice_channel.id] = {
        "owner_id": user_id,
        "character_id": char_id,
        "character_name": char_name,
        "text_channel_id": None,
        "guild_id": str(guild.id),
        "created_at": _now(),
    }
    music_control.music_preferences[voice_channel.id] = {
        "enabled": True,
        "current_playlist": "tavern",
    }
    cleanup_tasks[voice_channel.id] = asyncio.create_task(
        _schedule_idle_cleanup(guild, voice_channel.id, bot))

    await send_target.send(
        f"🎭 {owner.mention} You're ready to enter the world as **{char_name}**!\n"
        f"🔊 Join the voice channel **{voice_channel.name}** to begin your session."
    )


# ---------------------------------------------------------------------------
# Voice join: build the linked text channel, webhook identity, and enter world
# ---------------------------------------------------------------------------

async def on_session_voice_join(member: discord.Member, voice_channel, bot) -> bool:
    """Called when a member joins a voice channel. If it's a pending session
    voice channel owned by this member, spin up the linked session text channel
    and start play. Returns True if this was a session voice channel."""
    pending = pending_sessions.get(voice_channel.id)
    if not pending:
        return False
    if str(member.id) != pending["owner_id"]:
        return True   # someone else wandered in; ignore
    if pending.get("text_channel_id"):
        return True   # already started

    guild = voice_channel.guild
    char_name = pending["character_name"]
    char_id = pending["character_id"]

    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, read_messages=False),
        member: discord.PermissionOverwrite(
            view_channel=True, read_messages=True, send_messages=True),
        bot.user: discord.PermissionOverwrite(
            view_channel=True, read_messages=True, send_messages=True, manage_messages=True),
    }
    text_name = voice_channel.name.replace("play-", "session-", 1)
    try:
        text_channel = await guild.create_text_channel(
            text_name, category=voice_channel.category, overwrites=overwrites,
            reason=f"Session chat for {char_name}")
    except discord.Forbidden:
        print("[on_session_voice_join] missing permission to create text channel")
        return True
    except Exception as e:
        print(f"[on_session_voice_join text channel error] {e}")
        return True

    pending["text_channel_id"] = text_channel.id

    # Start ambient music in the voice channel (if enabled).
    prefs = music_control.music_preferences.get(voice_channel.id)
    if prefs and prefs.get("enabled"):
        try:
            await music_player.play_music_in_channel(voice_channel, prefs.get("current_playlist", "tavern"))
        except Exception as e:
            print(f"[session music error] {e}")

    # Build the character's "voice": a webhook wearing their name + portrait.
    webhook = await _make_character_webhook(text_channel, char_id, char_name, bot)

    # Enter the world through the backend (binds the character to the session).
    enter = await backend_integration.enter_world_session(
        pending["owner_id"], member.display_name, str(guild.id), char_name, bot.enter_url)
    backend_session_id = enter.get("session_id") or f"session:{text_channel.id}"
    intro = enter.get("intro") or "The world stirs around you as you step into it..."

    active_sessions[text_channel.id] = {
        "voice_channel_id": voice_channel.id,
        "text_channel_id": text_channel.id,
        "guild_id": str(guild.id),
        "owner_id": pending["owner_id"],
        "backend_session_id": backend_session_id,
        "participants": {
            pending["owner_id"]: {
                "character_id": char_id,
                "character_name": char_name,
                "webhook": webhook,
            }
        },
        "created_at": _now(),
        "last_message_at": _now(),
    }

    await text_channel.send(
        f"🌍 **{char_name}** steps into the world.\n\n"
        f"Speak and act as your character — everything you type here is spoken by "
        f"**{char_name}**, and I, the Oracle, will narrate what unfolds. "
        f"Only you, as **{char_name}**, may act in this session.\n"
        f"When you're done, type `!leave` to end the session."
    )
    await text_channel.send(intro)

    # Opening ambient-music cue from the DM, if the player is in the voice channel.
    music_query = enter.get("music")
    if music_query:
        try:
            await music_player.play_query_in_channel(voice_channel, music_query)
        except Exception as e:
            print(f"[session opening music error] {e}")
    return True


async def _make_character_webhook(text_channel, char_id: int, char_name: str, bot):
    """Create a webhook in the session channel that posts as the character
    (name + portrait). Returns the Webhook, or None if unavailable."""
    avatar_bytes = None
    try:
        portrait = await backend_integration.get_portrait(char_id, bot.backend_url)
        avatar_bytes = _decode_b64((portrait or {}).get("b64"))
    except Exception as e:
        print(f"[character webhook portrait error] {e}")
    try:
        return await text_channel.create_webhook(
            name=char_name[:80], avatar=avatar_bytes, reason="Character voice")
    except discord.Forbidden:
        print("[character webhook] missing Manage Webhooks permission")
    except Exception as e:
        print(f"[character webhook error] {e}")
    return None


# ---------------------------------------------------------------------------
# Owner leaving/rejoining the session voice channel (2-minute grace timer)
# ---------------------------------------------------------------------------

def is_session_voice(voice_channel_id: int) -> bool:
    """True if this voice channel belongs to a pending or active session."""
    return voice_channel_id in pending_sessions or _text_for_voice(voice_channel_id) is not None


def _session_owner_id(voice_channel_id: int) -> Optional[str]:
    if voice_channel_id in pending_sessions:
        return pending_sessions[voice_channel_id].get("owner_id")
    text_id = _text_for_voice(voice_channel_id)
    if text_id is not None:
        return active_sessions[text_id].get("owner_id")
    return None


async def on_session_voice_leave(member: discord.Member, voice_channel_id: int, bot) -> None:
    """The owner left their session voice channel — start a 2-minute grace timer
    that ends the session unless they rejoin first."""
    if str(member.id) != _session_owner_id(voice_channel_id):
        return
    existing = departure_tasks.get(voice_channel_id)
    if existing is not None and not existing.done():
        return   # timer already running
    guild = member.guild
    text_id = _text_for_voice(voice_channel_id)
    if text_id is not None:
        text_channel = guild.get_channel(text_id)
        if text_channel is not None:
            try:
                await text_channel.send(
                    "🚪 You left the session's voice channel. You have **2 minutes** to "
                    "rejoin before the session closes."
                )
            except Exception:
                pass
    departure_tasks[voice_channel_id] = asyncio.create_task(
        _departure_countdown(guild, voice_channel_id, str(member.id), bot))


def cancel_departure_timer(voice_channel_id: int) -> bool:
    """Cancel a running departure grace timer (owner came back). Returns True if
    one was cancelled."""
    task = departure_tasks.pop(voice_channel_id, None)
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


async def on_session_voice_rejoin(member: discord.Member, voice_channel_id: int, bot) -> None:
    """The owner rejoined their session voice channel — cancel the end timer."""
    if str(member.id) != _session_owner_id(voice_channel_id):
        return
    if cancel_departure_timer(voice_channel_id):
        text_id = _text_for_voice(voice_channel_id)
        if text_id is not None:
            text_channel = member.guild.get_channel(text_id)
            if text_channel is not None:
                try:
                    await text_channel.send("✅ Welcome back! The session continues.")
                except Exception:
                    pass


async def _departure_countdown(guild, voice_channel_id: int, owner_id: str, bot) -> None:
    """Wait the grace period, then end the session if the owner is still absent."""
    try:
        await asyncio.sleep(DEPARTURE_GRACE_SECONDS)
    except asyncio.CancelledError:
        return
    departure_tasks.pop(voice_channel_id, None)
    # Confirm the owner really isn't back in the voice channel.
    vc = guild.get_channel(voice_channel_id) if guild is not None else None
    if vc is not None and any(str(m.id) == owner_id for m in getattr(vc, "members", [])):
        return   # they came back right at the edge; leave the session running
    text_id = _text_for_voice(voice_channel_id)
    if text_id is not None:
        text_channel = guild.get_channel(text_id) if guild is not None else None
        if text_channel is not None:
            try:
                await text_channel.send(
                    "🌙 You didn't return within 2 minutes — closing the session. Safe travels!")
            except Exception:
                pass
    await _cleanup_session(guild, voice_channel_id, bot, reason="Owner left the voice channel")


# ---------------------------------------------------------------------------
# In-session messages: relay as the character, or reject non-participants
# ---------------------------------------------------------------------------

async def handle_session_message(message: discord.Message, bot) -> bool:
    """Handle a message posted in an active session text channel.

    Returns True if the message was handled here (so normal DM routing should
    stop). Owner/participant messages are relayed AS the character and sent to
    the DM brain; everyone else is politely told they aren't part of the game.
    """
    session = active_sessions.get(message.channel.id)
    if session is None:
        return False

    # Commands in a session channel are handled by the command processor, not
    # treated as in-game speech.
    if message.content.strip().startswith(bot.command_prefix):
        return True

    user_id = str(message.author.id)
    participant = session["participants"].get(user_id)

    if participant is None:
        # A non-character bystander posted here. Never route this to the DM brain;
        # just let them know (out-of-character) that they aren't in this game.
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
        except Exception as e:
            print(f"[session intruder delete error] {e}")
        owner = session["participants"].get(session["owner_id"], {})
        owner_name = owner.get("character_name", "another adventurer")
        entry = _entry_channel_mention(message.guild, bot)
        await message.channel.send(
            f"🚪 {message.author.mention} — your words don't reach this world. "
            f"This is **{owner_name}**'s active session, and you haven't entered it. "
            f"To play, go to {entry}, choose or create your own character, and log in as them."
        )
        return True

    session["last_message_at"] = _now()
    content = message.content.strip()
    if not content and not message.attachments:
        return True

    # Speak the line AS the character (webhook identity), then let the Oracle answer.
    await _relay_as_character(message, participant)

    if not content:
        return True

    result = await backend_integration.call_backend(
        content, session["backend_session_id"], user_id,
        participant["character_name"], bot.backend_url)
    reply = result.get("reply", "The Oracle is silent...")
    files = _scene_files(result.get("images"))
    await message.channel.send(reply, files=files if files else None)

    music_query = result.get("music")
    if music_query:
        voice_channel = message.guild.get_channel(session["voice_channel_id"])
        if voice_channel is not None:
            try:
                await music_player.play_query_in_channel(voice_channel, music_query)
            except Exception as e:
                print(f"[session scene music error] {e}")
    return True


async def _relay_as_character(message: discord.Message, participant: Dict) -> None:
    """Delete the player's raw message and repost it wearing the character's
    name + portrait, so the player visibly 'becomes' their character."""
    content = message.content
    char_name = participant["character_name"]
    webhook = participant.get("webhook")

    try:
        await message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass
    except Exception as e:
        print(f"[relay delete error] {e}")

    if webhook is None:
        # Fallback identity when webhooks aren't available.
        try:
            await message.channel.send(f"**{char_name}:** {content}")
        except Exception as e:
            print(f"[relay fallback error] {e}")
        return

    try:
        await webhook.send(content=content or "\u200b", username=char_name[:80], wait=True)
    except Exception as e:
        print(f"[relay webhook error] {e}")
        try:
            await message.channel.send(f"**{char_name}:** {content}")
        except Exception:
            pass


def _entry_channel_mention(guild, bot) -> str:
    """A clickable mention of the entry channel, or a plain name fallback."""
    if guild is not None:
        ch = None
        entry_id = getattr(bot, "entry_channel_id", None)
        if entry_id:
            ch = guild.get_channel(int(entry_id))
        if ch is None:
            name = getattr(bot, "entry_channel_name", None)
            if name:
                ch = discord.utils.get(guild.text_channels, name=name)
        if ch is not None:
            return ch.mention
    return "the entry channel"


# ---------------------------------------------------------------------------
# Ending / cleanup
# ---------------------------------------------------------------------------

async def end_session_for_channel(channel_id: int, bot, reason: str = "Session ended") -> bool:
    """End the active session that owns `channel_id` (its text channel).
    Returns True if a session was found and cleaned up."""
    session = active_sessions.get(channel_id)
    if session is None:
        return False
    guild = bot.get_guild(int(session["guild_id"])) if session.get("guild_id") else None
    if guild is None:
        guild = getattr(bot.get_channel(channel_id), "guild", None)
    await _cleanup_session(guild, session["voice_channel_id"], bot, reason)
    return True


async def _cleanup_session(guild, voice_channel_id: int, bot, reason: str) -> None:
    """Delete a session's channels + webhook and clear all tracking."""
    text_id = _text_for_voice(voice_channel_id)
    pending_sessions.pop(voice_channel_id, None)

    dep = departure_tasks.pop(voice_channel_id, None)
    if dep is not None and not dep.done():
        dep.cancel()

    try:
        await music_player.stop_music_in_channel(voice_channel_id)
    except Exception as e:
        print(f"[session cleanup music error] {e}")

    session = active_sessions.pop(text_id, None) if text_id is not None else None
    if session:
        for part in session.get("participants", {}).values():
            webhook = part.get("webhook")
            if webhook is not None:
                try:
                    await webhook.delete(reason="Session ended")
                except Exception:
                    pass

    if guild is not None:
        if text_id is not None:
            tc = guild.get_channel(text_id)
            if tc is not None:
                try:
                    await tc.delete(reason=reason)
                except Exception as e:
                    print(f"[session cleanup text delete error] {e}")
        vc = guild.get_channel(voice_channel_id)
        if vc is not None:
            try:
                await vc.delete(reason=reason)
            except Exception as e:
                print(f"[session cleanup voice delete error] {e}")

    music_control.music_preferences.pop(voice_channel_id, None)
    task = cleanup_tasks.pop(voice_channel_id, None)
    if task is not None and not task.done():
        task.cancel()


async def _schedule_idle_cleanup(guild, voice_channel_id: int, bot) -> None:
    """Close a session once it has been idle past the timeout."""
    try:
        while True:
            await asyncio.sleep(_IDLE_POLL_SECONDS)
            text_id = _text_for_voice(voice_channel_id)
            if text_id is not None and text_id in active_sessions:
                last = active_sessions[text_id]["last_message_at"]
            elif voice_channel_id in pending_sessions:
                last = pending_sessions[voice_channel_id]["created_at"]
            else:
                return   # session already gone
            if (_now() - last).total_seconds() > SESSION_IDLE_TIMEOUT:
                await _cleanup_session(
                    guild, voice_channel_id, bot, reason="Session idle timeout")
                return
    except asyncio.CancelledError:
        return
