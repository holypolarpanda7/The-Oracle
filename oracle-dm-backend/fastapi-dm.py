import os
import json
import re
import base64
from pathlib import Path
from typing import Dict, List, Literal, TypedDict, Optional
import uuid
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import Response
from pydantic import BaseModel
from dotenv import load_dotenv

# Database (SQLModel)
from sqlmodel import SQLModel, Field, create_engine, Session, select
from sqlalchemy import Column, JSON, String, Text
from typing import Any
from datetime import datetime
import sys

# Make the project root importable so the backend can use the shared packages.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eight_card_system import WorldGraph
from eight_card_system.graph import slugify
from eight_card_system.seed import seed_starter_world, place_pc
from eight_card_system.extraction import extract_and_apply
from rules import (
    RulesLibrary,
    format_monster_brief,
    format_spell_brief,
    format_item_brief,
    format_reference_brief,
    ingest_srd,
    ingest_items,
    ingest_reference,
    seed_classes_and_subclasses,
    level_up_report,
    OWNED_MONSTERS,
    seed_owned_monsters,
    MONSTER_TEMPLATES,
    list_templates,
    scale_monster,
    monster_to_dict,
)
from rules.models import Subclass, DndClass, Item, SrdEntry, Monster
from combat import CombatTracker, Condition
from dice import ability_check, ability_modifier, roll as dice_roll, proficiency_bonus_for_level
from game_config import get_config
from economy import (
    empty_purse,
    add_coins,
    subtract_cost,
    to_cp,
    gp_to_cp,
    gp_value,
    format_purse,
    resolve_downtime,
    start_crafting,
    advance_crafting,
)
from economy.models import DowntimeLog, CraftingProject
from bastion import (
    can_own_bastion,
    facilities_for_level,
    facility_cost_gp,
    resolve_bastion_turn,
    min_bastion_level,
    get_facility,
)
from bastion.models import Bastion, FacilityInstance, BastionEvent
from survival import (
    consume_day,
    add_exhaustion,
    remove_exhaustion,
    describe_exhaustion,
    short_rest as survival_short_rest,
    long_rest as survival_long_rest,
    encumbrance_status,
    generate_weather,
    hazards_from_weather,
    active_hazard_tags,
    travel as survival_travel,
    navigation_dc,
    forage as survival_forage,
    source_spec,
    burn as light_burn,
)
from hazards import (
    contract_disease,
    disease_recovery_check,
    trap_detect,
    trap_disarm,
    roll_madness,
    Affliction,
)
from reputation import (
    describe_standing,
    adjust_renown,
    Reputation,
)
from dm_guide import (
    guidance_block,
    full_guidance,
    brief_guidance,
    suggest_dc,
    dc_scale,
    estimate_encounter,
    build_encounter,
)
from imagery import ImageStore, ImageResult
# ----- Env loading -----

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / "backend-cred.env"  # <-- your env file name

load_dotenv(ENV_PATH)

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-70b-instruct")

if not OPENROUTER_API_KEY:
    raise RuntimeError(f"OPENROUTER_API_KEY not found in {ENV_PATH}!")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"


# ----- Lifespan Context Manager -----

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create DB tables if they do not exist
    SQLModel.metadata.create_all(engine)
    print("[Startup] Database tables created/verified")

    # Seed the persistent starter world once (offline, idempotent).
    try:
        world.create_tables()
        if world.get_entity("greenfields") is None:
            seed_starter_world(world)
            print("[Startup] Seeded starter world (Greenfields)")
    except Exception as e:
        print(f"[Startup] World seed skipped: {e}")

    # Seed the SRD rules reference if empty (best-effort; needs network).
    try:
        if rules_lib.count().get("monsters", 0) == 0:
            counts = ingest_srd(engine=engine)
            rules_lib.refresh_index()
            print(f"[Startup] Ingested SRD rules: {counts}")
    except Exception as e:
        print(f"[Startup] SRD ingest skipped (offline?): {e}")

    # Seed SRD equipment + magic items if empty (best-effort; needs network).
    try:
        if rules_lib.count().get("items", 0) == 0:
            item_counts = ingest_items(engine=engine)
            print(f"[Startup] Ingested SRD items: {item_counts}")
    except Exception as e:
        print(f"[Startup] Item ingest skipped (offline?): {e}")

    # Sweep the broad SRD mechanics (conditions, skills, feats, races, ...).
    try:
        if rules_lib.count().get("reference", 0) == 0:
            ref_counts = ingest_reference(engine=engine)
            print(f"[Startup] Ingested SRD reference: {ref_counts}")
    except Exception as e:
        print(f"[Startup] Reference ingest skipped (offline?): {e}")

    # Seed classes + subclasses (offline; includes owned non-SRD like Bladesinger).
    try:
        with Session(engine) as _s:
            has_classes = _s.exec(select(Subclass)).first() is not None
        if not has_classes:
            cls_counts = seed_classes_and_subclasses(engine=engine)
            print(f"[Startup] Seeded classes/subclasses: {cls_counts}")
    except Exception as e:
        print(f"[Startup] Class seed skipped: {e}")

    # Seed the owned (self-authored, non-SRD) bestiary — offline & idempotent.
    try:
        owned = seed_owned_monsters(engine=engine)
        if owned.get("owned_monsters_new", 0):
            rules_lib.refresh_index()
        print(f"[Startup] Seeded owned monsters: {owned}")
    except Exception as e:
        print(f"[Startup] Owned monster seed skipped: {e}")

    yield
    # Shutdown: Add cleanup logic here if needed
    print("[Shutdown] FastAPI shutting down")


app = FastAPI(lifespan=lifespan)


# ----- Models -----

class ChatRequest(BaseModel):
    session_id: str
    user_id: str
    username: str
    message: str
    channel_id: str
    guild_id: str


class ChatResponse(BaseModel):
    reply: str
    # Optional AI-recommended ambient-music search query for the current scene.
    music: Optional[str] = None
    # Optional scene pictures (base64 WebP + metadata) for the bot to attach.
    images: Optional[List[Dict[str, Any]]] = None


class ResetRequest(BaseModel):
    session_id: str


class EnterRequest(BaseModel):
    user_id: str
    username: str
    guild_id: str
    character_name: Optional[str] = None


class EnterResponse(BaseModel):
    status: str
    message: str
    session_id: Optional[str] = None
    intro: Optional[str] = None
    world_snippet: Optional[str] = None
    starting_region: Optional[str] = None
    characters: Optional[List[str]] = None
    # Optional AI-recommended opening ambient-music search query.
    music: Optional[str] = None


class RegisterCharacterRequest(BaseModel):
    discord_user_id: str
    name: str
    race: Optional[str] = None
    char_class: Optional[str] = None
    subclass: Optional[str] = None
    level: int = 1
    stats: Optional[Dict[str, int]] = None
    ddb_url: Optional[str] = None
    avrae_import_text: Optional[str] = None
    approve: Optional[bool] = False
    home_region: Optional[str] = None
    source: Optional[str] = "manual"  # one of: 'avrae', 'guided', 'manual'


class CheckCharacterRequest(BaseModel):
    discord_user_id: str


class LevelUpRequest(BaseModel):
    character_id: int
    # Provide when the character reaches the level that chooses a subclass, or to
    # (re)assign one. Validated against the class's subclass level + roster.
    subclass: Optional[str] = None


class CombatStartRequest(BaseModel):
    session_id: str
    name: str = "Encounter"


class CombatAddRequest(BaseModel):
    encounter_id: int
    kind: str = "monster"  # pc | npc | monster
    name: Optional[str] = None
    monster_slug: Optional[str] = None
    count: int = 1
    roll_hp: bool = False
    character_id: Optional[int] = None
    max_hp: Optional[int] = None
    armor_class: Optional[int] = None
    dex_mod: int = 0
    initiative: int = 0


class CombatDamageRequest(BaseModel):
    combatant_id: int
    amount: int


class CombatConditionRequest(BaseModel):
    combatant_id: int
    condition: str
    remove: bool = False


class CombatConcentrationRequest(BaseModel):
    combatant_id: int
    spell: Optional[str] = None



# ----- In-memory session store -----

Role = Literal["player", "dm"]


class Turn(TypedDict):
    role: Role
    user: str
    content: str


class SessionMemory(SQLModel, table=True):
    session_id: str = Field(primary_key=True, index=True)
    summary_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    recent_turns_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    meta_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    turn_count: int = Field(default=0, index=True)
    compaction_count: int = Field(default=0)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


SESSIONS: Dict[str, List[Turn]] = {}
SESSION_META: Dict[str, Dict] = {}
SESSION_STATE_CACHE: Dict[str, Dict[str, Any]] = {}


# Database engine (default: SQLite in project dir). Change DATABASE_URL to a Postgres URL for production.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'oracle.db'}")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)

# Shared subsystems on the same DB: persistent world graph + SRD rules reference.
world = WorldGraph(engine=engine)
rules_lib = RulesLibrary(engine=engine)
# Initiative-ordered combat state tracker (PCs, NPCs, monsters).
combat = CombatTracker(engine=engine)
# Self-hosted scene imagery (diffusion-backed, offline-tolerant). World-day aware
# so stored pictures are tagged with the in-world day they were made.
image_store = ImageStore(engine=engine, world_day_fn=world.current_day)

# Per-session metadata (which PC is playing) alongside the in-memory history.


def _default_session_state() -> Dict[str, Any]:
    return {
        "summary_text": "",
        "recent_turns": [],
        "meta": {},
        "turn_count": 0,
        "compaction_count": 0,
        "updated_at": datetime.utcnow(),
    }


def _parse_json_field(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except Exception:
        return default


def _session_state_to_cache(row: SessionMemory) -> Dict[str, Any]:
    state = _default_session_state()
    state["summary_text"] = row.summary_text or ""
    state["recent_turns"] = _parse_json_field(row.recent_turns_json, [])
    state["meta"] = _parse_json_field(row.meta_json, {})
    state["turn_count"] = row.turn_count or 0
    state["compaction_count"] = row.compaction_count or 0
    state["updated_at"] = row.updated_at or datetime.utcnow()
    return state


def _load_session_state(session_id: str) -> Dict[str, Any]:
    cached = SESSION_STATE_CACHE.get(session_id)
    if cached is not None:
        return cached

    with Session(engine) as db:
        row = db.get(SessionMemory, session_id)
        if row is None:
            state = _default_session_state()
        else:
            state = _session_state_to_cache(row)

    SESSION_STATE_CACHE[session_id] = state
    SESSIONS[session_id] = list(state["recent_turns"])
    SESSION_META[session_id] = dict(state["meta"])
    return state


def _save_session_state(session_id: str, state: Dict[str, Any]) -> None:
    state["updated_at"] = datetime.utcnow()
    with Session(engine) as db:
        row = db.get(SessionMemory, session_id)
        if row is None:
            row = SessionMemory(session_id=session_id)
        row.summary_text = state.get("summary_text", "") or ""
        row.recent_turns_json = json.dumps(state.get("recent_turns", []), ensure_ascii=True)
        row.meta_json = json.dumps(state.get("meta", {}), ensure_ascii=True)
        row.turn_count = int(state.get("turn_count", 0) or 0)
        row.compaction_count = int(state.get("compaction_count", 0) or 0)
        row.updated_at = state["updated_at"]
        db.add(row)
        db.commit()
    SESSION_STATE_CACHE[session_id] = state
    SESSIONS[session_id] = list(state.get("recent_turns", []))
    SESSION_META[session_id] = dict(state.get("meta", {}))


def _set_session_meta(session_id: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    state = _load_session_state(session_id)
    state["meta"] = dict(meta)
    _save_session_state(session_id, state)
    return state


def _append_turn(session_id: str, turn: Turn) -> Dict[str, Any]:
    state = _load_session_state(session_id)
    recent_turns = list(state.get("recent_turns", []))
    recent_turns.append(turn)
    state["recent_turns"] = recent_turns
    state["turn_count"] = int(state.get("turn_count", 0) or 0) + 1
    _save_session_state(session_id, state)
    return state


def _render_turns_for_summary(turns: List[Turn]) -> str:
    lines: List[str] = []
    for turn in turns:
        role = turn.get("role", "player")
        user = turn.get("user", "")
        content = (turn.get("content", "") or "").strip()
        if not content:
            continue
        prefix = "Player" if role == "player" else "DM"
        if user and user not in ("Oracle DM", ""):
            prefix = f"{prefix} {user}"
        lines.append(f"- {prefix}: {content}")
    return "\n".join(lines)


def _session_summary_block(session_id: str) -> str:
    state = _load_session_state(session_id)
    summary = (state.get("summary_text") or "").strip()
    if not summary:
        return ""
    return "# Session memory summary\n\n" + summary


def _maybe_compact_session_memory(session_id: str) -> None:
    cfg = get_config().session_memory
    state = _load_session_state(session_id)
    recent_turns = list(state.get("recent_turns", []))
    if len(recent_turns) <= cfg.compaction_threshold:
        return

    keep_recent = max(1, cfg.recent_turns)
    overflow_count = max(0, len(recent_turns) - keep_recent)
    if overflow_count <= 0:
        return

    overflow_turns = recent_turns[:overflow_count]
    kept_turns = recent_turns[-keep_recent:]

    prior_summary = (state.get("summary_text") or "").strip()
    summary_prompt = (
        "You compress long-running tabletop RPG session memory for a dungeon-master assistant. "
        "Update the running memory so future turns stay grounded after the live chat window is trimmed.\n\n"
        "Rules:\n"
        "- Preserve facts, unresolved quests, NPCs, locations, combat state, injuries, inventory, promises, and active mysteries.\n"
        "- Keep it concise and durable. Prefer short paragraphs or bullets.\n"
        "- Do not invent new facts. If something is uncertain, say so briefly.\n"
        "- Stay under the requested length and avoid story prose.\n\n"
        f"Current summary (may be empty):\n{prior_summary or '[none]'}\n\n"
        f"Turns to compress:\n{_render_turns_for_summary(overflow_turns)}\n"
    )

    try:
        merged_summary = call_openrouter_chat(
            [
                {"role": "system", "content": "You are a precise context compressor for an RPG memory ledger."},
                {"role": "user", "content": summary_prompt},
            ],
            max_tokens=cfg.compaction_max_tokens,
            timeout_seconds=cfg.compaction_timeout_seconds,
        ).strip()
    except Exception as e:
        print(f"[session compact error] {e}")
        merged_summary = prior_summary

    if merged_summary:
        if prior_summary and prior_summary not in merged_summary:
            merged_summary = f"{prior_summary}\n\n{merged_summary}"
    else:
        merged_summary = prior_summary

    merged_summary = merged_summary[: cfg.summary_max_chars].strip()
    state["summary_text"] = merged_summary
    state["recent_turns"] = kept_turns
    state["compaction_count"] = int(state.get("compaction_count", 0) or 0) + 1
    _save_session_state(session_id, state)


def _schedule_session_compaction(background_tasks: BackgroundTasks, session_id: str) -> None:
    background_tasks.add_task(_maybe_compact_session_memory, session_id)

class Character(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    # Identity
    discord_user_id: str = Field(sa_column=Column(String, nullable=False, index=True))
    avrae_id: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    # Basic character info
    name: str = Field(sa_column=Column(String, nullable=False))
    race: Optional[str] = Field(default=None, sa_column=Column(String))
    char_class: Optional[str] = Field(default=None, sa_column=Column(String))
    subclass: Optional[str] = Field(default=None, sa_column=Column(String))
    level: int = Field(default=1)
    xp: int = Field(default=0)

    # Coin purse (SRD denominations) + downtime lifestyle tier.
    cp: int = Field(default=0)
    sp: int = Field(default=0)
    ep: int = Field(default=0)
    gp: int = Field(default=0)
    pp: int = Field(default=0)
    lifestyle: str = Field(default="modest", sa_column=Column(String))

    # Survival state: HP pool, Hit Dice, death saves, exhaustion, provisions.
    max_hp: int = Field(default=0)
    current_hp: int = Field(default=0)
    hit_die: str = Field(default="d8", sa_column=Column(String))
    hit_dice_total: int = Field(default=1)
    hit_dice_remaining: int = Field(default=1)
    exhaustion: int = Field(default=0)
    rations: int = Field(default=0)
    water: int = Field(default=0)
    days_without_food: int = Field(default=0)
    days_without_water: int = Field(default=0)
    death_save_successes: int = Field(default=0)
    death_save_failures: int = Field(default=0)
    stable: bool = Field(default=True)
    inspiration: bool = Field(default=False)

    # Flexible JSON fields for spells/inventory/stats
    tags: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    stats: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    spells: Optional[Any] = Field(default=None, sa_column=Column(JSON))
    inventory: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Source/sync
    ddb_url: Optional[str] = Field(default=None, sa_column=Column(String))
    avrae_import_text: Optional[str] = Field(default=None, sa_column=Column(String))
    last_verified_at: Optional[datetime] = Field(default=None)
    approved: bool = Field(default=False, index=True)

    # Game metadata
    home_region: Optional[str] = Field(default=None, sa_column=Column(String))
    notes: Optional[str] = Field(default=None, sa_column=Column(String))

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ----- Avrae hook parsing -----

AVRAE_PATTERN = re.compile(r"\[\[AVRAE:(.+?)\]\]")

def render_avrae_hooks(text: str) -> str:
    """
    Replace [[AVRAE:!command here]] hooks with human-friendly instructions
    that tell players what to type with Avrae.
    """
    def repl(match: re.Match) -> str:
        cmd = match.group(1).strip()
        # You can tweak this phrasing to taste
        return f"\n\n_(To resolve this, use Avrae and type:_ `{cmd}`_)"

    return AVRAE_PATTERN.sub(repl, text)


ROLL_HOOK_PATTERN = re.compile(r"\[\[ROLL:(.+?)\]\]", re.IGNORECASE)
_D20_EXPR = re.compile(r"^1?d20([+-]\d+)?$", re.IGNORECASE)


def resolve_roll_hooks(text: str) -> str:
    """Replace [[ROLL: expr | label | DC n]] hooks with rolled results inline.

    The DM model requests rolls; the backend rolls them with the internal dice
    engine and substitutes the outcome, so the game stays in a single voice.
    """
    def repl(match: re.Match) -> str:
        inner = match.group(1).strip()
        parts = [p.strip() for p in inner.split("|")]
        expr = parts[0] if parts else ""
        label = parts[1] if len(parts) > 1 else ""
        dc = None
        if len(parts) > 2:
            dcm = re.search(r"\d+", parts[2])
            if dcm:
                dc = int(dcm.group())
        try:
            compact = expr.replace(" ", "")
            m = _D20_EXPR.match(compact)
            if m and dc is not None:
                mod = int(m.group(1)) if m.group(1) else 0
                res = ability_check(mod, dc=dc, label=label)
                return f"\U0001F3B2 {res.detail}"
            r = dice_roll(expr)
            lbl = f"{label}: " if label else ""
            return f"\U0001F3B2 {lbl}{r.detail}"
        except Exception as e:
            print(f"[roll hook error] {e} in '{inner}'")
            return match.group(0)

    return ROLL_HOOK_PATTERN.sub(repl, text)


MUSIC_HOOK_PATTERN = re.compile(r"\[\[MUSIC:(.+?)\]\]", re.IGNORECASE)


def extract_music_cue(text: str) -> tuple[str, Optional[str]]:
    """Pull the DM's ambient-music recommendation out of the narration.

    The model emits ``[[MUSIC: keywords]]`` when the scene's mood/location
    changes. We strip the hook from what the player sees and return the search
    query so the bot can play a matching background track. Returns
    ``(clean_text, query_or_None)``; if several cues appear, the last one wins.
    """
    queries = [m.group(1).strip() for m in MUSIC_HOOK_PATTERN.finditer(text)]
    clean = MUSIC_HOOK_PATTERN.sub("", text)
    # Collapse blank lines left behind by a hook that sat on its own line.
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    query = queries[-1] if queries else None
    return clean, query


# The DM asks for a scene picture:  [[IMAGE: kind | subject | context | look]]
#   kind    = place | npc | creature | item
#   subject = what it is ("dire wolf", "Jim the blacksmith", "Greenfields")
#   context = environment/situation ("desert at dusk", "town in winter"); optional
#   look    = intrinsic appearance details for the prompt; optional
IMAGE_HOOK_PATTERN = re.compile(r"\[\[IMAGE:(.+?)\]\]", re.IGNORECASE)
# A permanent appearance change / removal: wipe a subject's stored pictures.
#   [[IMAGE-RESET: kind | subject | reason]]
IMAGE_RESET_PATTERN = re.compile(r"\[\[IMAGE-RESET:(.+?)\]\]", re.IGNORECASE)


def _split_hook(inner: str) -> list[str]:
    return [p.strip() for p in inner.split("|")]


def extract_image_hooks(text: str) -> tuple[str, list[dict], list[dict]]:
    """Pull image + image-reset hooks out of the narration.

    Returns ``(clean_text, image_requests, reset_requests)``. Both request lists
    are dicts; the hooks are stripped from what the player sees.
    """
    images: list[dict] = []
    for m in IMAGE_HOOK_PATTERN.finditer(text):
        parts = _split_hook(m.group(1))
        if not parts or not parts[0]:
            continue
        images.append({
            "kind": parts[0] if len(parts) > 0 else "creature",
            "subject": parts[1] if len(parts) > 1 else "",
            "context": parts[2] if len(parts) > 2 else "",
            "look": parts[3] if len(parts) > 3 else "",
        })

    resets: list[dict] = []
    for m in IMAGE_RESET_PATTERN.finditer(text):
        parts = _split_hook(m.group(1))
        resets.append({
            "kind": parts[0] if len(parts) > 0 else "",
            "subject": parts[1] if len(parts) > 1 else "",
            "reason": parts[2] if len(parts) > 2 else "",
        })

    clean = IMAGE_RESET_PATTERN.sub("", IMAGE_HOOK_PATTERN.sub("", text))
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, images, resets


def process_image_hooks(image_reqs: list[dict], reset_reqs: list[dict]) -> list[dict]:
    """Apply image-reset purges and render/reuse pictures for image requests.

    Returns a list of transport payloads (base64 image + metadata) for the bot,
    capped by ``ImageryConfig.max_images_per_reply``. Offline results are skipped
    so nothing broken is shown to players.
    """
    cfg = get_config().imagery
    if not cfg.enabled:
        return []

    # Permanent changes first: wipe outdated art so any re-render is fresh.
    for rq in reset_reqs:
        subject = rq.get("subject") or ""
        if not subject:
            continue
        try:
            removed = image_store.invalidate_subject(rq.get("kind") or "creature", subject)
            if removed:
                print(f"[imagery] reset '{subject}' -> purged {removed} image(s)")
        except Exception as e:
            print(f"[imagery] reset error for '{subject}': {e}")

    payloads: list[dict] = []
    for rq in image_reqs[: max(0, cfg.max_images_per_reply)]:
        subject = rq.get("subject") or ""
        if not subject:
            continue
        try:
            result = image_store.ensure_image(
                rq.get("kind") or "creature", subject,
                look=rq.get("look") or "", context=rq.get("context") or "",
            )
        except Exception as e:
            print(f"[imagery] generation error for '{subject}': {e}")
            continue
        if result is None or result.offline:
            continue
        payloads.append(result.payload())
    return payloads


# ----- OpenRouter LLM call -----

def call_openrouter_chat(
    messages: List[Dict[str, str]],
    *,
    max_tokens: Optional[int] = None,
    timeout_seconds: int = 60,
) -> str:
    """Call OpenRouter's chat completion endpoint with the given messages."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        # Optional metadata:
        "HTTP-Referer": "http://localhost",
        "X-Title": "Oracle DM",
    }

    payload = {
        "model": OPENROUTER_MODEL,
        "messages": messages,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    resp = requests.post(OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=timeout_seconds)
    if resp.status_code != 200:
        print(f"[OpenRouter error] HTTP {resp.status_code}: {resp.text}")
        raise RuntimeError("LLM call failed")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[OpenRouter parse error] {e} | data={data}")
        raise RuntimeError("Failed to parse LLM response")


def call_openrouter_dm(messages: List[Dict[str, str]]) -> str:
    """Call OpenRouter for live DM narration."""
    return call_openrouter_chat(messages)


# ----- "DM brain" using OpenRouter + Avrae hooks -----

def generate_dm_reply(
    session_id: str,
    username: str,
    message: str,
    extra_context: Optional[List[str]] = None,
) -> str:
    """DM brain via OpenRouter, grounded in world state + SRD rules.

    ``extra_context`` is a list of ready-to-inject text blocks (world slice,
    rules briefs) produced by ``assemble_context``.
    """

    state = _load_session_state(session_id)
    history = list(state.get("recent_turns", []))

    messages: List[Dict[str, str]] = []

    system_prompt = (
        "You are an imaginative, fair Dungeon Master for a 5e-style tabletop RPG. "
        "You narrate the world, voice NPCs, and adjudicate the outcomes of actions.\n\n"
        "Tone & style:\n"
        "- Grounded, evocative fantasy; fun and playable.\n"
        "- 2-4 short paragraphs, not a novel.\n"
        "- End most responses by asking what the player does next.\n\n"
        "Using provided context:\n"
        "- A 'World state' block and/or a 'Rules reference' block may be supplied.\n"
        "- Treat them as ground truth. Keep NPCs, places, and facts consistent with them.\n"
        "- Use the EXACT numbers in the Rules reference for monster stats and spells.\n"
        "- A 'Combat' block may list the initiative order with each creature's current\n"
        "  HP, AC, and conditions. When present, respect the turn order and those HP\n"
        "  totals; narrate the fight around them and don't contradict the numbers. Apply\n"
        "  each listed condition's effects (see the conditions rules below).\n"
        "- A 'Character resources' block may show the PC's coin purse, lifestyle, level,\n"
        "  and bastion. Respect their wealth: don't hand out or deduct coin the block\n"
        "  doesn't support, and price goods sensibly against their purse.\n"
        "- That block ends with an 'Inventory:' line listing exactly what the PC carries.\n"
        "  Treat it as the truth about their gear. Before letting the player attack with a\n"
        "  weapon, drink a potion, read a scroll, or otherwise use an item, CHECK that it\n"
        "  appears in their inventory. If they try to use something they don't have (e.g.\n"
        "  'I swing my sword' with no sword listed), don't allow it: gently point out they\n"
        "  aren't carrying it and ask what they actually do (improvise, use fists, draw a\n"
        "  different listed weapon, etc.). Natural, always-available actions (unarmed\n"
        "  strikes, shoving, spells they know) are fine without an inventory entry.\n"
        "- A 'Physical limits' line gives the PC's speed, jump distances, lift/carry, and\n"
        "  reach WITHOUT magic or special items. Enforce these as hard limits:\n"
        "  * Movement: a creature can move up to its Speed on its turn (double if it Dashes,\n"
        "    using its action). It cannot cross more distance than that in one turn, and\n"
        "    climbing, swimming, or crawling through difficult terrain costs double movement.\n"
        "  * Jumping: a long jump clears at most the listed feet (a running start is needed\n"
        "    for the full distance; only half without ~10 ft of runway). A high jump reaches\n"
        "    only the listed height. Don't let a PC leap onto a rooftop, chasm, or wall that\n"
        "    exceeds these numbers unless they have a relevant spell, item, or class feature.\n"
        "  * Lifting/carrying/forcing: a PC can't lift, drag, or hurl objects beyond their\n"
        "    push/drag/lift limit, and hauling near capacity slows them.\n"
        "  * Reach: melee reach is 5 ft (10 ft only with a reach weapon); they can't strike\n"
        "    or grab something farther away.\n"
        "  When a player attempts a physical feat NEAR the edge of these limits, call for an\n"
        "  ability check (usually Strength (Athletics) or Dexterity (Acrobatics)) at a DC that\n"
        "  reflects the difficulty via a [[ROLL]] hook. When an attempt EXCEEDS what is\n"
        "  physically possible without augmentation, don't allow it — explain the limit in the\n"
        "  fiction (the ledge is simply too high) and invite a feasible alternative (find a\n"
        "  ladder, take the stairs, cast a spell, grapple up in stages). Also keep other\n"
        "  physics honest: unsupported creatures fall (~3d6 per 10 ft, capped) and take time\n"
        "  to act; a character can't be two places at once, act while unconscious, or use a\n"
        "  reaction they've already spent. Reward clever, plausible plans; refuse the\n"
        "  impossible.\n"
        "- Conditions bind EVERY creature — the PC, NPCs, and monsters alike. Before you let\n"
        "  anyone act (or resolve an action against them), check their conditions (the Combat\n"
        "  block lists them in a fight; otherwise track any you've narrated) and honor the\n"
        "  mechanical effects:\n"
        "  * Prone: attacks at disadvantage; melee attackers against it have advantage,\n"
        "    ranged have disadvantage; standing up costs half its movement.\n"
        "  * Grappled / Restrained: Speed becomes 0. Restrained also = attacks at disadvantage,\n"
        "    attacks against it at advantage, Dex saves at disadvantage.\n"
        "  * Incapacitated: no actions, bonus actions, or reactions at all.\n"
        "  * Stunned: incapacitated, can't move, auto-fails Str/Dex saves, attacks against it\n"
        "    have advantage. Paralyzed / Unconscious: as stunned, and any hit from within 5 ft\n"
        "    is a critical hit (unconscious also drops what it holds and falls prone).\n"
        "  * Petrified: incapacitated, unaware, resistant to all damage, immune to poison/disease.\n"
        "  * Blinded: can't see, auto-fails sight checks, attacks at disadvantage and attacks\n"
        "    against it at advantage. Deafened: can't hear, auto-fails hearing checks.\n"
        "  * Poisoned: disadvantage on attack rolls and ability checks.\n"
        "  * Frightened: disadvantage on checks and attacks while the source is in sight, and it\n"
        "    can't willingly move closer to the source.\n"
        "  * Charmed: can't attack the charmer or target them with harmful effects; the charmer\n"
        "    has advantage on social checks with it.\n"
        "  * Invisible: can't be seen without special senses (heavily obscured for locating);\n"
        "    attacks at advantage, attacks against it at disadvantage.\n"
        "  Don't let a stunned, paralyzed, or unconscious creature take actions, a grappled one\n"
        "  walk away, or a blinded one make a clean ranged shot. When a condition ends or a save\n"
        "  applies, resolve it with a [[ROLL]] hook rather than assuming the outcome.\n"
        "- That block may also show survival state: current/max HP, Hit Dice, exhaustion,\n"
        "  rations and water, active afflictions (diseases/madness), current weather and\n"
        "  environmental hazards, and faction reputation. Treat these as ground truth:\n"
        "  reflect a wounded, exhausted, hungry, diseased, or well-regarded character in\n"
        "  your narration, and let harsh weather and hazards matter. Do NOT invent HP or\n"
        "  resource changes yourself — describe the fiction and emit roll hooks; the\n"
        "  system applies mechanical changes.\n\n"
        "Dice - you roll them yourself; NEVER ask the player to roll or mention Avrae:\n"
        "- When an action needs a roll, emit a hook and the system fills in the result:\n"
        "    [[ROLL: 1d20+5 | Stealth | DC 15]]   for an ability check or saving throw\n"
        "    [[ROLL: 2d6+3 | Greataxe damage]]     for damage or a generic roll\n"
        "- Put ONLY the hook (never invent a result). The roller substitutes the outcome inline.\n"
        "\n"
        "Ambient music - set the mood, at most ONE cue per reply:\n"
        "- When the scene's location or mood changes (entering a new area, combat begins, a\n"
        "  hushed or tense moment), emit a single hook that the system uses to play matching\n"
        "  background music:\n"
        "    [[MUSIC: dark dungeon tension]]   or   [[MUSIC: lively medieval tavern]]\n"
        "- Use 3-6 evocative keywords (place + mood); imagine instrumental/ambient scoring.\n"
        "- Emit it ONLY when the ambiance meaningfully changes; otherwise omit it entirely.\n"
        "- Put the hook on its own line. It is removed from what the player sees.\n"
    )

    # Scene-imagery hook guidance (config-toggleable, only when imagery is on).
    _img_cfg = get_config().imagery
    if _img_cfg.enabled and _img_cfg.inject_hook_guidance:
        system_prompt += (
            "\nScene pictures - visualize notable new sights, sparingly:\n"
            "- The FIRST time the party clearly sees a notable new place, NPC, or creature,\n"
            "  emit ONE hook so the system can show an illustration:\n"
            "    [[IMAGE: creature | dire wolf | snowy pass at dusk | lean, scarred, pale fur]]\n"
            "    [[IMAGE: npc | Jim the blacksmith | forge in town | burly, soot-stained, graying beard]]\n"
            "    [[IMAGE: place | Greenfields | autumn morning | rolling farmland, timber cottages]]\n"
            "- Fields: kind (place|npc|creature|item) | subject | context (environment/season/mood) | look.\n"
            "- The SAME subject in a different environment (a desert wolf vs a jungle wolf, an NPC in\n"
            "  town vs in the desert) is a distinct picture: keep 'subject' stable, vary 'context'.\n"
            "- If a subject's appearance changes PERMANENTLY (an NPC is maimed, a town burns down),\n"
            "  emit [[IMAGE-RESET: kind | subject | reason]] so outdated pictures are cleared.\n"
            f"- At most {_img_cfg.max_images_per_reply} image hook(s) per reply. Put each on its own line;\n"
            "  hooks are removed from what the player sees.\n"
        )

    messages.append({"role": "system", "content": system_prompt})

    summary_block = _session_summary_block(session_id)
    if summary_block:
        messages.append({"role": "system", "content": summary_block})

    # Self-authored DM best-practice guidance (config-toggleable).
    try:
        _guidance = guidance_block()
        if _guidance:
            messages.append({"role": "system", "content": _guidance})
    except Exception:
        pass

    # Grounding context (world slice, rules briefs).
    for block in (extra_context or []):
        if block and block.strip():
            messages.append({"role": "system", "content": block})

    # Previous turns
    recent_limit = max(1, get_config().session_memory.recent_turns)
    for turn in history[-recent_limit:]:
        if turn["role"] == "player":
            messages.append({
                "role": "user",
                "content": f"{turn['user']}: {turn['content']}",
            })
        else:
            messages.append({
                "role": "assistant",
                "content": turn["content"],
            })

    # New user message
    messages.append({
        "role": "user",
        "content": f"{username}: {message}",
    })

    dm_raw = call_openrouter_dm(messages)

    # Resolve internal dice hooks ([[ROLL:...]]) inline using the dice roller.
    return resolve_roll_hooks(dm_raw)


def _resolve_session_character(session_id: str, user_id: str) -> Optional[int]:
    """Bind a character to a session when meta lacks one, by the player's user id.

    Picks the player's most recently created character and records its id (plus
    pc_slug when available) into the session meta so subsequent turns are cheap.
    Returns the character id, or ``None`` if the player has no character.
    """
    if not user_id:
        return None
    with Session(engine) as session:
        char = session.exec(
            select(Character)
            .where(Character.discord_user_id == user_id)
            .order_by(Character.id.desc())
        ).first()
    if not char:
        return None
    state = _load_session_state(session_id)
    meta_update: Dict[str, Any] = dict(state.get("meta", {}) or {})
    meta_update["character_id"] = char.id
    meta_update.setdefault("user_id", user_id)
    _set_session_meta(session_id, meta_update)
    return char.id


def assemble_context(session_id: str, message: str, user_id: Optional[str] = None):

    """Build grounding context for a turn: the local world slice + referenced rules.

    Returns ``(world_context_or_None, [text_blocks])``.
    """
    ctx_obj = None
    texts: List[str] = []

    state = _load_session_state(session_id)
    meta = state.get("meta", {})

    # If this session has no character bound (e.g. the in-game session_id differs
    # from the one enterworld created), fall back to the player's character so the
    # resource/inventory block and equipment guardrails still apply.
    if (not meta or not meta.get("character_id")) and user_id:
        resolved = _resolve_session_character(session_id, user_id)
        if resolved:
            state = _load_session_state(session_id)
            meta = state.get("meta", {})
    if meta and meta.get("pc_slug"):
        try:
            ctx_obj = world.get_world_context(meta["pc_slug"], message)
            rendered = ctx_obj.render()
            if rendered.strip():
                texts.append(rendered)
        except Exception as e:
            print(f"[world context error] {e}")

    # Inject exact stats for any monster/spell named in the action or last narration.
    try:
        scan = message
        hist = list(state.get("recent_turns", []))
        if hist and hist[-1]["role"] == "dm":
            scan = f"{message}\n{hist[-1]['content']}"
        mentions = rules_lib.find_mentions(scan, limit=6)
        if mentions:
            briefs = [
                format_monster_brief(obj) if kind == "monster" else format_spell_brief(obj)
                for kind, obj in mentions
            ]
            texts.append("# Rules reference (exact numbers)\n\n" + "\n\n".join(briefs))
    except Exception as e:
        print(f"[rules mention error] {e}")

    # Character resources: coin purse, lifestyle, level/XP, and any bastion.
    if meta and meta.get("character_id"):
        try:
            texts.append(_character_resource_block(meta["character_id"]))
        except Exception as e:
            print(f"[resource context error] {e}")

    return ctx_obj, texts


def _region_climate(home_region: Optional[str]) -> str:
    """Best-effort climate for a named region (defaults to temperate)."""
    if not home_region:
        return "temperate"
    r = home_region.lower()
    if any(k in r for k in ("frost", "ice", "north", "tundra", "glacier", "winter")):
        return "arctic"
    if any(k in r for k in ("desert", "sand", "dune", "waste", "scorch")):
        return "desert"
    if any(k in r for k in ("coast", "harbor", "harbour", "port", "shore", "bay")):
        return "coastal"
    if any(k in r for k in ("jungle", "tropic", "rainforest", "marsh")):
        return "tropical"
    if any(k in r for k in ("mountain", "peak", "highland", "crag", "summit")):
        return "mountain"
    return "temperate"


def _inventory_items(char: Character) -> List[Dict[str, Any]]:
    """Normalize the character's JSON inventory into a list of item dicts."""
    items: List[Dict[str, Any]] = []
    for raw in (char.inventory or []):
        if isinstance(raw, str):
            items.append({"name": raw, "quantity": 1})
        elif isinstance(raw, dict):
            name = raw.get("name") or raw.get("item") or "Unknown item"
            item = dict(raw)
            item["name"] = name
            item.setdefault("quantity", raw.get("qty", 1) or 1)
            items.append(item)
    return items


def _format_inventory(char: Character) -> Dict[str, Any]:
    """Structured inventory payload rendered from the character's stored items."""
    items = _inventory_items(char)
    lines: List[str] = []
    for it in items:
        qty = it.get("quantity", 1) or 1
        name = it.get("name", "Unknown item")
        parts = [f"{qty}x {name}" if qty and qty != 1 else name]
        extras = []
        if it.get("equipped"):
            extras.append("equipped")
        if it.get("weight"):
            try:
                extras.append(f"{float(it['weight']):g} lb")
            except (TypeError, ValueError):
                pass
        if it.get("notes"):
            extras.append(str(it["notes"]))
        if extras:
            parts.append(f"({', '.join(extras)})")
        lines.append(" ".join(parts))
    purse = {"cp": char.cp, "sp": char.sp, "ep": char.ep, "gp": char.gp, "pp": char.pp}
    return {
        "character_id": char.id,
        "name": char.name,
        "items": items,
        "lines": lines,
        "carried_weight": round(_carried_weight(char), 2),
        "purse": purse,
        "purse_text": format_purse(purse),
    }


def _equipment_summary(char: Character) -> str:
    """One-line 'what the player is carrying/wielding' summary for the DM prompt."""
    items = _inventory_items(char)
    if not items:
        return "Inventory: (empty — the player carries no listed gear)"
    names = []
    for it in items:
        qty = it.get("quantity", 1) or 1
        nm = it.get("name", "item")
        names.append(f"{qty}x {nm}" if qty and qty != 1 else nm)
    return "Inventory: " + ", ".join(names)


# Base walking speed by race (ft). Small races and dwarves move 25; a few move
# faster. Anything unlisted defaults to 30. Matches 5e defaults.
_RACE_SPEEDS = {
    "dwarf": 25, "hill dwarf": 25, "mountain dwarf": 25, "duergar": 25,
    "halfling": 25, "lightfoot halfling": 25, "stout halfling": 25,
    "gnome": 25, "forest gnome": 25, "rock gnome": 25, "deep gnome": 25,
    "wood elf": 35, "tabaxi": 30, "aarakocra": 25, "centaur": 40,
}


def _ability_score(char: Character, *names: str) -> int:
    """Raw ability score (defaults to 10) from the character's stats blob."""
    stats = char.stats or {}
    for n in names:
        for key in (n, n[:3], n.upper(), n[:3].upper(), n.capitalize()):
            if key in stats and stats[key] is not None:
                try:
                    return int(stats[key])
                except (TypeError, ValueError):
                    return 10
    return 10


def _base_walk_speed(char: Character) -> int:
    race = (char.race or "").strip().lower()
    if race in _RACE_SPEEDS:
        return _RACE_SPEEDS[race]
    # Fuzzy match (e.g. "High Elf" contains no listed key -> default 30).
    for key, spd in _RACE_SPEEDS.items():
        if key in race:
            return spd
    return 30


def _exhaustion_speed_multiplier(level: Optional[int]) -> float:
    lvl = int(level or 0)
    if lvl >= 5:
        return 0.0
    if lvl >= 2:
        return 0.5
    return 1.0


def _physical_capabilities(char: Character) -> Dict[str, Any]:
    """Derive movement, jump, and lift limits so the DM can gate physical feats.

    Values follow 5e defaults: jumps scale off Strength, carrying and push/drag/
    lift scale off the Strength SCORE, and speed is reduced by encumbrance and
    exhaustion. These are what a PC can do WITHOUT magic or special items.
    """
    str_score = _ability_score(char, "strength")
    str_mod = ability_modifier(str_score)

    speed = _base_walk_speed(char)
    notes: List[str] = []

    # Encumbrance speed penalty (only when the variant is enabled in config).
    try:
        enc = encumbrance_status(str_score, _carried_weight(char))
        pen = int(enc.get("speed_penalty_ft", 0) or 0)
        if pen:
            speed = max(0, speed - pen)
            notes.append(enc.get("note", ""))
        if enc.get("over_capacity") or enc.get("status") == "overloaded":
            notes.append("Over carrying capacity — movement severely limited.")
    except Exception:
        pass

    # Exhaustion speed effect (halved at 2, zero at 5).
    mult = _exhaustion_speed_multiplier(char.exhaustion)
    if mult < 1.0:
        speed = int(speed * mult)
        notes.append("Exhaustion reduces speed.")

    long_jump_run = str_score
    high_jump_run = max(0, 3 + str_mod)
    return {
        "walk_speed_ft": speed,
        "run_speed_ft": speed * 2,           # Dash action doubles movement
        "climb_swim_speed_ft": speed // 2,   # costs double movement w/o a speed
        "long_jump_running_ft": long_jump_run,
        "long_jump_standing_ft": long_jump_run // 2,
        "high_jump_running_ft": high_jump_run,
        "high_jump_standing_ft": high_jump_run // 2,
        "carrying_capacity_lb": str_score * 15,
        "push_drag_lift_lb": str_score * 30,
        "max_reach_ft": 5,                   # Medium reach without a reach weapon
        "notes": [n for n in notes if n],
    }


def _physical_capabilities_summary(char: Character) -> str:
    """Compact 'what the body can do' block for the DM prompt (physics guardrails)."""
    cap = _physical_capabilities(char)
    parts = [
        f"Speed {cap['walk_speed_ft']} ft (Dash {cap['run_speed_ft']} ft; "
        f"climb/swim {cap['climb_swim_speed_ft']} ft)",
        f"Jump: long {cap['long_jump_running_ft']} ft running / "
        f"{cap['long_jump_standing_ft']} ft standing, high "
        f"{cap['high_jump_running_ft']} ft running / "
        f"{cap['high_jump_standing_ft']} ft standing",
        f"Lift/carry: {cap['carrying_capacity_lb']} lb capacity, "
        f"{cap['push_drag_lift_lb']} lb push/drag/lift",
        f"Reach {cap['max_reach_ft']} ft",
    ]
    line = "Physical limits (no magic/items): " + "; ".join(parts) + "."
    if cap["notes"]:
        line += " " + " ".join(cap["notes"])
    return line


def _portrait_base_look(char: Character) -> str:
    """A short appearance seed (race/class/subclass) to anchor a PC portrait."""
    parts: List[str] = []
    if char.race:
        parts.append(str(char.race))
    if char.char_class:
        cls = str(char.char_class)
        if char.subclass:
            cls = f"{char.subclass} {cls}"
        parts.append(cls)
    return ", ".join(parts)


def _build_character_sheet(char: Character) -> Dict[str, Any]:
    """Render a D&D-Beyond-style character sheet from stored data (no AI)."""
    stats = char.stats or {}
    abil_order = ["strength", "dexterity", "constitution",
                  "intelligence", "wisdom", "charisma"]
    pb = proficiency_bonus_for_level(char.level)
    abilities: Dict[str, Any] = {}
    for a in abil_order:
        score = None
        for key in (a, a[:3], a.capitalize(), a[:3].capitalize(), a.upper()):
            if key in stats and stats[key] is not None:
                score = stats[key]
                break
        if score is None:
            score = 10
        mod = ability_modifier(score)
        abilities[a] = {
            "score": score,
            "modifier": mod,
            "modifier_text": f"{mod:+d}",
        }
    purse = {"cp": char.cp, "sp": char.sp, "ep": char.ep, "gp": char.gp, "pp": char.pp}
    inv = _format_inventory(char)
    return {
        "id": char.id,
        "name": char.name,
        "race": char.race,
        "char_class": char.char_class,
        "subclass": char.subclass,
        "level": char.level,
        "xp": char.xp,
        "proficiency_bonus": pb,
        "abilities": abilities,
        "combat": {
            "current_hp": char.current_hp,
            "max_hp": char.max_hp,
            "hit_die": char.hit_die,
            "hit_dice_remaining": char.hit_dice_remaining,
            "hit_dice_total": char.hit_dice_total,
            "exhaustion": char.exhaustion,
            "inspiration": bool(char.inspiration),
            "passive_perception": _passive_score(char, "wisdom"),
        },
        "physical": _physical_capabilities(char),
        "purse": purse,
        "purse_text": format_purse(purse),
        "spells": char.spells or [],
        "inventory": inv["items"],
        "inventory_lines": inv["lines"],
        "carried_weight": inv["carried_weight"],
        "home_region": char.home_region,
        "notes": char.notes,
    }


def _character_resource_block(character_id: int) -> str:
    """A compact 'Character resources' block for the DM prompt."""
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            return ""
        purse = {"cp": char.cp, "sp": char.sp, "ep": char.ep, "gp": char.gp, "pp": char.pp}
        lines = ["# Character resources"]
        prog = get_config().progression
        lvl_line = f"Level {char.level}"
        if not prog.milestone_leveling:
            lvl_line += f" ({char.xp} XP)"
        lines.append(lvl_line)
        lines.append(f"Purse: {format_purse(purse)} (~{gp_value(purse):g} gp)")
        lines.append(f"Lifestyle: {char.lifestyle}")

        # Survival state (HP, exhaustion, provisions) when enabled.
        cfg = get_config()
        if cfg.survival.enabled:
            hp_line = f"HP: {char.current_hp}/{char.max_hp}"
            if char.current_hp <= 0 and not char.stable:
                hp_line += (f" — DYING (death saves "
                            f"{char.death_save_successes}✓/{char.death_save_failures}✗)")
            elif char.current_hp <= 0:
                hp_line += " — unconscious but stable"
            lines.append(hp_line)
            lines.append(
                f"Hit Dice: {char.hit_dice_remaining}/{char.hit_dice_total}{char.hit_die}")
            if char.exhaustion:
                lines.append(f"Exhaustion: {describe_exhaustion(char.exhaustion)}")
            lines.append(f"Provisions: {char.rations} rations, {char.water} water")
            if char.inspiration:
                lines.append("Has Inspiration.")

            # Current weather at the PC's region.
            try:
                day = world.current_day()
                month = ((day // 30) % 12) + 1
                climate = _region_climate(char.home_region)
                weather = generate_weather(day, climate=climate, month=month)
                lines.append(f"Weather: {weather['summary']}")
                tags = active_hazard_tags(weather)
                if tags:
                    lines.append("Environmental hazards: " + ", ".join(tags))
            except Exception as e:
                print(f"[weather block error] {e}")

        # Active afflictions (diseases / madness).
        if cfg.hazard.enabled:
            afflictions = session.exec(
                select(Affliction).where(
                    Affliction.character_id == character_id,
                    Affliction.active == True,  # noqa: E712
                )
            ).all()
            if afflictions:
                names = ", ".join(
                    f"{a.name}" + (f" ({a.severity})" if a.severity else "")
                    for a in afflictions
                )
                lines.append(f"Afflictions: {names}")

        # Faction reputation.
        if cfg.reputation.enabled:
            reps = session.exec(
                select(Reputation).where(Reputation.character_id == character_id)
            ).all()
            if reps:
                parts = [
                    f"{r.faction_name}: {describe_standing(r.renown)['standing']} ({r.renown})"
                    for r in reps
                ]
                lines.append("Reputation: " + "; ".join(parts))

        bastion = session.exec(
            select(Bastion).where(Bastion.character_id == character_id)
        ).first()
        if bastion:
            facs = session.exec(
                select(FacilityInstance).where(FacilityInstance.bastion_id == bastion.id)
            ).all()
            fac_names = ", ".join(f.name for f in facs) or "no special facilities yet"
            lines.append(
                f"Bastion: {bastion.name} (turn {bastion.turns_taken}) — {fac_names}"
            )

        # Carried equipment — the DM must check this before letting the player
        # use a weapon/item/tool they don't actually have.
        lines.append(_equipment_summary(char))

        # Physical limits — movement, jumps, lifting, reach — so the DM can gate
        # feats of athletics that are impossible without magic or special items.
        lines.append(_physical_capabilities_summary(char))
        return "\n".join(lines)


# ----- Routes -----

@app.get("/")
async def root():
    return {"status": "ok", "message": "Oracle DM backend with OpenRouter + Avrae hooks running"}


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest, background_tasks: BackgroundTasks):
    """
    Main entry point from the Discord bot.
    Tracks per-session history and asks the DM brain for a reply.
    """

    _load_session_state(req.session_id)

    # Player turn
    state = _append_turn(req.session_id, Turn(role="player", user=req.username, content=req.message))

    # DM reply
    try:
        # Ground the turn in the local world slice, referenced rules, and — if a
        # fight is underway — the live initiative/HP board.
        _, ctx_texts = assemble_context(req.session_id, req.message, user_id=req.user_id)
        active_enc = combat.get_active(req.session_id)
        if active_enc:
            board = combat.render(active_enc.id)
            if board.strip():
                ctx_texts.append(board)
        dm_text = generate_dm_reply(
            session_id=req.session_id,
            username=req.username,
            message=req.message,
            extra_context=ctx_texts,
        )
    except Exception as e:
        print(f"[DM error] {e}")
        dm_text = (
            "⚠ The Oracle strains to speak, but something interrupts its vision. "
            "Try again in a moment."
        )

    # Separate the AI's ambient-music recommendation from the narration.
    dm_text, music_query = extract_music_cue(dm_text)

    # Pull out scene-image requests (and any permanent-change resets), then
    # render/reuse pictures for the bot to attach.
    dm_text, image_reqs, reset_reqs = extract_image_hooks(dm_text)
    try:
        image_payloads = process_image_hooks(image_reqs, reset_reqs)
    except Exception as e:
        print(f"[imagery] hook processing failed: {e}")
        image_payloads = []

    # Store DM turn
    state = _append_turn(req.session_id, Turn(role="dm", user="Oracle DM", content=dm_text))
    if len(state.get("recent_turns", [])) > get_config().session_memory.compaction_threshold:
        _schedule_session_compaction(background_tasks, req.session_id)

    return ChatResponse(reply=dm_text, music=music_query,
                        images=image_payloads or None)

@app.post("/reset")
async def reset_endpoint(req: ResetRequest):
    """
    Reset the story/session for a given session_id (guild:channel).
    Called by the Discord bot when !resetdm is used.
    """
    with Session(engine) as db:
        row = db.get(SessionMemory, req.session_id)
        if row is not None:
            db.delete(row)
            db.commit()
    SESSION_STATE_CACHE.pop(req.session_id, None)
    SESSIONS.pop(req.session_id, None)
    SESSION_META.pop(req.session_id, None)
    print(f"[SESSION RESET] {req.session_id}")
    return {"status": "ok", "message": f"Session {req.session_id} reset."}


@app.post("/enterworld", response_model=EnterResponse)
async def enter_world(req: EnterRequest):
    """
    Entry point for players to "enter the world".
    - If the player has no characters registered, returns status 'no_character' with guidance.
    - If the player has characters, create a new session_id, seed the session, generate an intro via the DM brain, and return session info.
    """
    # Look up characters for this user (persisted DB)
    with Session(engine) as session:
        statement = select(Character).where(Character.discord_user_id == req.user_id)
        chars = session.exec(statement).all()

    if not chars:
        return EnterResponse(
            status="no_character",
            message=(
                "No characters found for your account. To create one, open a DM with the bot and follow the character creation flow. "
                "You can also use Avrae's `!import` in Discord to import a character, then tell the Oracle to `!enterworld` again."
            ),
            characters=[],
        )

    # Choose the requested character (or first one)
    chosen = None
    if req.character_name:
        for c in chars:
            if c.name and c.name.lower() == req.character_name.lower():
                chosen = c
                break
    if not chosen:
        chosen = chars[0]

    # Create a new session id (guild-based namespace)
    session_id = f"{req.guild_id}:{uuid.uuid4().hex}"

    # Place the PC in the persistent world graph and remember it for this session.
    pc_slug = slugify(chosen.name)
    try:
        place_pc(
            world,
            chosen.name,
            discord_user_id=req.user_id,
            location_slug="the-silver-tankard",
            attributes={"race": chosen.race, "class": chosen.char_class, "subclass": chosen.subclass, "level": chosen.level},
        )
    except Exception as e:
        print(f"[enterworld place_pc error] {e}")
    _set_session_meta(session_id, {
        "pc_slug": pc_slug,
        "user_id": req.user_id,
        "character_name": chosen.name,
        "character_id": chosen.id,
    })

    _append_turn(session_id, Turn(role="player", user=req.username, content="ENTER_WORLD"))

    # Ask the DM brain for an opening narration, grounded in the world slice.
    arrival = "I arrive and take in my surroundings."
    _ctx_obj, ctx_texts = assemble_context(session_id, arrival)
    try:
        intro = generate_dm_reply(
            session_id=session_id, username=req.username, message=arrival, extra_context=ctx_texts,
        )
    except Exception as e:
        print(f"[enterworld DM error] {e}")
        intro = "The Oracle is silent for a moment. (failed to generate intro)"

    # Pull the opening ambient-music cue out of the intro (if the DM set one).
    intro, music_query = extract_music_cue(intro)

    # Store the DM turn in the session history
    _append_turn(session_id, Turn(role="dm", user="Oracle DM", content=intro))

    # Return a small package of data the bot can use to create a channel and start the session
    return EnterResponse(
        status="ok",
        message="Session created",
        session_id=session_id,
        intro=intro,
        world_snippet=f"{chosen.name} arrives at the edge of the {getattr(chosen, 'home_region', 'Greenfields')}",
        starting_region=getattr(chosen, 'home_region', 'Greenfields'),
        characters=[c.name for c in chars],
        music=music_query,
    )


@app.post("/register_character")
async def register_character(req: RegisterCharacterRequest):
    """Register a new character for a discord user.

    Basic validation applied (level range and unique name per owner). Approved flag can be used by admins later.
    """
    # Basic validation
    if not req.name or not req.discord_user_id:
        raise HTTPException(status_code=400, detail="Missing required fields: name and discord_user_id.")

    # Validate allowed source values
    if req.source and req.source not in ("avrae", "guided", "manual"):
        raise HTTPException(status_code=400, detail="Invalid source. Allowed: avrae, guided, manual.")

    # Level validation: characters are always CREATED at level 1. Advancement is
    # tracked in-system via the /level_up flow (SRD-based), so any import or manual
    # entry must start at level 1.
    if req.level != 1:
        raise HTTPException(
            status_code=400,
            detail="Characters must be created at level 1. Use /level_up to advance.",
        )

    # Validate stats if present
    if req.stats:
        for k, v in req.stats.items():
            if not isinstance(v, int) or v < 1 or v > 30:
                raise HTTPException(status_code=400, detail=f"Invalid stat value for {k}: {v} (must be 1-30).")

    with Session(engine) as session:
        # Check uniqueness per player
        stmt = select(Character).where(Character.discord_user_id == req.discord_user_id, Character.name == req.name)
        existing = session.exec(stmt).first()
        if existing:
            raise HTTPException(status_code=400, detail="Character name already exists for this user.")

        # Decide approval: auto-approve Avrae imports and guided creations, otherwise follow approve flag
        approved_flag = bool(req.approve) or (req.source in ("avrae", "guided"))

        # Derive starting survival state from class hit die + Constitution.
        cls_row = _get_class_row(session, req.char_class)
        hit_die = f"d{cls_row.hit_die}" if cls_row and cls_row.hit_die else "d8"
        die_faces = int(hit_die[1:])
        con_mod = ability_modifier((req.stats or {}).get("constitution")
                                   or (req.stats or {}).get("con")
                                   or (req.stats or {}).get("CON"))
        start_hp = max(1, die_faces + con_mod)
        surv = get_config().survival

        char = Character(
            discord_user_id=req.discord_user_id,
            name=req.name,
            race=req.race,
            char_class=req.char_class,
            subclass=req.subclass,
            level=req.level,
            gp=get_config().economy.starting_gold,
            stats=req.stats,
            ddb_url=req.ddb_url,
            avrae_import_text=req.avrae_import_text,
            approved=approved_flag,
            home_region=req.home_region,
            max_hp=start_hp,
            current_hp=start_hp,
            hit_die=hit_die,
            hit_dice_total=1,
            hit_dice_remaining=1,
            rations=surv.starting_rations,
            water=surv.starting_water,
        )

        session.add(char)
        session.commit()
        session.refresh(char)

    return {"status": "ok", "message": "Character registered", "character_id": char.id}


@app.post("/check_character")
async def check_character(req: CheckCharacterRequest):
    """Check if a discord user has any registered characters and return their list."""
    with Session(engine) as session:
        stmt = select(Character).where(Character.discord_user_id == req.discord_user_id)
        characters = session.exec(stmt).all()
    
    char_list = [{"id": c.id, "name": c.name, "level": c.level, "char_class": c.char_class, "subclass": c.subclass, "race": c.race} for c in characters]
    return {"has_character": len(characters) > 0, "characters": char_list}


# ----- Character progression (SRD level-up) -----

def _con_mod(char: Character) -> int:
    stats = char.stats or {}
    con = stats.get("constitution") or stats.get("con") or stats.get("CON")
    return ability_modifier(con)


def _get_class_row(session: Session, class_name: Optional[str]) -> Optional[DndClass]:
    if not class_name:
        return None
    key = class_name.strip().lower()
    row = session.exec(select(DndClass).where(DndClass.index_slug == key)).first()
    if row:
        return row
    for c in session.exec(select(DndClass)).all():
        if c.name.lower() == key:
            return c
    return None


def _subclasses_for(session: Session, cls: DndClass) -> list[Subclass]:
    rows = list(session.exec(select(Subclass).where(Subclass.class_slug == cls.index_slug)).all())
    if rows:
        return rows
    return list(session.exec(select(Subclass).where(Subclass.class_name == cls.name)).all())


def _find_subclass(session: Session, cls: DndClass, name: str) -> Optional[Subclass]:
    key = name.strip().lower()
    for sc in _subclasses_for(session, cls):
        if sc.name.lower() == key or sc.index_slug == key:
            return sc
    return None


def _progression(session: Session, char: Character, target_subclass: Optional[str], apply: bool) -> dict:
    cls = _get_class_row(session, char.char_class)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown class for character: {char.char_class!r}")

    new_level = char.level + 1
    if new_level > 20:
        raise HTTPException(status_code=400, detail="Character is already level 20 (max).")

    options = _subclasses_for(session, cls)
    option_list = [{"name": s.name, "slug": s.index_slug, "source": s.source} for s in options]

    chosen_name = char.subclass
    chosen_row: Optional[Subclass] = None
    if target_subclass:
        chosen_row = _find_subclass(session, cls, target_subclass)
        if chosen_row is None:
            raise HTTPException(status_code=400, detail=f"{target_subclass!r} is not a {cls.name} subclass.")
        if new_level < cls.subclass_level:
            raise HTTPException(
                status_code=400,
                detail=f"{cls.name} chooses its {cls.subclass_label or 'subclass'} at level {cls.subclass_level}.",
            )
        chosen_name = chosen_row.name
    elif char.subclass:
        chosen_row = _find_subclass(session, cls, char.subclass)

    features = chosen_row.features if chosen_row else None
    report = level_up_report(
        class_name=cls.name, hit_die=cls.hit_die, subclass_level=cls.subclass_level,
        subclass_name=chosen_name, subclass_features=features,
        con_mod=_con_mod(char), old_level=char.level, new_level=new_level,
    )

    subclass_required = bool(report.get("subclass_choice_due")) and not chosen_name
    # Don't advance until the required subclass choice is made.
    did_apply = apply and not subclass_required

    if did_apply:
        char.level = new_level
        if target_subclass and chosen_row:
            char.subclass = chosen_row.name
        char.updated_at = datetime.utcnow()
        session.add(char)
        session.commit()
        session.refresh(char)

    return {
        "character_id": char.id,
        "name": char.name,
        "class": cls.name,
        "subclass": char.subclass,
        "current_level": char.level,
        "next_level": new_level,
        "applied": did_apply,
        "subclass_label": cls.subclass_label,
        "subclass_level": cls.subclass_level,
        "subclass_required": subclass_required,
        "subclass_options": option_list,
        "report": report,
    }


@app.get("/character/{character_id}/progression")
async def character_progression(character_id: int):
    """Preview (without applying) the SRD changes for this character's next level."""
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        return _progression(session, char, target_subclass=None, apply=False)


@app.post("/level_up")
async def level_up(req: LevelUpRequest):
    """Advance a character one level, following SRD guidance.

    If the new level is where the class chooses its subclass and none is provided,
    the level is NOT applied — the response returns ``subclass_required`` plus the
    available subclass options (including owned non-SRD ones) so the caller can
    resubmit with a choice.
    """
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        return _progression(session, char, target_subclass=req.subclass, apply=True)


@app.get("/class_options/{class_name}")
async def class_options(class_name: str):
    """List a class's subclasses (name, source) and the level it chooses one at."""
    with Session(engine) as session:
        cls = _get_class_row(session, class_name)
        if not cls:
            raise HTTPException(status_code=404, detail=f"Unknown class: {class_name!r}")
        options = _subclasses_for(session, cls)
        return {
            "class": cls.name,
            "subclass_label": cls.subclass_label,
            "subclass_level": cls.subclass_level,
            "subclasses": [{"name": s.name, "slug": s.index_slug, "source": s.source} for s in options],
        }


# ----- Combat state tracker -----

@app.post("/combat/start")
async def combat_start(req: CombatStartRequest):
    enc = combat.start_encounter(req.session_id, req.name)
    return {"status": "ok", "encounter": combat.state(enc.id)}


@app.post("/combat/add")
async def combat_add(req: CombatAddRequest):
    try:
        if req.monster_slug:
            created = combat.add_from_monster(
                req.encounter_id, req.monster_slug, count=req.count, roll_hp=req.roll_hp
            )
            return {"status": "ok", "added": [c.id for c in created], "encounter": combat.state(req.encounter_id)}
        if not req.name or req.max_hp is None:
            raise HTTPException(status_code=400, detail="Non-monster combatants need 'name' and 'max_hp'.")
        c = combat.add_combatant(
            req.encounter_id, req.name, kind=req.kind, max_hp=req.max_hp,
            armor_class=req.armor_class, dex_mod=req.dex_mod, initiative=req.initiative,
            character_id=req.character_id,
        )
        return {"status": "ok", "added": [c.id], "encounter": combat.state(req.encounter_id)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/combat/{encounter_id}/roll_initiative")
async def combat_roll_initiative(encounter_id: int, reroll: bool = False):
    combat.roll_initiative(encounter_id, reroll=reroll)
    return {"status": "ok", "encounter": combat.state(encounter_id)}


@app.post("/combat/{encounter_id}/next")
async def combat_next(encounter_id: int):
    enc, cur = combat.next_turn(encounter_id)
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    return {"status": "ok", "current_combatant_id": cur.id if cur else None, "encounter": combat.state(encounter_id)}


@app.post("/combat/damage")
async def combat_damage(req: CombatDamageRequest):
    try:
        return {"status": "ok", "combatant": combat.apply_damage(req.combatant_id, req.amount)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/combat/heal")
async def combat_heal(req: CombatDamageRequest):
    try:
        return {"status": "ok", "combatant": combat.heal(req.combatant_id, req.amount)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/combat/temp_hp")
async def combat_temp_hp(req: CombatDamageRequest):
    try:
        return {"status": "ok", "combatant": combat.set_temp_hp(req.combatant_id, req.amount)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/combat/condition")
async def combat_condition(req: CombatConditionRequest):
    try:
        if req.remove:
            combatant = combat.remove_condition(req.combatant_id, req.condition)
        else:
            combatant = combat.add_condition(req.combatant_id, req.condition)
        return {"status": "ok", "combatant": combatant}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.post("/combat/concentration")
async def combat_concentration(req: CombatConcentrationRequest):
    try:
        return {"status": "ok", "combatant": combat.set_concentration(req.combatant_id, req.spell)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/combat/active/{session_id}")
async def combat_active(session_id: str):
    enc = combat.get_active(session_id)
    if not enc:
        return {"active": False}
    return {"active": True, "encounter": combat.state(enc.id), "board": combat.render(enc.id)}


@app.get("/combat/{encounter_id}/state")
async def combat_state(encounter_id: int):
    state = combat.state(encounter_id)
    if not state:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    return {"encounter": state, "board": combat.render(encounter_id)}


@app.post("/combat/{encounter_id}/end")
async def combat_end(encounter_id: int):
    enc = combat.end_encounter(encounter_id)
    if not enc:
        raise HTTPException(status_code=404, detail="Encounter not found.")
    return {"status": "ok", "encounter": combat.state(encounter_id)}


# ===================== Rules lookup (items + reference) =====================

@app.get("/rules/item/{ref}")
async def rules_item(ref: str):
    it = rules_lib.get_item(ref)
    if not it:
        raise HTTPException(status_code=404, detail=f"Item '{ref}' not found.")
    return {"item": it.model_dump(exclude={"raw"}), "brief": format_item_brief(it)}


@app.get("/rules/items")
async def rules_items(
    q: str = "",
    category: Optional[str] = None,
    max_cost_gp: Optional[float] = None,
    rarity: Optional[str] = None,
    limit: int = 20,
):
    items = rules_lib.search_items(
        q, category=category, max_cost_gp=max_cost_gp, rarity=rarity, limit=limit
    )
    return {"count": len(items), "items": [format_item_brief(i) for i in items]}


@app.get("/rules/reference/{category}/{ref}")
async def rules_reference_entry(category: str, ref: str):
    e = rules_lib.get_reference(category, ref)
    if not e:
        raise HTTPException(status_code=404, detail=f"{category}/{ref} not found.")
    return {"entry": e.model_dump(exclude={"data"}), "brief": format_reference_brief(e)}


@app.get("/rules/reference")
async def rules_reference_search(q: str = "", category: Optional[str] = None, limit: int = 20):
    entries = rules_lib.search_reference(q, category=category, limit=limit)
    return {"count": len(entries), "entries": [format_reference_brief(e) for e in entries]}


# ===================== Economy (coins, buying, downtime, crafting) =========

def _char_purse(char: Character) -> Dict[str, int]:
    return {"cp": char.cp, "sp": char.sp, "ep": char.ep, "gp": char.gp, "pp": char.pp}


def _apply_purse(char: Character, purse: Dict[str, int]) -> None:
    char.cp = int(purse.get("cp", 0))
    char.sp = int(purse.get("sp", 0))
    char.ep = int(purse.get("ep", 0))
    char.gp = int(purse.get("gp", 0))
    char.pp = int(purse.get("pp", 0))


def _apply_cp_delta(char: Character, cp_delta: int) -> None:
    """Add/subtract copper from a character's purse, re-minting change."""
    from economy.currency import from_cp

    total = to_cp(_char_purse(char)) + int(cp_delta)
    if total < 0:
        raise HTTPException(status_code=400, detail="Insufficient funds for this transaction.")
    _apply_purse(char, from_cp(total))


class CoinAdjustRequest(BaseModel):
    character_id: int
    # Any subset of denominations; positive adds, negative removes.
    cp: int = 0
    sp: int = 0
    ep: int = 0
    gp: int = 0
    pp: int = 0
    reason: Optional[str] = None


class BuyRequest(BaseModel):
    character_id: int
    item: str  # slug or name
    quantity: int = 1


class SellRequest(BaseModel):
    character_id: int
    item: str
    quantity: int = 1


class DowntimeRequest(BaseModel):
    character_id: int
    activity: str
    days: int
    lifestyle_tier: Optional[str] = None
    extra_cost_gp: float = 0.0
    earnings_gp: float = 0.0
    advance_world: bool = True


class CraftingStartRequest(BaseModel):
    character_id: int
    item: str  # slug or name (its market cost sets the project size)
    is_magic: bool = False
    pay_materials: bool = True


class CraftingAdvanceRequest(BaseModel):
    project_id: int
    days: int
    advance_world: bool = True


@app.get("/character/{character_id}/purse")
async def character_purse(character_id: int):
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        purse = _char_purse(char)
        return {"purse": purse, "gp_value": gp_value(purse), "display": format_purse(purse)}


@app.post("/economy/adjust")
async def economy_adjust(req: CoinAdjustRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        delta = {"cp": req.cp, "sp": req.sp, "ep": req.ep, "gp": req.gp, "pp": req.pp}
        new_purse = add_coins(_char_purse(char), delta)
        if to_cp(new_purse) < 0:
            raise HTTPException(status_code=400, detail="Adjustment would make the purse negative.")
        _apply_purse(char, new_purse)
        session.add(char)
        session.commit()
        session.refresh(char)
        return {"status": "ok", "purse": _char_purse(char), "display": format_purse(_char_purse(char))}


@app.post("/economy/buy")
async def economy_buy(req: BuyRequest):
    it = rules_lib.get_item(req.item)
    if not it or it.cost_gp is None:
        raise HTTPException(status_code=404, detail=f"Priced item '{req.item}' not found.")
    if req.quantity < 1:
        raise HTTPException(status_code=400, detail="quantity must be >= 1")
    unit_cp = round(it.cost_gp * get_config().economy.item_cost_multiplier * 100)
    total_cp = unit_cp * req.quantity
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        try:
            new_purse = subtract_cost(_char_purse(char), total_cp)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        _apply_purse(char, new_purse)
        session.add(char)
        session.commit()
        return {
            "status": "ok",
            "item": it.name,
            "quantity": req.quantity,
            "spent_gp": round(total_cp / 100, 2),
            "purse": _char_purse(char),
            "display": format_purse(_char_purse(char)),
        }


@app.post("/economy/sell")
async def economy_sell(req: SellRequest):
    it = rules_lib.get_item(req.item)
    if not it or it.cost_gp is None:
        raise HTTPException(status_code=404, detail=f"Priced item '{req.item}' not found.")
    if req.quantity < 1:
        raise HTTPException(status_code=400, detail="quantity must be >= 1")
    econ = get_config().economy
    unit_cp = round(it.cost_gp * econ.item_cost_multiplier * econ.sell_price_ratio * 100)
    total_cp = unit_cp * req.quantity
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        _apply_cp_delta(char, total_cp)
        session.add(char)
        session.commit()
        return {
            "status": "ok",
            "item": it.name,
            "quantity": req.quantity,
            "earned_gp": round(total_cp / 100, 2),
            "purse": _char_purse(char),
            "display": format_purse(_char_purse(char)),
        }


@app.post("/downtime")
async def downtime(req: DowntimeRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        tier = req.lifestyle_tier or char.lifestyle or "modest"
        try:
            result = resolve_downtime(
                req.activity,
                req.days,
                lifestyle_tier=tier,
                extra_cost_gp=req.extra_cost_gp,
                earnings_gp=req.earnings_gp,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        start_day = world.current_day()
        _apply_cp_delta(char, result["cp_delta"])

        end_day = start_day
        if req.advance_world and req.days > 0:
            try:
                end_day = world.advance_day(req.days)
            except Exception:
                end_day = start_day

        log = DowntimeLog(
            character_id=char.id,
            activity=result["activity"],
            days=req.days,
            start_day=start_day,
            end_day=end_day,
            cp_delta=result["cp_delta"],
            result_summary=result["summary"],
        )
        session.add(log)
        session.add(char)
        session.commit()
        return {
            "status": "ok",
            "result": result,
            "purse": _char_purse(char),
            "display": format_purse(_char_purse(char)),
            "start_day": start_day,
            "end_day": end_day,
        }


@app.post("/crafting/start")
async def crafting_start(req: CraftingStartRequest):
    it = rules_lib.get_item(req.item)
    if not it or it.cost_gp is None:
        raise HTTPException(status_code=404, detail=f"Priced item '{req.item}' not found.")
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        try:
            plan = start_crafting(it.cost_gp, is_magic=req.is_magic)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if req.pay_materials:
            mat_cp = plan["materials_cp"]
            if to_cp(_char_purse(char)) < mat_cp:
                raise HTTPException(status_code=400, detail="Not enough coin for materials.")
            _apply_cp_delta(char, -mat_cp)

        project = CraftingProject(
            character_id=char.id,
            item_slug=it.index_slug,
            item_name=it.name,
            is_magic=req.is_magic,
            target_cost_gp=plan["target_cost_gp"],
            materials_gp=plan["materials_gp"],
            progress_gp=0.0,
            gp_per_day=plan["gp_per_day"],
        )
        session.add(project)
        session.add(char)
        session.commit()
        session.refresh(project)
        return {
            "status": "ok",
            "project_id": project.id,
            "plan": plan,
            "purse": _char_purse(char),
        }


@app.post("/crafting/advance")
async def crafting_advance(req: CraftingAdvanceRequest):
    with Session(engine) as session:
        project = session.get(CraftingProject, req.project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Crafting project not found.")
        if project.complete:
            return {"status": "already_complete", "project": project.model_dump()}

        step = advance_crafting(
            target_cost_gp=project.target_cost_gp,
            progress_gp=project.progress_gp,
            gp_per_day=project.gp_per_day,
            days=req.days,
        )
        project.progress_gp = step["progress_gp"]
        project.days_spent += req.days
        project.complete = step["complete"]
        project.updated_at = datetime.utcnow()

        end_day = None
        if req.advance_world and req.days > 0:
            try:
                end_day = world.advance_day(req.days)
            except Exception:
                end_day = None

        session.add(project)
        session.commit()
        session.refresh(project)
        return {"status": "ok", "step": step, "project": project.model_dump(), "end_day": end_day}


@app.get("/character/{character_id}/crafting")
async def character_crafting(character_id: int):
    with Session(engine) as session:
        rows = session.exec(
            select(CraftingProject).where(CraftingProject.character_id == character_id)
        ).all()
        return {"projects": [r.model_dump() for r in rows]}


# ===================== XP & milestone progression ==========================

class AwardXpRequest(BaseModel):
    character_id: int
    amount: int
    reason: Optional[str] = None


def _level_for_xp(xp: int) -> int:
    prog = get_config().progression
    thresholds = prog.xp_thresholds
    level = 1
    for lvl in range(1, min(prog.max_level, len(thresholds) - 1) + 1):
        if xp >= thresholds[lvl]:
            level = lvl
    return level


@app.post("/award_xp")
async def award_xp(req: AwardXpRequest):
    prog = get_config().progression
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        if prog.milestone_leveling:
            return {
                "status": "milestone",
                "message": "Milestone leveling is enabled; XP is not tracked. Use /level_up.",
                "xp": char.xp,
                "level": char.level,
            }
        gained = int(round(req.amount * prog.xp_multiplier))
        char.xp = max(0, char.xp + gained)
        eligible_level = _level_for_xp(char.xp)
        can_level = eligible_level > char.level
        session.add(char)
        session.commit()
        session.refresh(char)
        return {
            "status": "ok",
            "xp_awarded": gained,
            "xp_total": char.xp,
            "current_level": char.level,
            "eligible_level": eligible_level,
            "can_level_up": can_level,
        }


# ===================== Bastions ============================================

class BastionCreateRequest(BaseModel):
    character_id: int
    name: str


class BastionFacilityRequest(BaseModel):
    bastion_id: int
    facility_slug: str
    pay_cost: bool = True


class BastionOrderRequest(BaseModel):
    facility_id: int
    order: str


class BastionTurnRequest(BaseModel):
    bastion_id: int
    advance_world: bool = True


@app.post("/bastion/create")
async def bastion_create(req: BastionCreateRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        if not can_own_bastion(char.level):
            raise HTTPException(
                status_code=400,
                detail=f"Bastions require level {min_bastion_level()}+ (character is {char.level}).",
            )
        b = Bastion(character_id=char.id, name=req.name, level_acquired=char.level)
        session.add(b)
        session.commit()
        session.refresh(b)
        return {"status": "ok", "bastion": b.model_dump()}


@app.post("/bastion/facility")
async def bastion_add_facility(req: BastionFacilityRequest):
    cat = get_facility(req.facility_slug)
    if not cat:
        raise HTTPException(status_code=404, detail=f"Facility '{req.facility_slug}' not in catalog.")
    with Session(engine) as session:
        b = session.get(Bastion, req.bastion_id)
        if not b:
            raise HTTPException(status_code=404, detail="Bastion not found.")
        char = session.get(Character, b.character_id)
        if char and char.level < cat["min_level"]:
            raise HTTPException(
                status_code=400,
                detail=f"{cat['name']} requires level {cat['min_level']} (character is {char.level}).",
            )
        if req.pay_cost and char:
            cost_cp = gp_to_cp(facility_cost_gp(req.facility_slug))
            if to_cp(_char_purse(char)) < cost_cp:
                raise HTTPException(status_code=400, detail="Not enough coin to build this facility.")
            _apply_cp_delta(char, -cost_cp)
            session.add(char)
        fac = FacilityInstance(
            bastion_id=b.id,
            facility_slug=req.facility_slug,
            name=cat["name"],
            facility_type="special",
            space=cat.get("space"),
        )
        session.add(fac)
        session.commit()
        session.refresh(fac)
        return {"status": "ok", "facility": fac.model_dump()}


@app.post("/bastion/order")
async def bastion_set_order(req: BastionOrderRequest):
    with Session(engine) as session:
        fac = session.get(FacilityInstance, req.facility_id)
        if not fac:
            raise HTTPException(status_code=404, detail="Facility not found.")
        fac.current_order = req.order
        session.add(fac)
        session.commit()
        session.refresh(fac)
        return {"status": "ok", "facility": fac.model_dump()}


@app.post("/bastion/turn")
async def bastion_turn(req: BastionTurnRequest):
    with Session(engine) as session:
        b = session.get(Bastion, req.bastion_id)
        if not b:
            raise HTTPException(status_code=404, detail="Bastion not found.")
        facilities = session.exec(
            select(FacilityInstance).where(
                FacilityInstance.bastion_id == b.id, FacilityInstance.enabled == True  # noqa: E712
            )
        ).all()
        fac_payload = [
            {"facility_slug": f.facility_slug, "current_order": f.current_order} for f in facilities
        ]
        start_day = world.current_day()
        result = resolve_bastion_turn(
            fac_payload, world_day=start_day, turn_number=b.turns_taken + 1
        )

        # Pay income to the bastion owner.
        char = session.get(Character, b.character_id)
        if char and result["income_cp"]:
            _apply_cp_delta(char, result["income_cp"])
            session.add(char)

        end_day = start_day
        if req.advance_world and result["days"] > 0:
            try:
                end_day = world.advance_day(result["days"])
            except Exception:
                end_day = start_day

        for ev in result["events"]:
            session.add(BastionEvent(
                bastion_id=b.id,
                turn=result["turn_number"],
                world_day=end_day,
                event_type=ev["event_type"],
                facility_slug=ev.get("facility_slug"),
                description=ev["description"],
                cp_delta=ev.get("cp_delta", 0),
            ))
        b.turns_taken += 1
        b.last_turn_day = end_day
        b.updated_at = datetime.utcnow()
        session.add(b)
        session.commit()
        return {
            "status": "ok",
            "result": result,
            "purse": _char_purse(char) if char else None,
            "end_day": end_day,
        }


@app.get("/character/{character_id}/bastion")
async def character_bastion(character_id: int):
    with Session(engine) as session:
        b = session.exec(
            select(Bastion).where(Bastion.character_id == character_id)
        ).first()
        if not b:
            return {"has_bastion": False, "min_level": min_bastion_level()}
        facilities = session.exec(
            select(FacilityInstance).where(FacilityInstance.bastion_id == b.id)
        ).all()
        return {
            "has_bastion": True,
            "bastion": b.model_dump(),
            "facilities": [f.model_dump() for f in facilities],
        }


@app.get("/bastion/facilities/{level}")
async def bastion_facilities_for_level(level: int):
    return {
        "level": level,
        "facilities": [
            {k: v for k, v in f.items() if k != "source"} for f in facilities_for_level(level)
        ],
    }


# ===================== Survival & exploration ==============================

def _ability_mod(char: Character, *names: str) -> int:
    stats = char.stats or {}
    for n in names:
        for key in (n, n[:3], n.upper(), n[:3].upper(), n.capitalize()):
            if key in stats and stats[key] is not None:
                return ability_modifier(stats[key])
    return 0


def _passive_score(char: Character, ability: str, *, proficient: bool = False) -> int:
    mod = _ability_mod(char, ability)
    pb = proficiency_bonus_for_level(char.level) if proficient else 0
    return 10 + mod + pb


class ConsumeDayRequest(BaseModel):
    character_id: int
    rations_consumed: Optional[int] = None   # defaults to config food_per_day
    water_consumed: Optional[int] = None
    advance_world: bool = True


@app.post("/survival/consume_day")
async def survival_consume_day(req: ConsumeDayRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        result = consume_day(
            rations=char.rations,
            water=char.water,
            days_without_food=char.days_without_food,
            days_without_water=char.days_without_water,
            exhaustion=char.exhaustion,
        )
        char.rations = result["rations"]
        char.water = result["water"]
        char.days_without_food = result["days_without_food"]
        char.days_without_water = result["days_without_water"]
        char.exhaustion = result["exhaustion"]
        session.add(char)
        session.commit()
        end_day = world.current_day()
        if req.advance_world:
            try:
                end_day = world.advance_day(1)
            except Exception:
                pass
        return {"status": "ok", "result": result, "world_day": end_day}


class ProvisionRequest(BaseModel):
    character_id: int
    rations_delta: int = 0
    water_delta: int = 0


@app.post("/survival/provisions")
async def survival_provisions(req: ProvisionRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        char.rations = max(0, char.rations + req.rations_delta)
        char.water = max(0, char.water + req.water_delta)
        session.add(char)
        session.commit()
        session.refresh(char)
        return {"status": "ok", "rations": char.rations, "water": char.water}


class TravelRequest(BaseModel):
    distance_miles: float
    pace: str = "normal"
    terrain: str = "road"


@app.post("/survival/travel")
async def survival_travel_endpoint(req: TravelRequest):
    return {
        "travel": survival_travel(req.distance_miles, pace=req.pace, terrain=req.terrain),
        "navigation": navigation_dc(req.terrain),
    }


class ForageRequest(BaseModel):
    terrain: str = "grassland"
    foragers: int = 1


@app.post("/survival/forage")
async def survival_forage_endpoint(req: ForageRequest):
    return survival_forage(req.terrain, foragers=req.foragers)


@app.get("/survival/weather")
async def survival_weather(region: Optional[str] = None, climate: Optional[str] = None):
    day = world.current_day()
    month = ((day // 30) % 12) + 1
    clim = climate or _region_climate(region)
    weather = generate_weather(day, climate=clim, month=month)
    return {
        "world_day": day,
        "climate": clim,
        "weather": weather,
        "hazards": active_hazard_tags(weather),
    }


class LightBurnRequest(BaseModel):
    kind: str = "torch"
    minutes_remaining: int
    minutes_elapsed: int


@app.post("/survival/light")
async def survival_light(req: LightBurnRequest):
    return {
        "spec": source_spec(req.kind),
        "result": light_burn(req.kind, req.minutes_remaining, req.minutes_elapsed),
    }


class ExhaustionRequest(BaseModel):
    character_id: int
    delta: int = 0
    set_to: Optional[int] = None


@app.post("/survival/exhaustion")
async def survival_exhaustion(req: ExhaustionRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        if req.set_to is not None:
            char.exhaustion = max(0, min(6, req.set_to))
        elif req.delta >= 0:
            char.exhaustion = add_exhaustion(char.exhaustion, req.delta)
        else:
            char.exhaustion = remove_exhaustion(char.exhaustion, -req.delta)
        session.add(char)
        session.commit()
        session.refresh(char)
        return {"status": "ok", "exhaustion": char.exhaustion,
                "description": describe_exhaustion(char.exhaustion)}


class RestRequest(BaseModel):
    character_id: int
    spend_hit_dice: int = 0    # short rest only
    ate_and_drank: bool = True  # long rest only


@app.post("/survival/short_rest")
async def survival_short_rest_endpoint(req: RestRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        result = survival_short_rest(
            current_hp=char.current_hp,
            max_hp=char.max_hp,
            hit_die=char.hit_die,
            hit_dice_remaining=char.hit_dice_remaining,
            con_mod=_con_mod(char),
            spend=req.spend_hit_dice,
        )
        char.current_hp = result["current_hp"]
        char.hit_dice_remaining = result["hit_dice_remaining"]
        session.add(char)
        session.commit()
        return {"status": "ok", "result": result}


@app.post("/survival/long_rest")
async def survival_long_rest_endpoint(req: RestRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        result = survival_long_rest(
            current_hp=char.current_hp,
            max_hp=char.max_hp,
            hit_dice_total=char.hit_dice_total,
            hit_dice_remaining=char.hit_dice_remaining,
            exhaustion=char.exhaustion,
            ate_and_drank=req.ate_and_drank,
        )
        char.current_hp = result["current_hp"]
        char.hit_dice_remaining = result["hit_dice_remaining"]
        char.exhaustion = result["exhaustion"]
        # A long rest resets deprivation if fed and watered.
        if req.ate_and_drank:
            char.days_without_food = 0
            char.days_without_water = 0
        char.death_save_successes = 0
        char.death_save_failures = 0
        char.stable = True
        session.add(char)
        session.commit()
        return {"status": "ok", "result": result}


class DamageHealRequest(BaseModel):
    character_id: int
    amount: int   # positive = damage, negative = heal


@app.post("/survival/hp")
async def survival_hp(req: DamageHealRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        note = ""
        if req.amount >= 0:
            char.current_hp -= req.amount
            if char.current_hp <= 0:
                char.current_hp = 0
                char.stable = False
                char.death_save_successes = 0
                char.death_save_failures = 0
                note = "Dropped to 0 HP — dying and unstable."
        else:
            healed = -req.amount
            was_down = char.current_hp <= 0
            char.current_hp = min(char.max_hp, char.current_hp + healed)
            if was_down and char.current_hp > 0:
                char.stable = True
                char.death_save_successes = 0
                char.death_save_failures = 0
                note = "Revived above 0 HP."
        session.add(char)
        session.commit()
        session.refresh(char)
        return {"status": "ok", "current_hp": char.current_hp, "max_hp": char.max_hp,
                "stable": char.stable, "note": note}


class DeathSaveRequest(BaseModel):
    character_id: int
    result: str  # success | failure | crit_success | crit_failure


@app.post("/survival/death_save")
async def survival_death_save(req: DeathSaveRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        outcome = ""
        r = req.result.lower()
        if r == "crit_success":
            char.current_hp = 1
            char.stable = True
            char.death_save_successes = 0
            char.death_save_failures = 0
            outcome = "Natural 20 — regains 1 HP and is conscious!"
        elif r == "crit_failure":
            char.death_save_failures = min(3, char.death_save_failures + 2)
            outcome = "Natural 1 — counts as two failures."
        elif r == "success":
            char.death_save_successes = min(3, char.death_save_successes + 1)
        elif r == "failure":
            char.death_save_failures = min(3, char.death_save_failures + 1)
        else:
            raise HTTPException(status_code=400, detail="result must be success/failure/crit_success/crit_failure.")
        dead = char.death_save_failures >= 3
        stabilized = char.death_save_successes >= 3
        if stabilized and not dead:
            char.stable = True
            char.death_save_successes = 0
            char.death_save_failures = 0
            outcome = outcome or "Three successes — stabilized at 0 HP."
        session.add(char)
        session.commit()
        session.refresh(char)
        return {
            "status": "ok",
            "successes": char.death_save_successes,
            "failures": char.death_save_failures,
            "dead": dead,
            "stable": char.stable,
            "current_hp": char.current_hp,
            "outcome": outcome,
        }


class InspirationRequest(BaseModel):
    character_id: int
    grant: bool = True


@app.post("/survival/inspiration")
async def survival_inspiration(req: InspirationRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        char.inspiration = bool(req.grant)
        session.add(char)
        session.commit()
        return {"status": "ok", "inspiration": char.inspiration}


@app.get("/character/{character_id}/survival")
async def character_survival(character_id: int):
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        return {
            "hp": {"current": char.current_hp, "max": char.max_hp},
            "hit_dice": {"remaining": char.hit_dice_remaining, "total": char.hit_dice_total,
                         "die": char.hit_die},
            "exhaustion": char.exhaustion,
            "exhaustion_desc": describe_exhaustion(char.exhaustion),
            "provisions": {"rations": char.rations, "water": char.water,
                           "days_without_food": char.days_without_food,
                           "days_without_water": char.days_without_water},
            "death_saves": {"successes": char.death_save_successes,
                            "failures": char.death_save_failures, "stable": char.stable},
            "inspiration": char.inspiration,
            "encumbrance": encumbrance_status(_ability_mod(char, "strength") * 2 + 10,
                                              _carried_weight(char)),
            "passive_perception": _passive_score(char, "wisdom"),
            "passive_investigation": _passive_score(char, "intelligence"),
            "passive_insight": _passive_score(char, "wisdom"),
        }


def _carried_weight(char: Character) -> float:
    inv = char.inventory or []
    total = 0.0
    if isinstance(inv, list):
        for it in inv:
            if isinstance(it, dict):
                total += float(it.get("weight", 0) or 0) * float(it.get("quantity", 1) or 1)
    return total


# ===================== Hazards (diseases / traps / madness) ================

class ContractDiseaseRequest(BaseModel):
    character_id: int
    disease_slug: str


@app.post("/hazards/disease/contract")
async def hazards_contract_disease(req: ContractDiseaseRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        info = contract_disease(req.disease_slug, world_day=world.current_day())
        if "error" in info:
            raise HTTPException(status_code=404, detail=info["error"])
        aff = Affliction(
            character_id=char.id,
            kind="disease",
            slug=req.disease_slug,
            name=info["name"],
            description=info["description"],
            onset_day=info["onset_day"],
            active=True,
            notes=info.get("effect"),
        )
        session.add(aff)
        session.commit()
        session.refresh(aff)
        return {"status": "ok", "affliction": aff.model_dump(), "info": info}


class DiseaseSaveRequest(BaseModel):
    affliction_id: int
    save_succeeded: bool
    consecutive_successes: int = 0


@app.post("/hazards/disease/save")
async def hazards_disease_save(req: DiseaseSaveRequest):
    with Session(engine) as session:
        aff = session.get(Affliction, req.affliction_id)
        if not aff or aff.kind != "disease":
            raise HTTPException(status_code=404, detail="Disease affliction not found.")
        result = disease_recovery_check(
            aff.slug, save_succeeded=req.save_succeeded,
            consecutive_successes=req.consecutive_successes)
        if result.get("cured"):
            aff.active = False
            session.add(aff)
            session.commit()
        return {"status": "ok", "result": result}


class CureRequest(BaseModel):
    affliction_id: int


@app.post("/hazards/cure")
async def hazards_cure(req: CureRequest):
    with Session(engine) as session:
        aff = session.get(Affliction, req.affliction_id)
        if not aff:
            raise HTTPException(status_code=404, detail="Affliction not found.")
        aff.active = False
        session.add(aff)
        session.commit()
        return {"status": "ok", "cured": aff.name}


class TrapDetectRequest(BaseModel):
    trap_slug: str
    passive_perception: int


@app.post("/hazards/trap/detect")
async def hazards_trap_detect(req: TrapDetectRequest):
    result = trap_detect(req.trap_slug, req.passive_perception)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


class TrapDisarmRequest(BaseModel):
    trap_slug: str
    check_total: int


@app.post("/hazards/trap/disarm")
async def hazards_trap_disarm(req: TrapDisarmRequest):
    result = trap_disarm(req.trap_slug, check_total=req.check_total)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


class MadnessRequest(BaseModel):
    character_id: Optional[int] = None
    severity: str = "short"
    persist: bool = False


@app.post("/hazards/madness")
async def hazards_madness(req: MadnessRequest):
    result = roll_madness(req.severity)
    if not result.get("enabled"):
        return result
    if req.persist and req.character_id:
        with Session(engine) as session:
            char = session.get(Character, req.character_id)
            if not char:
                raise HTTPException(status_code=404, detail="Character not found.")
            aff = Affliction(
                character_id=char.id,
                kind="madness",
                severity=result["severity"],
                name=f"{result['severity'].title()} madness",
                description=result["description"],
                onset_day=world.current_day(),
                active=True,
                notes=result["effect"],
            )
            session.add(aff)
            session.commit()
            session.refresh(aff)
            result["affliction"] = aff.model_dump()
    return result


@app.get("/character/{character_id}/afflictions")
async def character_afflictions(character_id: int):
    with Session(engine) as session:
        rows = session.exec(
            select(Affliction).where(
                Affliction.character_id == character_id,
                Affliction.active == True,  # noqa: E712
            )
        ).all()
        return {"afflictions": [a.model_dump() for a in rows]}


# ===================== Reputation ==========================================

class RenownRequest(BaseModel):
    character_id: int
    faction_slug: str
    faction_name: Optional[str] = None
    delta: int


@app.post("/reputation/adjust")
async def reputation_adjust(req: RenownRequest):
    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        rep = session.exec(
            select(Reputation).where(
                Reputation.character_id == req.character_id,
                Reputation.faction_slug == req.faction_slug,
            )
        ).first()
        if not rep:
            rep = Reputation(
                character_id=req.character_id,
                faction_slug=req.faction_slug,
                faction_name=req.faction_name or req.faction_slug.replace("-", " ").title(),
                renown=0,
            )
        result = adjust_renown(rep.renown, req.delta)
        rep.renown = result["renown"]
        rep.updated_at = datetime.utcnow()
        session.add(rep)
        session.commit()
        session.refresh(rep)
        return {"status": "ok", "reputation": rep.model_dump(), "result": result}


@app.get("/character/{character_id}/reputation")
async def character_reputation(character_id: int):
    with Session(engine) as session:
        rows = session.exec(
            select(Reputation).where(Reputation.character_id == character_id)
        ).all()
        return {
            "reputations": [
                {**r.model_dump(), "standing": describe_standing(r.renown)}
                for r in rows
            ]
        }


# ===================== Combat cover ========================================

class CoverRequest(BaseModel):
    combatant_id: int
    cover: str  # none | half | three-quarters | total


@app.post("/combat/cover")
async def combat_set_cover(req: CoverRequest):
    try:
        combatant = combat.set_cover(req.combatant_id, req.cover)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "status": "ok",
        "combatant": combatant,
        "effective_ac": combat.effective_ac(req.combatant_id),
    }


# ===================== Bestiary: owned monsters & scaling ==================

class ScaleMonsterRequest(BaseModel):
    monster: str                      # slug or name (SRD or owned)
    template: str                     # weak | tough | elite | young | boss | swarm
    name_override: Optional[str] = None


@app.get("/monsters/templates")
async def monsters_templates():
    """List the available monster-scaling templates."""
    return {"templates": list_templates()}


@app.get("/monsters/owned")
async def monsters_owned():
    """List the self-authored (non-SRD) bestiary catalog."""
    return {
        "count": len(OWNED_MONSTERS),
        "monsters": [
            {
                "slug": m.get("index_slug"),
                "name": m.get("name"),
                "type": m.get("type"),
                "challenge_rating": m.get("challenge_rating"),
                "xp": m.get("xp"),
                "source": m.get("source"),
            }
            for m in OWNED_MONSTERS
        ],
    }


@app.post("/monsters/scale")
async def monsters_scale(req: ScaleMonsterRequest):
    """Scale a monster (SRD or owned) by a template into a new stat block."""
    if req.template not in MONSTER_TEMPLATES:
        raise HTTPException(status_code=400,
                            detail=f"Unknown template '{req.template}'. Options: {list(MONSTER_TEMPLATES)}")
    row = rules_lib.get_monster(req.monster)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Monster '{req.monster}' not found")
    base = monster_to_dict(row)
    try:
        scaled = scale_monster(base, req.template, name_override=req.name_override)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "status": "ok",
        "template": req.template,
        "base": {"name": base.get("name"), "challenge_rating": base.get("challenge_rating")},
        "scaled": scaled,
        "brief": format_monster_brief(scaled) if isinstance(scaled, dict) else None,
    }


# ===================== DM guidance & encounter tools =======================

class EncounterEstimateRequest(BaseModel):
    party_levels: list[int]
    monster_xps: Optional[list[int]] = None
    monsters: Optional[list[str]] = None   # slugs/names; XP looked up if given


class EncounterPlanRequest(BaseModel):
    party_levels: list[int]
    target_difficulty: str = "medium"


@app.get("/dm/guidance")
async def dm_guidance(verbosity: str = ""):
    """Return the self-authored DM best-practice text."""
    if verbosity == "full":
        return {"guidance": full_guidance(), "verbosity": "full"}
    if verbosity == "brief":
        return {"guidance": brief_guidance(), "verbosity": "brief"}
    block = guidance_block()
    cfg = get_config().dm_guide
    return {"guidance": block, "verbosity": cfg.guidance_verbosity, "enabled": cfg.enabled}


@app.get("/dm/dc")
async def dm_dc(difficulty: str = ""):
    """Suggest a check DC for a named difficulty, or return the full ladder."""
    if difficulty:
        return suggest_dc(difficulty)
    return {"scale": dc_scale()}


@app.post("/dm/encounter")
async def dm_encounter(req: EncounterEstimateRequest):
    """Estimate encounter difficulty for a party from monster XP (or monster refs)."""
    xps: list[int] = list(req.monster_xps or [])
    resolved: list[dict] = []
    if req.monsters:
        for ref in req.monsters:
            row = rules_lib.get_monster(ref)
            if row is None:
                raise HTTPException(status_code=404, detail=f"Monster '{ref}' not found")
            xp = int(row.xp or 0)
            xps.append(xp)
            resolved.append({"ref": ref, "name": row.name, "xp": xp,
                             "challenge_rating": row.challenge_rating})
    if not xps:
        raise HTTPException(status_code=400,
                            detail="Provide monster_xps or monsters (refs) to estimate.")
    est = estimate_encounter(req.party_levels, xps)
    if resolved:
        est["monsters"] = resolved
    return est


@app.post("/dm/encounter/plan")
async def dm_encounter_plan(req: EncounterPlanRequest):
    """Suggest an XP budget for a target difficulty and party."""
    return build_encounter(req.party_levels, req.target_difficulty)


# ===================== Scene imagery (self-hosted diffusion) ===============

class ImageEnsureRequest(BaseModel):
    kind: str                          # place | npc | creature | item
    subject: str
    context: str = ""
    look: str = ""
    ref_slug: Optional[str] = None
    force_new: bool = False


class ImageTempRequest(BaseModel):
    kind: str = "creature"
    subject: str
    context: str = ""
    look: str = ""


class PortraitGenerateRequest(BaseModel):
    character_id: int
    description: str = ""   # free-text look ("weathered half-elf ranger, green cloak")
    look: str = ""          # optional structured appearance override


class PortraitUploadRequest(BaseModel):
    character_id: int
    b64: str                # base64-encoded PNG/JPEG/WebP bytes
    caption: str = ""


@app.get("/imagery/status")
async def imagery_status():
    """Report imagery config + whether the diffusion backend is reachable."""
    cfg = get_config().imagery
    available = False
    if cfg.enabled:
        try:
            available = image_store._client_for(cfg).is_available()
        except Exception:
            available = False
    return {
        "enabled": cfg.enabled,
        "backend": cfg.backend,
        "base_url": cfg.base_url,
        "checkpoint": cfg.checkpoint,
        "service_available": available,
        "max_per_bucket": cfg.max_per_bucket,
        "stats": image_store.stats(),
    }


@app.post("/imagery/ensure")
async def imagery_ensure(req: ImageEnsureRequest):
    """Generate or reuse a stored picture for (subject x context)."""
    cfg = get_config().imagery
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail="Imagery is disabled in config.")
    result = image_store.ensure_image(
        req.kind, req.subject, look=req.look, context=req.context,
        ref_slug=req.ref_slug, force_new=req.force_new,
    )
    if result is None:
        raise HTTPException(status_code=503, detail="Imagery is disabled.")
    if result.offline:
        raise HTTPException(status_code=503, detail="Image service is offline.")
    return result.payload()


@app.post("/imagery/temp")
async def imagery_temp(req: ImageTempRequest):
    """Generate a throwaway image (never stored)."""
    cfg = get_config().imagery
    if not cfg.enabled or not cfg.allow_temp:
        raise HTTPException(status_code=503, detail="Temp imagery is disabled.")
    result = image_store.generate_temp(
        req.kind, req.subject, look=req.look, context=req.context,
    )
    if result is None:
        raise HTTPException(status_code=503, detail="Temp imagery is disabled.")
    if result.offline:
        raise HTTPException(status_code=503, detail="Image service is offline.")
    return result.payload()


@app.get("/imagery/entity/{kind}/{ref}")
async def imagery_list(kind: str, ref: str, context: Optional[str] = None):
    """List stored image metadata for a subject (optionally one context)."""
    return {"images": image_store.list_for(kind, ref, context)}


@app.get("/imagery/image/{image_id}")
async def imagery_image(image_id: int, thumb: bool = False):
    """Return the raw WebP bytes of a stored image."""
    data = image_store.get_image_bytes(image_id, thumb=thumb)
    if data is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    return Response(content=data, media_type="image/webp")


@app.delete("/imagery/entity/{kind}/{ref}")
async def imagery_invalidate(kind: str, ref: str, context: Optional[str] = None):
    """Remove a subject's images (all contexts) or just one context bucket.

    Use when the world evolves — an NPC is permanently changed, or a place is
    destroyed — so stale pictures don't linger.
    """
    if context:
        removed = image_store.invalidate_context(kind, ref, context)
    else:
        removed = image_store.invalidate_subject(kind, ref)
    return {"status": "ok", "removed": removed}


# ----- Character sheet / inventory / portrait (rendered from the DB) -----

@app.get("/character/{character_id}/sheet")
async def character_sheet(character_id: int):
    """Return a rendered character sheet (structured data straight from the DB)."""
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        sheet = _build_character_sheet(char)
    portrait = image_store.get_portrait(sheet["name"])
    sheet["portrait"] = portrait.payload() if portrait else None
    return sheet


@app.get("/character/{character_id}/inventory")
async def character_inventory(character_id: int):
    """Return the character's inventory list, rendered from stored items."""
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        return _format_inventory(char)


@app.post("/character/{character_id}/portrait/generate")
async def character_portrait_generate(character_id: int, req: PortraitGenerateRequest):
    """Generate (or regenerate) a portrait for the character from a description."""
    cfg = get_config().imagery
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail="Imagery is disabled in config.")
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        name = char.name
        base_look = _portrait_base_look(char)
    look = " ".join(p for p in (base_look, req.look or req.description) if p).strip()
    result = image_store.generate_portrait(name, description=req.description, look=look)
    if result is None:
        raise HTTPException(status_code=503, detail="Imagery is disabled.")
    if result.offline:
        raise HTTPException(status_code=503, detail="Image service is offline.")
    return result.payload()


@app.post("/character/{character_id}/portrait/upload")
async def character_portrait_upload(character_id: int, req: PortraitUploadRequest):
    """Store a player-supplied portrait image (base64 PNG/JPEG/WebP)."""
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        name = char.name
    try:
        raw = base64.b64decode(req.b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64 image data.")
    if not raw:
        raise HTTPException(status_code=400, detail="Empty image data.")
    try:
        result = image_store.set_portrait_from_bytes(
            name, raw, caption=req.caption or f"{name} (portrait)")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not process image: {e}")
    return result.payload()


@app.get("/character/{character_id}/portrait")
async def character_portrait_get(character_id: int):
    """Return the character's current portrait, or 404 if none is stored."""
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        name = char.name
    portrait = image_store.get_portrait(name)
    if portrait is None:
        raise HTTPException(status_code=404, detail="No portrait stored for this character.")
    return portrait.payload()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
