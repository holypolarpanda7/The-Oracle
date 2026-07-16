"""Ephemeral session voice channels — the single entry surface for play.

A player presses **Enter the Oracle** in the entry channel; the bot spawns a
fresh, fun-named voice channel (a "table") and hands back a one-click button
that both drops them into the channel and launches the web Activity in it.
Character creation, resume, and play all happen inside the Activity, so there
is no separate character-creation channel any more — one table does everything.

Tables are ephemeral: when the last person leaves, the channel is deleted. A
table that nobody joins is swept after a short idle window.

Discord note: a bot cannot force a client to open an Activity. The launch is an
*embedded-application invite* (target_type=embedded_application) — clicking it
joins the voice channel AND opens the Activity in one action. That invite is the
"auto-start": we surface it the instant a table is made, and again (via DM) if
someone joins a table without having clicked it.
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime, timezone
from typing import Dict, Optional

import aiohttp
import discord


# channel_id -> {owner_id, owner_name, created_at, name}
ephemeral_session_channels: Dict[int, Dict] = {}
# channel_id -> the idle-sweep task, so we can cancel it once someone joins.
_idle_tasks: Dict[int, asyncio.Task] = {}

# Category the tables live under; created on demand if absent.
SESSION_CATEGORY_NAME = "The Oracle — Tables"
# How long an untouched (never-joined) table lingers before it's swept.
IDLE_SWEEP_SECONDS = 900  # 15 minutes


# ---------------------------------------------------------------------------
# Fun dynamic naming
# ---------------------------------------------------------------------------
# Evocative, candle-lit-tavern flavour rather than "Session #3". Names are
# assembled from a few patterns so tables read like places in the living world.

_EMOJI = ["🔮", "🕯️", "⚔️", "🐉", "🍺", "🗺️", "🌙", "🔥", "⭐", "🏰", "📜", "🎲", "🗡️", "🏹"]

_ADJECTIVES = [
    "Whispering", "Emberlit", "Gilded", "Moonlit", "Sunken", "Hollow", "Wandering",
    "Ashen", "Verdant", "Forgotten", "Crimson", "Silvered", "Weeping", "Thornbound",
    "Stormcalled", "Candlewrought", "Dusklit", "Frostbitten", "Hallowed", "Wayworn",
]
_PLACES = [
    "Vault", "Hearth", "Hollow", "Crossroads", "Sanctum", "Flagon", "Table", "Reach",
    "Threshold", "Lantern", "Gallows", "Refuge", "Alcove", "Bastion", "Snug", "Longhall",
]
_NOUNS = [
    "Ravens", "Embers", "Wyrm", "Vagabonds", "Oath", "Fool", "Pilgrim", "Coin",
    "Tankard", "Wanderer", "Lantern-Keeper", "Broken Crown", "Last Light", "Dawn",
]

def _possessive(word: str) -> str:
    """Grammatically correct possessive: ``Ravens'`` but ``Pilgrim's``."""
    return word + "'" if word.endswith("s") else word + "'s"


# Each pattern is a callable so we can mix structures.
_PATTERNS = [
    lambda: f"The {random.choice(_ADJECTIVES)} {random.choice(_PLACES)}",
    lambda: f"{random.choice(_PLACES)} of the {random.choice(_NOUNS)}",
    lambda: f"The {_possessive(random.choice(_NOUNS))} {random.choice(_PLACES)}",
    lambda: f"{random.choice(_ADJECTIVES)} {random.choice(_PLACES)}",
]


def generate_session_name(guild: Optional[discord.Guild] = None) -> str:
    """A fresh, flavourful table name like ``🕯️ The Whispering Vault``.

    Tries a handful of times to avoid colliding with a live table's name in the
    same guild; falls back to a suffixed name if it somehow can't find a free one.
    """
    existing = set()
    if guild is not None:
        existing = {c.name for c in guild.voice_channels}

    for _ in range(12):
        name = f"{random.choice(_EMOJI)} {random.choice(_PATTERNS)()}"
        if name not in existing:
            return name
    # Extremely unlikely: decorate with a short random tag to guarantee uniqueness.
    return f"{random.choice(_EMOJI)} {random.choice(_PATTERNS)()} · {random.randint(2, 999)}"


# ---------------------------------------------------------------------------
# Launch invite (join voice + open the Activity in one click)
# ---------------------------------------------------------------------------

def activity_client_id() -> str:
    return os.getenv("ORACLE_DM_CLIENT_ID", "").strip()


async def ensure_entry_point_command(bot) -> None:
    """Guarantee the app has a PRIMARY_ENTRY_POINT command.

    An app with Activities must expose an entry-point command or launching the
    Activity fails with *"make sure the app is installed and there is a proper
    app entrypoint"*. Discord only auto-creates one under certain install
    settings, so we ensure it ourselves on startup. Idempotent, and uses POST
    (create) — never a bulk overwrite that could wipe the bot's other commands.
    """
    app_id = getattr(bot, "application_id", None)
    token = os.getenv("ORACLE_DM_TOKEN", "").strip()
    if not app_id or not token:
        return
    base = f"https://discord.com/api/v10/applications/{app_id}/commands"
    headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(base, headers=headers) as resp:
                if resp.status != 200:
                    print(f"[activity] entry-point check failed: HTTP {resp.status}")
                    return
                commands = await resp.json()
            # type 4 == PRIMARY_ENTRY_POINT
            if any(c.get("type") == 4 for c in commands):
                print("[activity] entry-point command already present.")
                return
            payload = {
                "name": "launch",
                "description": "Launch The Oracle",
                "type": 4,      # PRIMARY_ENTRY_POINT
                "handler": 2,   # DISCORD_LAUNCH_ACTIVITY — Discord opens the Activity
            }
            async with session.post(base, headers=headers, json=payload) as resp:
                if resp.status in (200, 201):
                    print("[activity] created PRIMARY_ENTRY_POINT command 'launch'.")
                else:
                    body = await resp.text()
                    print(f"[activity] entry-point create failed: HTTP {resp.status} {body}")
    except Exception as e:  # noqa: BLE001 - never block startup on this
        print(f"[activity] entry-point ensure error: {e}")


def launch_view_from_url(url: str) -> discord.ui.View:
    """A one-button view whose link joins the table and opens the Activity."""
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="Take your seat & begin", style=discord.ButtonStyle.link,
        url=url, emoji="🔮"))
    return view


async def make_launch_invite(channel: discord.VoiceChannel) -> "tuple[Optional[str], Optional[str]]":
    """Mint an embedded-application invite URL for ``channel`` (join + launch).

    Returns ``(url, None)`` on success, or ``(None, reason)`` with a
    player-facing explanation of why the invite couldn't be made.
    """
    client_id = activity_client_id()
    if not client_id:
        return None, "the Activity isn't configured yet (missing `ORACLE_DM_CLIENT_ID`)."
    try:
        invite = await channel.create_invite(
            target_type=discord.InviteTarget.embedded_application,
            target_application_id=int(client_id),
            max_age=86400, unique=True,
            reason="Launch The Oracle Activity")
    except discord.Forbidden:
        print(f"[session] no Create Invite permission on {channel.id}")
        return None, "I need the **Create Invite** permission on that channel."
    except discord.HTTPException as e:
        print(f"[session] launch invite failed for {channel.id}: {e}")
        # 50035 + "not embedded" = the app hasn't had Activities enabled in the
        # Developer Portal, so it can't be an embedded-app invite target.
        if getattr(e, "code", None) == 50035 and "not embedded" in str(e).lower():
            return None, (
                "this app isn't enabled as a Discord **Activity** yet. In the "
                "Developer Portal → your app → **Activities**, turn Activities on and "
                "add a URL mapping to `oracle.oracle-dm.com`, then try again.")
        return None, f"Discord rejected the launch invite (error {getattr(e, 'code', '?')})."
    except Exception as e:  # noqa: BLE001 - surface, don't crash the flow
        print(f"[session] launch invite failed for {channel.id}: {e}")
        return None, "something went wrong creating the launch invite."
    return invite.url, None


async def make_launch_view(channel: discord.VoiceChannel) -> Optional[discord.ui.View]:
    """Convenience: an invite + button view in one call (None if unavailable)."""
    url, _reason = await make_launch_invite(channel)
    return launch_view_from_url(url) if url else None


# ---------------------------------------------------------------------------
# Table lifecycle
# ---------------------------------------------------------------------------

def find_session_by_owner(user_id: str) -> Optional[int]:
    """Return a live table this user already owns, if any (avoids spam-spawning)."""
    for cid, data in ephemeral_session_channels.items():
        if str(data.get("owner_id")) == str(user_id):
            return cid
    return None


# Suppress a duplicate on-join launch DM if we just handed them the button.
_LAUNCH_DM_DEDUPE_SECONDS = 120


def note_launch_notified(channel_id: int) -> None:
    """Record that we just gave this table's player a launch button, so the
    on-join handler doesn't immediately DM them a second one."""
    data = ephemeral_session_channels.get(channel_id)
    if data is not None:
        data["last_launch_dm"] = datetime.now(timezone.utc)


def _recently_notified(channel_id: int) -> bool:
    data = ephemeral_session_channels.get(channel_id)
    last = data.get("last_launch_dm") if data else None
    if not last:
        return False
    return (datetime.now(timezone.utc) - last).total_seconds() < _LAUNCH_DM_DEDUPE_SECONDS


async def _get_or_make_category(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    cat = discord.utils.find(
        lambda c: isinstance(c, discord.CategoryChannel) and c.name == SESSION_CATEGORY_NAME,
        guild.channels)
    if cat is not None:
        return cat
    try:
        return await guild.create_category(SESSION_CATEGORY_NAME, reason="Oracle session tables")
    except Exception as e:  # noqa: BLE001 - fall back to no category
        print(f"[session] could not create category: {e}")
        return None


async def create_session_channel(
    guild: discord.Guild, owner: discord.Member, bot,
) -> Optional[discord.VoiceChannel]:
    """Spawn a fresh, fun-named ephemeral table, PRIVATE to its owner.

    Only the owner and the bot may see or join, so the launch invite is useless
    to anyone else — it effectively works for the individual who logged in.
    (Co-op tables are a later add: grant a friend a view/connect overwrite to
    seat them at the same table.) Returns None if creation is refused."""
    name = generate_session_name(guild)

    # Private table: deny @everyone, allow only the owner and the bot. This is
    # what makes the invite single-player — others can hold the link but can't
    # connect to the channel it points at.
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False, connect=False),
        owner: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
        bot.user: discord.PermissionOverwrite(view_channel=True, connect=True, speak=True),
    }

    category = await _get_or_make_category(guild)
    try:
        channel = await guild.create_voice_channel(
            name, category=category, overwrites=overwrites,
            reason=f"Oracle table for {owner.display_name}")
    except discord.Forbidden:
        print("[session] missing Manage Channels permission")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[session] channel creation failed: {e}")
        return None

    ephemeral_session_channels[channel.id] = {
        "owner_id": str(owner.id),
        "owner_name": owner.display_name,
        "created_at": datetime.now(timezone.utc),
        "name": name,
    }
    # Default ambient music on for the table.
    try:
        import music_control
        music_control.music_preferences[channel.id] = {
            "enabled": True, "current_playlist": "cc_menu"}
    except Exception:
        pass

    # Sweep the table if nobody ever takes a seat.
    _idle_tasks[channel.id] = asyncio.create_task(
        _idle_sweep(guild, channel.id))
    print(f"[session] created table '{name}' ({channel.id}) for {owner.display_name}")
    return channel


async def _idle_sweep(guild: discord.Guild, channel_id: int) -> None:
    """Delete a table that nobody joined within the idle window."""
    try:
        await asyncio.sleep(IDLE_SWEEP_SECONDS)
    except asyncio.CancelledError:
        return
    channel = guild.get_channel(channel_id)
    if channel is not None and not channel.members:
        await cleanup_session_channel(guild, channel_id, reason="No one took a seat")


async def cleanup_session_channel(guild: discord.Guild, channel_id: int, reason: str) -> None:
    """Delete a table and forget all state tied to it."""
    task = _idle_tasks.pop(channel_id, None)
    if task is not None:
        task.cancel()
    try:
        import music_player
        await music_player.stop_music_in_channel(channel_id)
    except Exception:
        pass
    try:
        import music_control
        music_control.music_preferences.pop(channel_id, None)
    except Exception:
        pass

    channel = guild.get_channel(channel_id)
    if channel is not None:
        try:
            await channel.delete(reason=f"Oracle table cleanup: {reason}")
            print(f"[session] deleted table {channel_id} ({reason})")
        except Exception as e:  # noqa: BLE001
            print(f"[session] delete failed for {channel_id}: {e}")
    ephemeral_session_channels.pop(channel_id, None)


# ---------------------------------------------------------------------------
# Voice-state routing
# ---------------------------------------------------------------------------

async def handle_voice_state_update(
    member: discord.Member,
    before: discord.VoiceState,
    after: discord.VoiceState,
    bot,
) -> None:
    """Keep tables tidy and nudge joiners toward the one-click launch.

    * Someone JOINS a table they didn't launch from → DM them the launch button
      (the feasible "auto-start": we can't force the client to open it).
    * A table EMPTIES → delete it.
    """
    if member.bot:
        return

    before_id = before.channel.id if before.channel else None
    after_id = after.channel.id if after.channel else None
    if before_id == after_id:
        return  # mute/deafen/etc., not a move

    # Joined a live table: hand them the launch button in a DM — unless we just
    # gave them one (the normal launch-then-join path), which would double-DM.
    if after_id in ephemeral_session_channels and after.channel is not None:
        # Cancel the idle sweep — the table is in use now.
        task = _idle_tasks.pop(after_id, None)
        if task is not None:
            task.cancel()
        if not _recently_notified(after_id):
            view = await make_launch_view(after.channel)
            if view is not None:
                try:
                    await member.send(
                        f"🔮 You've taken a seat at **{after.channel.name}**. "
                        "Click below to open The Oracle:", view=view)
                    note_launch_notified(after_id)
                except discord.Forbidden:
                    pass  # DMs closed — the Activity tray still works manually

    # Left a table that is now empty: sweep it.
    if before_id in ephemeral_session_channels and before.channel is not None:
        if not before.channel.members:
            await cleanup_session_channel(
                member.guild, before_id, reason="Table emptied")
