"""Session-table lifecycle — a table closes when the players go, and not before.

Offline: no Discord connection, no gateway, no voice. Fake guild/channel/member
objects are driven through the REAL `handle_voice_state_update`, because the
bug this exists for was in that function's idea of "empty".

What it pins:

1. The voice SIDECAR doesn't count as an occupant. It joins as its own bot user
   to play the table's music and never leaves on its own, so counting it kept
   every table open forever — the last player walked out and the channel stayed.
2. A table the last player left closes after EMPTY_GRACE_SECONDS, not instantly.
3. Stepping back inside that window keeps the table — a dropped client, a
   switch to the phone, an Activity reconnect.
4. A table nobody ever joined is still swept by the idle sweep.

Usage:  uv run python scripts/session_table_smoke.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ai-dm-sicord-bot"))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = f"{GREEN}✓{OFF}" if ok else f"{RED}✗{OFF}"
    print(f"  {mark} {label}" + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        _fails.append(label)


class FakeMember:
    def __init__(self, mid: int, name: str, *, bot: bool = False, guild=None):
        self.id, self.name, self.bot, self.guild = mid, name, bot, guild

    async def send(self, *a, **k):        # DMs are not what this test is about
        raise RuntimeError("DMs closed")


class FakeChannel:
    def __init__(self, cid: int, guild):
        self.id, self.guild, self.name = cid, guild, "The Emberlit Vault"
        self.members: list = []
        self.deleted = False

    async def delete(self, reason: str = ""):
        self.deleted = True
        self.guild.channels.pop(self.id, None)


class FakeGuild:
    def __init__(self):
        self.id = 999
        self.channels: dict = {}

    def get_channel(self, cid):
        return self.channels.get(cid)


class FakeState:
    def __init__(self, channel):
        self.channel = channel


async def main() -> int:
    import session_channels as sc

    sc.EMPTY_GRACE_SECONDS = 0.3          # the real one is 20s
    sc.IDLE_SWEEP_SECONDS = 0.3
    grace = sc.EMPTY_GRACE_SECONDS
    # The music sidecar is a live HTTP service; a table test must not need it.
    async def _no_music(channel, bot):
        return None
    sc._start_table_music = _no_music

    def table(cid: int):
        guild = FakeGuild()
        ch = FakeChannel(cid, guild)
        guild.channels[cid] = ch
        sc.ephemeral_session_channels[cid] = {
            "owner_id": "1", "owner_name": "Kara", "name": ch.name}
        return guild, ch

    print(f"\n{BOLD}1. the music sidecar is not an occupant{OFF}")
    guild, ch = table(101)
    player = FakeMember(1, "Kara", guild=guild)
    dave = FakeMember(2, "Oracle Voice", bot=True, guild=guild)
    ch.members = [player, dave]
    check("a bot in the channel isn't a player", sc.seated(ch) == [player],
          f"{len(ch.members)} members, {len(sc.seated(ch))} seated")

    ch.members = [dave]                    # the player walks out; music plays on
    await sc.handle_voice_state_update(
        player, FakeState(ch), FakeState(None), bot=None)
    check("leaving starts the close, sidecar or not", 101 in sc._empty_tasks)
    await asyncio.sleep(grace * 2.5)
    check("…and the table is gone", ch.deleted and 101 not in sc.ephemeral_session_channels)

    print(f"\n{BOLD}2. the table is held open for a moment{OFF}")
    guild, ch = table(102)
    player = FakeMember(1, "Kara", guild=guild)
    ch.members = []
    await sc.handle_voice_state_update(
        player, FakeState(ch), FakeState(None), bot=None)
    await asyncio.sleep(grace * 0.4)
    check("still there immediately after the last player leaves", not ch.deleted)
    await asyncio.sleep(grace * 2.0)
    check("closed once the grace period is up", ch.deleted)

    print(f"\n{BOLD}3. stepping back in keeps the table{OFF}")
    guild, ch = table(103)
    player = FakeMember(1, "Kara", guild=guild)
    ch.members = []
    await sc.handle_voice_state_update(
        player, FakeState(ch), FakeState(None), bot=None)
    await asyncio.sleep(grace * 0.3)
    ch.members = [player]                  # back before the sweep fires
    await sc.handle_voice_state_update(
        player, FakeState(None), FakeState(ch), bot=None)
    check("the pending close is cancelled", 103 not in sc._empty_tasks)
    await asyncio.sleep(grace * 2.5)
    check("…and the table survived", not ch.deleted and 103 in sc.ephemeral_session_channels)

    print(f"\n{BOLD}4. a table nobody sat at is still swept{OFF}")
    guild, ch = table(104)
    sc._idle_tasks[104] = asyncio.create_task(sc._idle_sweep(guild, 104))
    await asyncio.sleep(sc.IDLE_SWEEP_SECONDS * 2.5)
    check("the idle sweep still fires", ch.deleted)

    print()
    if _fails:
        print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
        return 1
    print(f"{GREEN}a table closes when its players go — and not while they're back{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
