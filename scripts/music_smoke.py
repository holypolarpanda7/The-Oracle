"""The table should HEAR that steel came out.

Nothing but the DM's own `[[MUSIC:]]` cue ever moved a table's playlist. So
initiative could be rolled, a board could come out and six creatures could
start swinging over the same tavern lute — and with combat narration lean or
muted there is no cue at all. A fight starting is a fact the TRACKER holds, and
the music is one of the few things at a table that can announce it before
anybody reads a word.

Two halves, one per side of the wire:

1. The BACKEND decides WHEN, from the tracker (`_sync_combat_music`), and holds
   a scene cue that arrives mid-fight rather than letting it play over the
   fight — the DM's own cue lands one line after the encounter opened, and
   without that rule it puts the lute straight back on.
2. The BOT decides WHICH, from its own mood vocabulary
   (`music_control.mood_for_query`) — the backend never names a playlist,
   because the bot is the only side that knows which moods it has audio for.

Offline: a scratch copy of the database, no Discord, no voice, no LLM, no GPU.

    uv run python scripts/music_smoke.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {GREEN}✓{OFF} {label}" if ok else f"  {RED}✗{OFF} {label}"
          + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        _fails.append(label)


# ---------------------------------------------------------------------------
# 1. which mood — the bot's half
# ---------------------------------------------------------------------------
def _load_music_control():
    """`music_control` without pulling in discord.py or the voice player."""
    sys.path.insert(0, str(ROOT / "ai-dm-sicord-bot"))
    d = types.ModuleType("discord")
    d.VoiceChannel = type("VoiceChannel", (object,), {})
    sys.modules.setdefault("discord", d)
    sys.modules.setdefault("music_player", types.ModuleType("music_player"))
    spec = importlib.util.spec_from_file_location(
        "music_control", str(ROOT / "ai-dm-sicord-bot" / "music_control.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


print(f"\n{BOLD}1. which mood a cue means{OFF}")
mc = _load_music_control()

# A keyword matched as a bare substring lives inside other words, and both of
# these are cues this has actually been handed.
check("a WARm tavern is not a war", mc.mood_for_query(
    "a warm tavern, lutes and laughter") == "tavern",
    mc.mood_for_query("a warm tavern, lutes and laughter"))
check("a BARe arena is not a bar", mc.mood_for_query(
    "the proving grounds — a bare arena between bouts") != "tavern",
    mc.mood_for_query("the proving grounds — a bare arena between bouts"))
# …while the long entries are deliberate STEMS and must still catch their
# own endings.
for cue, want in (("celebrating in the inn", "tavern"),
                  ("a bustling market", "town"),
                  ("a haunted crypt", "dungeon"),
                  ("dunes under a scorching sun", "desert")):
    check(f"'{cue}' still reads as {want}", mc.mood_for_query(cue) == want,
          mc.mood_for_query(cue))

# And none of the rest of this matters if the mood has no audio behind it.
audio = ROOT / "voice-service" / "audio" / "combat"
tracks = sorted(p.name for p in audio.glob("*.mp3")) if audio.is_dir() else []
check("and there is combat audio to play", bool(tracks),
      f"{len(tracks)} track(s)")


# ---------------------------------------------------------------------------
# 2. when it changes — the backend's half
# ---------------------------------------------------------------------------
print(f"\n{BOLD}2. when the music changes{OFF}")

db = Path(tempfile.gettempdir()) / "oracle_music_smoke.db"
for suffix in ("", "-wal", "-shm"):
    Path(str(db) + suffix).unlink(missing_ok=True)
live = ROOT / "oracle-dm-backend" / "oracle.db"
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
os.environ["ORACLE_IMAGERY_ENABLED"] = "0"

spec = importlib.util.spec_from_file_location(
    "dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
asyncio.run(m.lifespan(m.app).__aenter__())

CH = "music-smoke-channel"
SID = "music:smoke"
m._set_session_meta(SID, {"user_id": "smoke", "character_name": "Smoke",
                          "activity_channel": CH})


def cue():
    return (m._ACTIVITY_MUSIC.get(CH) or {}).get("query")


def seq():
    return (m._ACTIVITY_MUSIC.get(CH) or {}).get("seq", 0)


m._set_activity_music(CH, "a warm tavern, lutes and laughter")
before = cue()
check("a scene cue plays while nothing is happening",
      mc.mood_for_query(before) == "tavern", before)

enc = m.combat.start_encounter(SID, "The Sand Ring")
m._sync_combat_music(SID, CH)
check("a fight opening changes the music by itself",
      mc.mood_for_query(cue() or "") == "combat", cue())

at = seq()
m._sync_combat_music(SID, CH)
check("…and says so once, not every time it is asked", seq() == at,
      f"seq {at} -> {seq()}")

# The DM's own cue for the scene lands one line after the encounter opened.
m._set_activity_music(CH, "a warm tavern, lutes and laughter")
check("a scene cue arriving mid-fight does not play over it",
      mc.mood_for_query(cue() or "") == "combat", cue())

m.combat.end_encounter(enc.id)
m._sync_combat_music(SID, CH)
check("…but it is what plays when the fight ends",
      mc.mood_for_query(cue() or "") == "tavern", cue())

# A fight with no scene before it still has somewhere to go back to.
m._ACTIVITY_MUSIC.pop(CH, None)
m._COMBAT_MUSIC.pop(CH, None)
enc2 = m.combat.start_encounter(SID, "Again")
m._sync_combat_music(SID, CH)
check("a fight from silence still sounds like a fight",
      mc.mood_for_query(cue() or "") == "combat", cue())
m.combat.end_encounter(enc2.id)
m._sync_combat_music(SID, CH)
check("…and afterwards the table gets the PLACE's own words",
      bool(cue()) and mc.mood_for_query(cue()) != "combat", cue())

print()
if _fails:
    print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
    raise SystemExit(1)
print(f"{GREEN}the table hears the fight start, and hears it end{OFF}")
