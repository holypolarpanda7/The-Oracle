import os
import json
import re
import time
import base64
from pathlib import Path
from typing import Dict, List, Literal, TypedDict, Optional
import uuid
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import Response, FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv

# Database (SQLModel)
from sqlmodel import SQLModel, Field, create_engine, Session, select
from sqlalchemy import Column, JSON, String, Text, func
from typing import Any
from datetime import datetime, timezone


def _utcnow() -> datetime:
    """Naive UTC now (datetime.utcnow() is deprecated since 3.12)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


import sys

# Make the project root importable so the backend can use the shared packages.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from eight_card_system import WorldGraph
from eight_card_system import entropy as world_entropy
from eight_card_system.graph import slugify
from eight_card_system.seed import seed_minimal_world, backfill_coords, place_pc
from eight_card_system.extraction import extract_and_apply
from eight_card_system.models import (
    Entity as WorldEntity, Relation as WorldRelation, WorldEvent,
    EntityType, RelationType, Attitude, CompanionControl, NpcAttr, attitude_for_trust,
)
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
from combat import CombatTracker, CombatEngine, PCProfile, PCWeapon, Condition
from combat.models import CombatLog
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

# LLM model and auth — provider-agnostic. Works with OpenRouter (cloud) or any
# local OpenAI-compatible server such as Ollama.
LLM_MODEL = os.getenv("LLM_MODEL", "meta-llama/llama-3.1-70b-instruct")
# Optional comma-separated fallback models tried (in order) when the primary
# model is rate-limited/unavailable.
LLM_FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",")
    if m.strip()
]
LLM_BASE_URL = os.getenv(
    "LLM_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
)
# Auth token. Local servers (Ollama) ignore it; pass "ollama" or any placeholder.
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# Ollama's OpenAI-compat shim (LLM_BASE_URL ending in /v1/chat/completions)
# silently ignores context-size options and defaults to a small context window
# (typically 4096) — a big system prompt gets truncated with no error. When
# LLM_NUM_CTX is set AND LLM_BASE_URL looks like that shim, call_openrouter_chat
# instead calls Ollama's NATIVE /api/chat endpoint (derived by swapping the
# path), which honors options.num_ctx. See call_openrouter_chat for the
# graceful-degrade fallback to the OpenAI-compat path.
try:
    LLM_NUM_CTX = int(os.getenv("LLM_NUM_CTX", "0") or 0)
except ValueError:
    LLM_NUM_CTX = 0

# Optional model used for the world-extraction second LLM call (see
# _call_extractor_llm). Falls back to LLM_MODEL when unset — a smaller/cheaper
# model is fine here since it only ever emits a strict JSON delta.
EXTRACTOR_MODEL = os.getenv("EXTRACTOR_MODEL", "").strip() or LLM_MODEL

# --- Ratchet world clock (multiplayer-ready time) ---
# Wall-clock floor: how many world-days pass per real day while nobody plays.
# Session bubbles run in PARALLEL world time and ratchet the clock to the max
# closing bubble (never the sum), so player count never inflates world pace.
WORLD_DAYS_PER_REAL_DAY = float(os.getenv("WORLD_DAYS_PER_REAL_DAY", "1.0"))
# Lead cap: story time may run ahead of the wall-clock floor by at most this
# many days. Bounds the "I logged in and months had passed" whiplash a fast
# table could otherwise inflict on everyone else. Excess days become personal
# busy_until_day commitments instead of moving the shared clock.
WORLD_LEAD_CAP_DAYS = int(os.getenv("WORLD_LEAD_CAP_DAYS", "15"))
# In-bubble skips longer than this become a personal downtime commitment
# (PC attr busy_until_day) instead of dragging the shared clock forward.
BUBBLE_SKIP_CAP_DAYS = int(os.getenv("BUBBLE_SKIP_CAP_DAYS", "7"))

# --- Single-GPU VRAM time-sharing (LLM <-> diffusion) ---
# When enabled, the /chat handler evicts the diffusion model from VRAM before an
# LLM call and evicts the local LLM before rendering an image, so a 70B/32B model
# and ComfyUI can share one GPU (at the cost of load latency between turns).
VRAM_TIMESHARE = os.getenv("VRAM_TIMESHARE", "0").strip().lower() not in {
    "0", "false", "no", "",
}
# Native Ollama endpoint used only to unload the model (keep_alive=0). Distinct
# from LLM_BASE_URL, which is the OpenAI-compatible chat path.
OLLAMA_UNLOAD_URL = os.getenv("OLLAMA_UNLOAD_URL", "http://127.0.0.1:11434/api/generate")

if not LLM_API_KEY:
    raise RuntimeError(f"LLM_API_KEY not found in {ENV_PATH}!")


# ----- Lifespan Context Manager -----

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: Create DB tables if they do not exist
    SQLModel.metadata.create_all(engine)
    print("[Startup] Database tables created/verified")

    # Lightweight schema self-heal for older SQLite DBs. SQLModel create_all()
    # does not ALTER existing tables, so add known-missing world columns needed
    # by current world graph queries.
    try:
        if str(engine.url).startswith("sqlite"):
            with engine.begin() as conn:
                rows = conn.exec_driver_sql("PRAGMA table_info(world_entity)").fetchall()
                existing = {str(r[1]).lower() for r in rows}

                if "subtype" not in existing:
                    conn.exec_driver_sql("ALTER TABLE world_entity ADD COLUMN subtype VARCHAR")
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_world_entity_subtype ON world_entity (subtype)")
                    print("[Startup] Migrated world_entity: added subtype")

                if "status" not in existing:
                    conn.exec_driver_sql("ALTER TABLE world_entity ADD COLUMN status VARCHAR")
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_world_entity_status ON world_entity (status)")
                    print("[Startup] Migrated world_entity: added status")

                if "character_id" not in existing:
                    conn.exec_driver_sql("ALTER TABLE world_entity ADD COLUMN character_id INTEGER")
                    conn.exec_driver_sql(
                        "CREATE INDEX IF NOT EXISTS ix_world_entity_character_id ON world_entity (character_id)")
                    print("[Startup] Migrated world_entity: added character_id")

                # Older DBs may have a partial character schema. Add missing
                # columns used by current queries/endpoints.
                c_rows = conn.exec_driver_sql('PRAGMA table_info("character")').fetchall()
                c_existing = {str(r[1]).lower() for r in c_rows}
                char_cols: list[tuple[str, str]] = [
                    ("subclass", "TEXT"),
                    ("xp", "INTEGER DEFAULT 0"),
                    ("pending_level_up", "INTEGER DEFAULT 0"),
                    ("is_npc", "INTEGER DEFAULT 0"),
                    ("controller", "TEXT DEFAULT 'player'"),
                    ("cp", "INTEGER DEFAULT 0"),
                    ("sp", "INTEGER DEFAULT 0"),
                    ("ep", "INTEGER DEFAULT 0"),
                    ("gp", "INTEGER DEFAULT 0"),
                    ("pp", "INTEGER DEFAULT 0"),
                    ("lifestyle", "TEXT DEFAULT 'modest'"),
                    ("max_hp", "INTEGER DEFAULT 0"),
                    ("current_hp", "INTEGER DEFAULT 0"),
                    ("temp_hp", "INTEGER DEFAULT 0"),
                    ("spell_slots_used", "JSON"),
                    ("hit_die", "TEXT DEFAULT 'd8'"),
                    ("hit_dice_total", "INTEGER DEFAULT 1"),
                    ("hit_dice_remaining", "INTEGER DEFAULT 1"),
                    ("exhaustion", "INTEGER DEFAULT 0"),
                    ("rations", "INTEGER DEFAULT 0"),
                    ("water", "INTEGER DEFAULT 0"),
                    ("days_without_food", "INTEGER DEFAULT 0"),
                    ("days_without_water", "INTEGER DEFAULT 0"),
                    ("death_save_successes", "INTEGER DEFAULT 0"),
                    ("death_save_failures", "INTEGER DEFAULT 0"),
                    ("stable", "INTEGER DEFAULT 1"),
                    ("inspiration", "INTEGER DEFAULT 0"),
                    ("tags", "JSON"),
                    ("stats", "JSON"),
                    ("spells", "JSON"),
                    ("inventory", "JSON"),
                    ("conditions", "JSON"),
                    ("ddb_url", "TEXT"),
                    ("avrae_import_text", "TEXT"),
                    ("last_verified_at", "DATETIME"),
                    ("approved", "INTEGER DEFAULT 0"),
                    ("home_region", "TEXT"),
                    ("background", "TEXT"),
                    ("notes", "TEXT"),
                    ("created_at", "DATETIME"),
                    ("updated_at", "DATETIME"),
                ]
                for col, ddl in char_cols:
                    if col not in c_existing:
                        conn.exec_driver_sql(f'ALTER TABLE "character" ADD COLUMN {col} {ddl}')
                        print(f"[Startup] Migrated character: added {col}")

                conn.exec_driver_sql(
                    'CREATE INDEX IF NOT EXISTS ix_character_is_npc ON "character" (is_npc)')
                conn.exec_driver_sql(
                    'CREATE INDEX IF NOT EXISTS ix_character_approved ON "character" (approved)')

                # Combat tracker columns added after the table first shipped
                # (pre-existing DBs): cover, spacing band, per-turn economy.
                cb_existing = {row[1] for row in conn.exec_driver_sql(
                    'PRAGMA table_info("combat_combatant")')}
                if cb_existing:
                    for col, ddl in [("cover", "VARCHAR DEFAULT 'none'"),
                                     ("position", "VARCHAR"),
                                     ("action_used", "BOOLEAN DEFAULT 0"),
                                     ("bonus_used", "BOOLEAN DEFAULT 0"),
                                     ("reaction_used", "BOOLEAN DEFAULT 0"),
                                     ("move_left", "INTEGER DEFAULT 1"),
                                     ("dodging", "BOOLEAN DEFAULT 0"),
                                     ("disengaging", "BOOLEAN DEFAULT 0"),
                                     ("attacks_made", "INTEGER DEFAULT 0"),
                                     ("sneak_used", "BOOLEAN DEFAULT 0"),
                                     ("used_features", "JSON"),
                                     ("pending_saves", "JSON")]:
                        if col not in cb_existing:
                            conn.exec_driver_sql(
                                f"ALTER TABLE combat_combatant ADD COLUMN {col} {ddl}")
                            print(f"[Startup] Migrated combat_combatant: added {col}")
                ce_existing = {row[1] for row in conn.exec_driver_sql(
                    'PRAGMA table_info("combat_encounter")')}
                if ce_existing and "pending_reaction" not in ce_existing:
                    conn.exec_driver_sql(
                        "ALTER TABLE combat_encounter ADD COLUMN pending_reaction JSON")
                    print("[Startup] Migrated combat_encounter: added pending_reaction")

                # 2024 species columns on pre-existing rules_race tables.
                rr_existing = {row[1] for row in conn.exec_driver_sql(
                    'PRAGMA table_info("rules_race")')}
                if rr_existing:
                    for col, ddl in [("lineages", "JSON"),
                                     ("lineage_label", "VARCHAR"),
                                     ("feat_choice", "VARCHAR")]:
                        if col not in rr_existing:
                            conn.exec_driver_sql(
                                f"ALTER TABLE rules_race ADD COLUMN {col} {ddl}")
                            print(f"[Startup] Migrated rules_race: added {col}")

                # Ratchet-clock + entropy + calendar columns on world_meta
                # (pre-existing DBs). Calendar defaults mirror WorldMeta.
                wm_existing = {row[1] for row in
                               conn.exec_driver_sql('PRAGMA table_info("world_meta")')}
                if wm_existing:
                    for col, ddl in [("real_anchor_ts", "REAL"),
                                     ("anchor_world_day", "INTEGER DEFAULT 0"),
                                     ("last_entropy_day", "INTEGER DEFAULT 0"),
                                     ("year", "INTEGER DEFAULT 1492"),
                                     ("month", "INTEGER DEFAULT 1"),
                                     ("day_of_month", "INTEGER DEFAULT 1"),
                                     ("time_of_day", "VARCHAR DEFAULT 'morning'")]:
                        if col not in wm_existing:
                            conn.exec_driver_sql(
                                f'ALTER TABLE "world_meta" ADD COLUMN {col} {ddl}')
                            print(f"[Startup] Migrated world_meta: added {col}")
    except Exception as e:
        print(f"[Startup] World schema self-heal skipped: {e}")

    # Seed the persistent world once (offline, idempotent). Minimal by design:
    # just the starting town, its tavern, and the frontier stubs — everything
    # else grows through play via the extraction loop and its world laws.
    try:
        world.create_tables()
        if world.get_entity("greenfields") is None:
            seed_minimal_world(world)
            print("[Startup] Seeded minimal world (Greenfields origin)")
        else:
            # Worlds from before spherical coords get positions for the known
            # seed places so climate/bearings work; no-op once filled.
            filled = backfill_coords(world)
            if filled:
                print(f"[Startup] Backfilled coords on {filled} legacy place(s)")
    except Exception as e:
        print(f"[Startup] World seed skipped: {e}")

    # Owned-book / homebrew content the operator typed in (rules/homebrew.json)
    # joins the rules tables — shops, guardrails, and exact-numbers injection
    # all see it. See rules/homebrew.sample.json for the format.
    try:
        from rules.homebrew import load_homebrew
        load_homebrew(engine=engine)
    except Exception as e:
        print(f"[Startup] Homebrew load skipped: {e}")

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

    # Seed playable races + class skill lists for deterministic character
    # creation — offline & idempotent (also self-heals rules_class columns).
    try:
        from rules.ingest import (seed_races, seed_classes_and_subclasses,
                                   seed_feats)
        seeded_r = seed_races(engine=engine)
        seed_classes_and_subclasses(engine=engine)
        seeded_f = seed_feats(engine=engine)
        print(f"[Startup] Seeded CC data: {seeded_r} | {seeded_f}")
        # Apply curated exotic-species overrides if the local (gitignored)
        # file is present — surfaces book species the parser can't read.
        try:
            from rules.owned_ingest import (ingest_species_overrides,
                                             ingest_feats_overrides)
            ov = ingest_species_overrides(engine=engine)
            if ov.get("overrides_applied") or ov.get("overrides_new"):
                print(f"[Startup] Species overrides: {ov}")
            fo = ingest_feats_overrides(engine=engine)
            if fo.get("feat_overrides_applied") or fo.get("feat_overrides_new"):
                print(f"[Startup] Feat overrides: {fo}")
        except Exception as e:
            print(f"[Startup] Overrides skipped: {e}")
    except Exception as e:
        print(f"[Startup] CC data seed skipped: {e}")

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
    # Optional: the bot's chat callers key sessions on session_id and don't
    # always supply these (e.g. guided character creation). The /chat handler
    # doesn't use them, so keep them optional to avoid 422 rejections.
    channel_id: Optional[str] = None
    guild_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    # Optional AI-recommended ambient-music search query for the current scene.
    music: Optional[str] = None
    # Optional scene pictures (base64 WebP + metadata) for the bot to attach.
    images: Optional[List[Dict[str, Any]]] = None
    # Set once, on the turn a PC's death is confirmed: a one-page life record
    # the bot posts to the memorial channel. {"title": str, "text": str}
    memorial: Optional[Dict[str, Any]] = None


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
    # Opening scene picture(s) — same transport payload as chat images.
    images: Optional[List[dict]] = None


class RegisterCharacterRequest(BaseModel):
    discord_user_id: str
    name: str
    race: Optional[str] = None
    char_class: Optional[str] = None
    subclass: Optional[str] = None
    level: int = 1
    stats: Optional[Dict[str, int]] = None
    background: Optional[str] = None
    ddb_url: Optional[str] = None
    avrae_import_text: Optional[str] = None
    approve: Optional[bool] = False
    home_region: Optional[str] = None
    source: Optional[str] = "manual"  # one of: 'avrae', 'guided', 'manual', 'ddb'
    # Skill proficiencies chosen at creation (deterministic CC wizard).
    skills: Optional[List[str]] = None
    # Feats granted at creation (e.g. Custom Lineage origin feat).
    feats: Optional[List[str]] = None
    # Starting gear choice: "kit" = the class/background package (default);
    # "buy" = skip those and spend class starting gold on `bought_items`.
    gear_mode: Optional[str] = "kit"
    # [{"name": str, "quantity": int}] purchased in buy mode.
    bought_items: Optional[List[Dict[str, Any]]] = None
    # A free common wondrous item chosen at creation (rules_item slug).
    wondrous_item: Optional[str] = None


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
    updated_at: datetime = Field(default_factory=_utcnow)


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
# Deterministic turn engine on top of it: validates intents, rolls real dice,
# enforces action economy and spacing; the LLM only proposes and narrates.
combat_engine = CombatEngine(combat)
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
        "updated_at": _utcnow(),
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
    state["updated_at"] = row.updated_at or _utcnow()
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
    state["updated_at"] = _utcnow()
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


def _table_is_mature(session_id: str) -> bool:
    """Whether this table has opted into mature (NSFW-capable) scene art.

    Off by default and per-table — a shared Discord surface, so mature imagery
    must be explicitly enabled for a table, never global. Gated further by
    ``ImageryConfig.checkpoint_mature`` being configured (the switch is a no-op
    without a mature model installed)."""
    try:
        meta = _load_session_state(session_id).get("meta", {}) or {}
        return bool(meta.get("mature", False))
    except Exception:
        return False


def _set_table_mature(session_id: str, enabled: bool) -> bool:
    state = _load_session_state(session_id)
    meta = dict(state.get("meta", {}) or {})
    meta["mature"] = bool(enabled)
    _set_session_meta(session_id, meta)
    return bool(enabled)


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
                {"role": "system", "content": "You are a precise context compressor for an RPG memory ledger. Always respond in English only."},
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
    background: Optional[str] = Field(default=None, sa_column=Column(String))
    level: int = Field(default=1)
    xp: int = Field(default=0)
    # Flag: character has XP to level up but hasn't taken level-up options yet.
    # Must resolve via /level_up before continuing adventure.
    pending_level_up: bool = Field(default=False)

    # PC vs NPC discriminator. NPCs are FULL characters — same class/subclass/
    # level/feature machinery as PCs — distinguished only by this flag. ``controller``
    # records who runs an NPC once it joins a party: "player" or "dm" (PCs are always
    # player-run). The world-graph NPC entity holds the narrative/relational layer
    # (location, trust, party membership) and links back here by character id.
    is_npc: bool = Field(default=False, index=True)
    controller: str = Field(default="player", sa_column=Column(String))

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
    temp_hp: int = Field(default=0)     # temporary hit points (buffer over current_hp)
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
    # Expended spell slots since the last rest: {"1": 2, "3": 1} (slot level ->
    # used count). Cleared on a long rest; warlock pact slots clear on short.
    spell_slots_used: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Persistent conditions/status effects that carry BETWEEN encounters (e.g.
    # poisoned until a save, frightened, a lingering curse). Exhaustion is tracked
    # separately above as a level. Each entry: {name, source?, note?, duration?,
    # clears_on_long_rest?, applied_day?}.
    conditions: Optional[Any] = Field(default=None, sa_column=Column(JSON))

    # Source/sync
    ddb_url: Optional[str] = Field(default=None, sa_column=Column(String))
    avrae_import_text: Optional[str] = Field(default=None, sa_column=Column(String))
    last_verified_at: Optional[datetime] = Field(default=None)
    approved: bool = Field(default=False, index=True)

    # Game metadata
    home_region: Optional[str] = Field(default=None, sa_column=Column(String))
    notes: Optional[str] = Field(default=None, sa_column=Column(String))

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


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

# Structured roll capture for the Activity UI: when set (per task/thread via
# contextvars), every resolved hook also appends a dict here so the web client
# can render roll CARDS at the exact spots the inline "🎲 …" text sits.
import contextvars as _contextvars
_ACTIVITY_ROLLS: _contextvars.ContextVar = _contextvars.ContextVar(
    "activity_rolls", default=None)


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
        collector = _ACTIVITY_ROLLS.get()
        try:
            compact = expr.replace(" ", "")
            m = _D20_EXPR.match(compact)
            if m and dc is not None:
                mod = int(m.group(1)) if m.group(1) else 0
                res = ability_check(mod, dc=dc, label=label)
                out = f"\U0001F3B2 {res.detail}"
                if collector is not None:
                    collector.append({
                        "expr": compact, "label": label or None, "dc": dc,
                        "total": res.total, "detail": res.detail,
                        "success": res.success, "marker": out,
                    })
                return out
            r = dice_roll(expr)
            lbl = f"{label}: " if label else ""
            out = f"\U0001F3B2 {lbl}{r.detail}"
            if collector is not None:
                collector.append({
                    "expr": expr, "label": label or None, "dc": None,
                    "total": r.total, "detail": r.detail,
                    "success": None, "marker": out,
                })
            return out
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


def _scene_reference_refs(subject: str, pc_name: Optional[str],
                          ctx_entities: Optional[list]) -> list[tuple[str, str]]:
    """Which stored art should guide a scene render: participants NAMED in it.

    The PC (portrait) when the scene mentions them or speaks in first person,
    plus any in-scene world NPCs and any SRD creatures named in the subject.
    """
    refs: list[tuple[str, str]] = []
    text = (subject or "").lower()
    if pc_name:
        first = pc_name.split()[0].lower()
        if first in text or pc_name.lower() in text or re.search(r"\b(my|me|i)\b", text):
            refs.append(("pc", pc_name))
    for e in ctx_entities or []:
        if getattr(e, "type", "") == "npc" and e.name.lower() in text:
            refs.append(("npc", e.slug))
    try:
        for kind, obj in rules_lib.find_mentions(subject, limit=3):
            if kind == "monster":
                refs.append(("creature", obj.index_slug))
    except Exception:
        pass
    return refs


def process_image_hooks(image_reqs: list[dict], reset_reqs: list[dict],
                        *, pc_name: Optional[str] = None,
                        ctx_entities: Optional[list] = None,
                        mature: bool = False) -> list[dict]:
    """Apply image-reset purges and render/reuse pictures for image requests.

    Returns a list of transport payloads (base64 image + metadata) for the bot,
    capped by ``ImageryConfig.max_images_per_reply``. Offline results are skipped
    so nothing broken is shown to players.

    ``kind: scene`` requests are special: rendered fresh (never bucketed) with
    stored art of their named participants as visual references, so the scene
    depicts THESE people and creatures, not generic lookalikes.
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
            if (rq.get("kind") or "").strip().lower() == "scene":
                refs = _scene_reference_refs(subject, pc_name, ctx_entities)
                result = image_store.generate_scene(
                    subject, context=rq.get("context") or "",
                    extra=rq.get("look") or "", reference_refs=refs,
                    mature=mature,
                )
            else:
                result = image_store.ensure_image(
                    rq.get("kind") or "creature", subject,
                    look=rq.get("look") or "", context=rq.get("context") or "",
                    mature=mature,
                )
        except Exception as e:
            print(f"[imagery] generation error for '{subject}': {e}")
            continue
        if result is None or result.offline:
            continue
        payloads.append(result.payload())
    return payloads


# The DM applies or clears a lasting condition on the PLAYER outside the combat
# tracker (so it persists between encounters):
#   [[CONDITION: add | poisoned | giant spider venom | until long rest]]
#   [[CONDITION: remove | frightened]]
#   [[CONDITION: clear]]
# Fields: action(add|remove|clear) | name | source (optional) | duration (optional)
# Player cartography (SRD Cartographer's Tools): after adjudicating the
# Wisdom (DC 15) roll, the DM emits the outcome and the system renders the
# artifact — accurate on a success, confidently WRONG on a failure:
#   [[MAP: draft-success | the lands around Millbrook]]
#   [[MAP: draft-failure | the lands around Millbrook]]
#   [[MAP: purchase | Greenfields]]         (bought from a map-maker, ~25 gp)
MAP_HOOK_PATTERN = re.compile(r"\[\[MAP:(.+?)\]\]", re.IGNORECASE)


def extract_map_hooks(text: str) -> tuple[str, list[dict]]:
    ops: list[dict] = []
    for m in MAP_HOOK_PATTERN.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        action = (parts[0] if parts else "").lower()
        if action in ("draft-success", "draft-failure", "purchase"):
            ops.append({"action": action,
                        "area": parts[1] if len(parts) > 1 else ""})
    clean = MAP_HOOK_PATTERN.sub("", text)
    return clean.strip(), ops


def process_map_hooks(map_ops: list[dict], session_id: str) -> list[dict]:
    """Turn MAP hooks into rendered parchment artifacts + inventory items.

    Enforces the hard prerequisites the prompt also states: drafting needs
    Cartographer's Tools in inventory; purchase needs the coin. A failed
    draft is rendered with deterministic distortion — the caption gives
    nothing away.
    """
    if not map_ops:
        return []
    from eight_card_system import mapmaker
    payloads: list[dict] = []
    state = _load_session_state(session_id)
    meta = state.get("meta", {}) or {}
    pc_slug, char_id = meta.get("pc_slug"), meta.get("character_id")
    if not pc_slug or not char_id:
        return []
    day = world.current_day()

    with Session(engine) as s:
        char = s.get(Character, char_id)
        if char is None:
            return []
        for op in map_ops[:2]:
            action, area = op["action"], op["area"] or "the surrounding lands"
            if action.startswith("draft"):
                if not _has_inventory_item(char, mapmaker.TOOLS_ITEM):
                    print(f"[map] {char.name} lacks {mapmaker.TOOLS_ITEM} — draft refused")
                    continue
                flawed = action == "draft-failure"
                # You can only draw what you KNOW: visited places and places
                # learned of in play (knows_about). Someone else's discoveries
                # don't appear on your parchment.
                known = mapmaker.known_place_ids(world, pc_slug)
                center, places = mapmaker.gather_mappable_places(
                    world, pc_slug, radius_mi=mapmaker.DRAFT_RADIUS_MI,
                    known_ids=known)
                subtitle = f"drafted by {char.name}, {world.current_date_str()}"
            else:  # purchase
                price = mapmaker.MAP_PRICE_GP
                if (char.gp or 0) < price:
                    print(f"[map] {char.name} can't afford the {price} gp map")
                    continue
                char.gp -= price
                flawed = False
                center, places = mapmaker.gather_mappable_places(
                    world, pc_slug, radius_mi=mapmaker.PURCHASE_RADIUS_MI,
                    include_rumored=True)
                subtitle = "by a map-maker's practiced hand"
            if center is None or not places:
                print(f"[map] nothing mappable around {pc_slug}")
                continue
            title = f"Map of {area}"
            png = mapmaker.render_map(
                places, center, title=title, flawed=flawed,
                seed=f"{pc_slug}:{day}:{area}", subtitle=subtitle)
            if action == "purchase" and pc_slug:
                # Buying the map IS gaining the knowledge: every charted site
                # (rumored ones included) becomes drawable on your own drafts.
                for p in places:
                    if p.get("slug"):
                        world.add_relation(pc_slug, RelationType.KNOWS_ABOUT, p["slug"])
            _add_inventory_item(char, title, extra={
                "map": {"area": area, "day": day,
                        "provenance": "drafted" if action.startswith("draft") else "purchased",
                        # The truth lives in the data; the player never sees it.
                        "reliable": not flawed}})
            payloads.append({
                "b64": base64.b64encode(png).decode("ascii"),
                "mime": "image/png", "kind": "map",
                "caption": title, "temp": False,
            })
            print(f"[map] {char.name}: {action} '{title}' "
                  f"({len(places)} sites{', flawed' if flawed else ''})")
        s.add(char)
        s.commit()
    return payloads


# Commerce: the DM narrates the deal, the system moves the actual coin/items.
#   [[TRADE: buy | Longsword | 15]]      (price cross-checked vs rolled stock)
#   [[TRADE: sell | wolf pelt | 2]]
#   [[TRADE: wager | dice | 5]]          (needs a den keeper/gambling venue)
TRADE_HOOK_PATTERN = re.compile(r"\[\[TRADE:(.+?)\]\]", re.IGNORECASE)


def extract_trade_hooks(text: str) -> tuple[str, list[dict]]:
    ops: list[dict] = []
    for m in TRADE_HOOK_PATTERN.finditer(text):
        parts = [p.strip() for p in m.group(1).split("|")]
        action = (parts[0] if parts else "").lower()
        if action in ("buy", "sell", "wager") and len(parts) >= 2:
            try:
                amount = float(parts[2]) if len(parts) > 2 else 0.0
            except ValueError:
                amount = 0.0
            ops.append({"action": action, "item": parts[1], "amount": amount})
    return TRADE_HOOK_PATTERN.sub("", text).strip(), ops


def _purse_of(char: Character) -> Dict[str, int]:
    return {"cp": char.cp or 0, "sp": char.sp or 0, "ep": char.ep or 0,
            "gp": char.gp or 0, "pp": char.pp or 0}


def _write_purse(char: Character, purse: Dict[str, int]) -> None:
    char.cp, char.sp, char.ep = purse.get("cp", 0), purse.get("sp", 0), purse.get("ep", 0)
    char.gp, char.pp = purse.get("gp", 0), purse.get("pp", 0)


def process_trade_hooks(trade_ops: list[dict], session_id: str,
                        ctx_obj) -> list[str]:
    """Execute agreed trades against rolled stock and the real purse.

    Returns short system lines appended to the narration (purse deltas,
    wager outcomes). Refuses hallucinated bargains: buys must match a
    present merchant's actual stock, wagers need a gambling presence.
    """
    if not trade_ops:
        return []
    from eight_card_system import shops
    notes: list[str] = []
    state = _load_session_state(session_id)
    meta = state.get("meta", {}) or {}
    char_id, pc_slug = meta.get("character_id"), meta.get("pc_slug")
    if not char_id:
        return []
    day = world.current_day()

    # Merchants present in the scene, with this week's actual stock.
    merchants: list[tuple[Any, list[dict]]] = []
    gambling_present = False
    for e in (ctx_obj.entities if ctx_obj else []):
        attrs = e.attributes or {}
        if getattr(e, "type", "") == "npc":
            role = str(attrs.get("role", "")).strip().lower()
            if role in ("den keeper", "dice-den keeper"):
                gambling_present = True
            if role in shops.MERCHANT_ROLES:
                merchants.append(
                    (e, shops.roll_stock(e.slug, role, ctx_obj.merchant_scale(e), day)))
        elif attrs.get("venue") == "gambling den":
            gambling_present = True

    with Session(engine) as s:
        char = s.get(Character, char_id)
        if char is None:
            return []
        for op in trade_ops[:3]:
            action, item, amount = op["action"], op["item"], op["amount"]
            if action == "buy":
                hit = None
                for merch, stock in merchants:
                    found = shops.find_in_stock(stock, item)
                    if found:
                        hit = (merch, found)
                        break
                if hit is None:
                    print(f"[trade] refused buy '{item}': no merchant here stocks it")
                    continue
                merch, entry = hit
                cost_cp = gp_to_cp(entry["price_gp"])
                purse = _purse_of(char)
                if to_cp(purse) < cost_cp:
                    notes.append(f"*(Your purse comes up short for the {entry['name']}.)*")
                    continue
                _write_purse(char, subtract_cost(purse, cost_cp))
                _add_inventory_item(char, entry["name"])
                world.add_event(f"{char.name} bought {entry['name']} from {merch.name}.",
                                involved=[pc_slug] if pc_slug else [])
                notes.append(f"*({entry['name']} acquired — {entry['price_gp']:g} gp. "
                             f"Purse: {format_purse(_purse_of(char))}.)*")
            elif action == "sell":
                if not _has_inventory_item(char, item):
                    print(f"[trade] refused sell '{item}': not in inventory")
                    continue
                # SRD spirit: used gear fetches half-ish; clamp DM enthusiasm.
                price_gp = max(0.0, min(float(amount), 200.0))
                inv = _inventory_items(char)
                for it in inv:
                    if _normalize_item_name(str(it.get("name", ""))) == _normalize_item_name(item):
                        qty = int(it.get("quantity", 1) or 1)
                        if qty > 1:
                            it["quantity"] = qty - 1
                        else:
                            inv.remove(it)
                        char.inventory = inv
                        break
                _write_purse(char, add_coins(_purse_of(char),
                                             {"cp": gp_to_cp(price_gp)}))
                notes.append(f"*({item} sold — {price_gp:g} gp. "
                             f"Purse: {format_purse(_purse_of(char))}.)*")
            elif action == "wager":
                if not gambling_present:
                    print("[trade] refused wager: no gambling den or den keeper here")
                    continue
                stake_gp = int(max(1, min(amount or 1, 50)))
                stake_cp = gp_to_cp(stake_gp)
                purse = _purse_of(char)
                if to_cp(purse) < stake_cp:
                    notes.append("*(You can't cover that stake.)*")
                    continue
                player = dice_roll("1d20").total
                house = dice_roll("1d20").total
                if player > house:
                    _write_purse(char, add_coins(purse, {"cp": stake_cp}))
                    notes.append(f"*(The dice fall {player} to {house} — you WIN "
                                 f"{stake_gp} gp. Purse: {format_purse(_purse_of(char))}.)*")
                else:  # house wins ties: that's why the den keeps its lamps lit
                    _write_purse(char, subtract_cost(purse, stake_cp))
                    notes.append(f"*(The dice fall {player} to {house} — the house "
                                 f"takes {stake_gp} gp. Purse: {format_purse(_purse_of(char))}.)*")
        s.add(char)
        s.commit()
    return notes


CONDITION_HOOK_PATTERN = re.compile(r"\[\[CONDITION:(.+?)\]\]", re.IGNORECASE)


def extract_condition_hooks(text: str) -> tuple[str, list[dict]]:
    """Pull persistent-condition hooks out of the narration.

    Returns ``(clean_text, ops)`` where each op is a dict describing an add/
    remove/clear. Hooks are stripped from what the player sees.
    """
    ops: list[dict] = []
    for m in CONDITION_HOOK_PATTERN.finditer(text):
        parts = _split_hook(m.group(1))
        if not parts:
            continue
        action = (parts[0] or "add").lower()
        op: dict = {"action": action}
        if action != "clear":
            op["name"] = parts[1] if len(parts) > 1 else ""
            if not op["name"]:
                continue
            if len(parts) > 2 and parts[2]:
                op["source"] = parts[2]
            if len(parts) > 3 and parts[3]:
                op["duration"] = parts[3]
        ops.append(op)

    clean = CONDITION_HOOK_PATTERN.sub("", text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, ops


def apply_condition_hooks(character_id: Optional[int], ops: list[dict]) -> list[str]:
    """Persist condition add/remove/clear ops to the character. Returns notes."""
    if not character_id or not ops:
        return []
    notes: list[str] = []
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            return []
        changed = False
        for op in ops:
            if _apply_condition_op(char, op):
                changed = True
                action = (op.get("action") or "add").lower()
                if action == "clear":
                    notes.append("conditions cleared")
                elif action == "remove":
                    notes.append(f"no longer {_normalize_condition_name(op.get('name',''))}")
                else:
                    notes.append(f"now {_normalize_condition_name(op.get('name',''))}")
        if changed:
            session.add(char)
            session.commit()
    return notes


# The DM records a shift in an NPC's running trust toward the party during
# roleplay (kindness, betrayal, shared danger, insults, favors kept or broken):
#   [[TRUST: Marta Fenn | +5 | the party saved her tavern]]
#   [[TRUST: Old Ferran | -3 | they mocked his warnings]]
# Fields: npc (slug or name) | signed delta | reason (optional)
TRUST_HOOK_PATTERN = re.compile(r"\[\[TRUST:(.+?)\]\]", re.IGNORECASE)


def extract_trust_hooks(text: str) -> tuple[str, list[dict]]:
    """Pull NPC trust-shift hooks out of the narration. Returns (clean, ops)."""
    ops: list[dict] = []
    for m in TRUST_HOOK_PATTERN.finditer(text):
        parts = _split_hook(m.group(1))
        if len(parts) < 2:
            continue
        npc = (parts[0] or "").strip()
        raw = (parts[1] or "").strip().replace("+", "")
        try:
            delta = int(raw)
        except ValueError:
            continue
        if not npc or delta == 0:
            continue
        op = {"npc": npc, "delta": delta}
        if len(parts) > 2 and parts[2]:
            op["reason"] = parts[2].strip()
        ops.append(op)
    clean = TRUST_HOOK_PATTERN.sub("", text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, ops


def apply_trust_hooks(character_id: Optional[int], ops: list[dict]) -> list[str]:
    """Apply NPC trust shifts toward the session's PC. Returns short notes."""
    if not character_id or not ops:
        return []
    notes: list[str] = []
    with Session(engine) as session:
        pc_char = session.get(Character, character_id)
        if not pc_char:
            return []
        pc_slug = _pc_entity_slug(pc_char)
        for op in ops:
            ent = world.get_entity(op["npc"])
            if not ent or ent.type != EntityType.NPC:
                continue
            result = world.adjust_trust(ent.slug, pc_slug, int(op["delta"]),
                                        reason=op.get("reason", ""))
            if result:
                notes.append(f"{ent.name} -> {result['attitude']} (trust {result['trust']})")
    return notes


# The DM runs fights through the initiative tracker (combat/) with COMBAT hooks.
# ``start`` auto-seats the table's PCs; initiative is rolled once the reply's
# adds are in. PC damage/healing on the tracker is mirrored onto the character
# sheet, so the Activity HP bar stays truthful.
#   [[COMBAT: start | Ambush at the mill]]
#   [[COMBAT: add | goblin | x2]]                      (SRD monster by name)
#   [[COMBAT: add | Bandit Captain | hp 65 | ac 15]]   (custom foe)
#   [[COMBAT: damage | Goblin 2 | 5]]   [[COMBAT: heal | Kara | 7]]
#   [[COMBAT: temp | Kara | 10]]
#   [[COMBAT: condition | Goblin 1 | poisoned]]  [[COMBAT: uncondition | Goblin 1 | poisoned]]
#   [[COMBAT: next]]   [[COMBAT: end]]
COMBAT_HOOK_PATTERN = re.compile(r"\[\[COMBAT:(.+?)\]\]", re.IGNORECASE)

_COMBAT_HOOK_ACTIONS = {"start", "add", "damage", "heal", "temp", "condition",
                        "uncondition", "cover", "move", "next", "end"}

_COVER_LEVELS = {"none": "none", "half": "half", "three-quarters": "three-quarters",
                 "3/4": "three-quarters", "three quarters": "three-quarters",
                 "threequarters": "three-quarters", "total": "total", "full": "total"}

# Always-on nudge so the DM knows how to OPEN a fight (kept short, like
# _CONDITIONS_SHORT); the full verb list only ships while a fight is live.
_COMBAT_HOOKS_IDLE = (
    "# Combat hooks\n"
    "When a real fight breaks out (not a mere threat), open the initiative tracker:\n"
    "    [[COMBAT: start | short encounter name]]\n"
    "    [[COMBAT: add | goblin | x2]]   (SRD monster; or add | Name | hp 20 | ac 13 for a custom foe)\n"
    "The party is seated automatically and initiative is rolled for everyone. Narrate as usual.\n"
)

_COMBAT_HOOKS_ACTIVE = (
    "# Combat hooks (a fight is underway — keep the tracker true)\n"
    "Emit these alongside your narration; they are applied and hidden from players:\n"
    "    [[COMBAT: damage | target | 7]]      damage dealt (temp HP absorbs first)\n"
    "    [[COMBAT: heal | target | 5]]        healing received\n"
    "    [[COMBAT: temp | target | 10]]       temporary hit points granted\n"
    "    [[COMBAT: condition | target | poisoned]] / [[COMBAT: uncondition | target | poisoned]]\n"
    "    [[COMBAT: move | target | melee with Kara]]   spacing band: 'melee with <name>' | 'near' | 'far'\n"
    "    [[COMBAT: cover | target | half]]    cover: none | half | three-quarters | total\n"
    "    [[COMBAT: add | wolf | x1]]          reinforcements arrive\n"
    "    [[COMBAT: next]]                     the current creature's turn is resolved\n"
    "    [[COMBAT: end]]                      the fight is over (victory, flight, or surrender)\n"
    "Follow the board's initiative order: on a monster's or NPC's turn, run it in narration and\n"
    "record its outcome with damage/condition hooks, then emit [[COMBAT: next]]. Use [[ROLL: ...]]\n"
    "for attacks and saves as normal — COMBAT hooks record the OUTCOME on the tracker.\n"
    "\n"
    "Action economy — enforce strictly, every turn, PCs and monsters alike:\n"
    "- One ACTION (Attack, Dash, Disengage, Dodge, Help, Hide, Ready, cast a spell, use an\n"
    "  item). Multiattack is ONE action — follow the stat block's routine exactly.\n"
    "- At most one BONUS ACTION, and only when a feature explicitly grants one. A creature\n"
    "  that casts a leveled spell as a bonus action can only cast a cantrip with its action.\n"
    "- MOVEMENT up to Speed, split freely around its action; difficult terrain costs double;\n"
    "  standing from prone costs half.\n"
    "- One REACTION per round (e.g. an opportunity attack when a foe leaves melee reach\n"
    "  without Disengaging); it refreshes at the start of the creature's own turn.\n"
    "Never grant a second action, an extra attack beyond the stat block, or two leveled\n"
    "spells in one turn — however dramatic the moment.\n"
    "\n"
    "Spacing (theater of the mind, tracked in bands): the board shows each creature's band\n"
    "after '@'. Keep it true with move hooks whenever someone closes, retreats, or is pushed.\n"
    "'melee with <name>' = adjacent, in reach; 'near' = one normal move away (~30 ft);\n"
    "'far' = needs a Dash or a ranged attack to matter this turn. Consequences to honor:\n"
    "ranged attacks while in melee have disadvantage; leaving melee without Disengage\n"
    "provokes an opportunity attack; melee attacks require being in melee with the target.\n"
)


def extract_combat_hooks(text: str) -> tuple[str, list[dict]]:
    """Pull combat-tracker hooks out of the narration. Returns (clean, ops)."""
    ops: list[dict] = []
    for m in COMBAT_HOOK_PATTERN.finditer(text):
        parts = _split_hook(m.group(1))
        if not parts:
            continue
        action = (parts[0] or "").lower()
        if action not in _COMBAT_HOOK_ACTIONS:
            continue
        ops.append({"action": action, "args": parts[1:]})
    clean = COMBAT_HOOK_PATTERN.sub("", text)
    clean = re.sub(r"\n{3,}", "\n\n", clean).strip()
    return clean, ops


def _combat_find(encounter_id: int, ref: str):
    """Resolve a combatant by name: exact, then prefix, then substring."""
    ref_l = (ref or "").strip().lower()
    if not ref_l:
        return None
    order = combat.order(encounter_id)
    for c in order:
        if c.name.lower() == ref_l:
            return c
    for c in order:
        if c.name.lower().startswith(ref_l):
            return c
    for c in order:
        if ref_l in c.name.lower():
            return c
    return None


def _combat_mirror_pc(combatant_id: int) -> None:
    """Copy a PC combatant's HP pools back onto the character sheet."""
    c = combat.get_combatant(combatant_id)
    if not c or not c.character_id:
        return
    with Session(engine) as s:
        char = s.get(Character, c.character_id)
        if not char:
            return
        char.current_hp = c.current_hp
        char.temp_hp = c.temp_hp
        s.add(char)
        s.commit()


def _combat_seat_pc(encounter_id: int, char: "Character"):
    """Seat one PC at the encounter with their real current HP / temp HP / AC."""
    dex = ability_modifier(_ability_score(char, "dexterity"))
    c = combat.add_pc(
        encounter_id, name=char.name, max_hp=char.max_hp,
        armor_class=_compute_ac(char), dex_mod=dex, character_id=char.id)
    if char.current_hp < char.max_hp:
        combat.apply_damage(c.id, char.max_hp - char.current_hp)
    temp = int(getattr(char, "temp_hp", 0) or 0)
    if temp:
        combat.set_temp_hp(c.id, temp)
    return c


def _combat_enroll_pcs(encounter_id: int, session_id: str) -> None:
    """Seat every member PC of the session at the encounter, carrying their
    real current HP / temp HP / AC in from the character sheet."""
    state = _load_session_state(session_id)
    members = _session_members(state.get("meta", {}) or {})
    existing = {c.character_id for c in combat.order(encounter_id) if c.character_id}
    with Session(engine) as s:
        for m in members.values():
            cid = m.get("character_id")
            if not cid or cid in existing:
                continue
            char = s.get(Character, cid)
            if not char or char.current_hp <= 0:
                continue
            _combat_seat_pc(encounter_id, char)


def apply_combat_hooks(session_id: str, ops: list[dict]) -> list[str]:
    """Apply COMBAT hooks to the session's encounter. Returns short notes
    appended to the narration (initiative line, downed foes, fight over)."""
    if not ops:
        return []
    notes: list[str] = []
    enc = combat.get_active(session_id)
    roster_changed = False
    started_now = False
    for op in ops:
        action = op["action"]
        args = op.get("args") or []
        try:
            if action == "start":
                if enc is None:
                    name = (args[0] if args and args[0] else "Encounter")
                    enc = combat.start_encounter(session_id, name)
                    _combat_enroll_pcs(enc.id, session_id)
                    roster_changed = True
                    started_now = True
                continue
            if enc is None:
                continue  # every other verb needs a live encounter
            if action == "add":
                if not args or not args[0]:
                    continue
                name, count, hp, ac = args[0], 1, None, None
                for extra in args[1:]:
                    e = (extra or "").strip().lower()
                    if m := re.fullmatch(r"x\s*(\d+)", e):
                        count = max(1, min(20, int(m.group(1))))
                    elif m := re.fullmatch(r"hp\s*(\d+)", e):
                        hp = int(m.group(1))
                    elif m := re.fullmatch(r"ac\s*(\d+)", e):
                        ac = int(m.group(1))
                mon = None
                try:
                    mon = rules_lib.get_monster(name)
                except Exception:
                    pass
                if mon and hp is None:
                    combat.add_from_monster(enc.id, mon.index_slug, count=count)
                else:
                    for i in range(count):
                        label = name if count == 1 else f"{name} {i + 1}"
                        combat.add_combatant(enc.id, label, max_hp=hp or 10,
                                             armor_class=ac)
                roster_changed = True
            elif action in ("damage", "heal", "temp"):
                if len(args) < 2:
                    continue
                c = _combat_find(enc.id, args[0])
                try:
                    amount = int(re.sub(r"[^\d]", "", args[1] or "") or 0)
                except ValueError:
                    continue
                if not c or amount <= 0:
                    continue
                if action == "damage":
                    out = combat.apply_damage(c.id, amount)
                    if out.get("defeated"):
                        notes.append(f"⚔ {c.name} goes down.")
                elif action == "heal":
                    combat.heal(c.id, amount)
                else:
                    combat.set_temp_hp(c.id, amount)
                if c.character_id:
                    _combat_mirror_pc(c.id)
            elif action in ("condition", "uncondition"):
                if len(args) < 2 or not args[1]:
                    continue
                c = _combat_find(enc.id, args[0])
                if not c:
                    continue
                if action == "condition":
                    combat.add_condition(c.id, args[1].lower())
                else:
                    combat.remove_condition(c.id, args[1].lower())
            elif action == "cover":
                if len(args) < 2:
                    continue
                c = _combat_find(enc.id, args[0])
                level = _COVER_LEVELS.get((args[1] or "").strip().lower())
                if c and level:
                    combat.set_cover(c.id, level)
            elif action == "move":
                if not args:
                    continue
                c = _combat_find(enc.id, args[0])
                if c:
                    # Second field is the band; empty/'none' clears it.
                    band = (args[1] if len(args) > 1 else "").strip()
                    combat.set_position(
                        c.id, None if band.lower() in ("", "none") else band)
            elif action == "next":
                combat.next_turn(enc.id)
            elif action == "end":
                combat.end_encounter(enc.id)
                notes.append("⚔ The fight is over.")
                enc = None
        except Exception as e:
            print(f"[combat] hook {action!r} failed: {e}")
    if enc is not None and roster_changed:
        # roll_initiative only fills unset rolls, so mid-fight reinforcements
        # get theirs without rerolling everyone else's — and without resetting
        # the round unless the fight just began.
        order = combat.roll_initiative(enc.id, reset_turn=started_now)
        if started_now:
            line = ", ".join(f"{c.name} {c.initiative}" for c in order)
            notes.append(f"⚔ Initiative — {line}")
    return notes


# ======================================================================
# Deterministic combat turns: the LLM proposes INTENTS, combat/engine.py
# validates + resolves them with real dice, and the narration call renders
# the certified log. Illegal requests bounce back to the player with the
# reason; a PC's turn stays open across messages until they declare it done
# (or the engine proves their economy exhausted).
# ======================================================================

_MARTIAL_EXTRA_ATTACK = {"fighter", "barbarian", "paladin", "ranger", "monk",
                         "blood hunter", "gunslinger", "illrigger"}
_CASTING_ABILITY = {"bard": "cha", "sorcerer": "cha", "warlock": "cha",
                    "paladin": "cha", "cleric": "wis", "druid": "wis",
                    "ranger": "wis", "wizard": "int", "artificer": "int",
                    "illrigger": "cha"}


def _combat_pc_profile(char: Character) -> PCProfile:
    """Build the engine's acting numbers for a PC from the character sheet."""
    mods = {short: ability_modifier(_ability_score(char, full))
            for short, full in (("str", "strength"), ("dex", "dexterity"),
                                ("con", "constitution"), ("int", "intelligence"),
                                ("wis", "wisdom"), ("cha", "charisma"))}
    pb = proficiency_bonus_for_level(char.level)
    skills = {t.split(":", 1)[1].strip().lower() for t in (char.tags or [])
              if isinstance(t, str) and t.lower().startswith("skill:")}
    weapons: list[PCWeapon] = []
    for it in _inventory_items(char):
        name = it.get("name") or ""
        try:
            row = rules_lib.get_item(name)
        except Exception:
            row = None
        if row is None or not getattr(row, "damage_dice", None):
            continue
        # SRD melee weapons still carry range.normal = 5, so "has a range" is
        # not "is ranged" — trust the type label, else a range beyond reach.
        itype = (getattr(row, "item_type", None) or "").lower()
        ranged = "ranged" in itype or (getattr(row, "range_normal", None) or 0) > 10
        props = " ".join(str(p) for p in (row.properties or [])).lower()
        finesse = "finesse" in props
        mod = mods["dex"] if (ranged or (finesse and mods["dex"] > mods["str"])) \
            else mods["str"]
        dmg = row.damage_dice + (f"{mod:+d}" if mod else "")
        weapons.append(PCWeapon(name=row.name, attack_bonus=pb + mod,
                                damage=dmg, ranged=ranged, finesse=finesse))
    cls = (char.char_class or "").strip().lower()
    lvl = char.level
    if cls == "fighter":
        attacks = 4 if lvl >= 20 else 3 if lvl >= 11 else 2 if lvl >= 5 else 1
    elif cls in _MARTIAL_EXTRA_ATTACK and lvl >= 5:
        attacks = 2
    else:
        attacks = 1
    features: set[str] = set()
    if cls == "fighter":
        features.add("second wind")
        if lvl >= 2:
            features.add("action surge")
    if cls == "rogue":
        features.add("sneak attack")
        if lvl >= 2:
            features.add("cunning action")
        if lvl >= 5:
            features.add("uncanny dodge")
    if cls == "barbarian":
        features.add("rage")
    if cls == "paladin" and lvl >= 2:
        features.add("divine smite")
    if cls == "monk":
        features.add("bonus attack")
    cast_key = _CASTING_ABILITY.get(cls)
    spell_atk = spell_dc = None
    if cast_key and (char.spells or cls in ("cleric", "druid", "wizard",
                                            "sorcerer", "bard", "warlock")):
        spell_atk = pb + mods[cast_key]
        spell_dc = 8 + pb + mods[cast_key]
    # Remaining spell slots (total minus expended since the last rest).
    slots = {row["level"]: max(0, row["total"] - row["used"])
             for row in _spell_slots_for(cls, lvl, char.spell_slots_used)}
    # Reaction spells the engine may auto-cast when they change an outcome.
    known = {(x if isinstance(x, str) else (x.get("name") or "")).strip().lower()
             for x in (char.spells or [])}
    reaction_spells = {"shield"} & known if cast_key else set()
    return PCProfile(
        character_id=char.id, name=char.name, level=lvl, ability_mods=mods,
        prof=pb, skills=skills, weapons=weapons, attacks_per_action=attacks,
        features=features, spell_attack_bonus=spell_atk, spell_dc=spell_dc,
        spell_mod=cast_key, slots=slots, reaction_spells=reaction_spells)


# ---- deterministic pre-parser: the common phrasings never need an LLM ----
# Conservative by design: a pattern only fires when the WHOLE message is one
# simple declaration; anything compound or fuzzy goes to the intent model.

_PRE_END = re.compile(
    r"^\W*((i\s+)?(end|finish)\s+(my\s+)?turn|(i'?m\s+)?done( for now)?|"
    r"that'?s\s+(my|the)\s+turn|i\s+(wait|hold|brace)(\s.*)?)\W*$", re.IGNORECASE)
_PRE_SIMPLE = {
    "dodge": re.compile(r"^\W*(i\s+)?(take\s+the\s+)?dodge(\s+action)?\W*$", re.IGNORECASE),
    "dash": re.compile(r"^\W*(i\s+)?dash\W*$", re.IGNORECASE),
    "disengage": re.compile(r"^\W*(i\s+)?disengage\W*$", re.IGNORECASE),
    "hide": re.compile(r"^\W*(i\s+)?(hide|take\s+the\s+hide\s+action)\W*$", re.IGNORECASE),
}
_PRE_ATTACK = re.compile(
    r"^\W*(i\s+)?(attack|strike|stab|swing\s+at|slash(\s+at)?|shoot(\s+at)?|hit|"
    r"loose\s+an?\s+\w+\s+at|fire\s+at)\s+(the\s+)?(?P<target>[\w\s'\-]+?)"
    r"(\s+with\s+(my\s+)?(?P<weapon>[\w\s'\-]+?))?\W*$", re.IGNORECASE)
_PRE_CHARGE = re.compile(
    r"^\W*(i\s+)?(charge|close\s+with|move\s+(in)?to\s+melee\s+with|engage)\s+"
    r"(the\s+)?(?P<target>[\w\s'\-]+?)\W*$", re.IGNORECASE)
_PRE_RETREAT = re.compile(
    r"^\W*(i\s+)?(retreat|fall\s+back|back\s+away|back\s+off)\W*$", re.IGNORECASE)

_COMBAT_INTENT_STATS = {"preparsed": 0, "llm": 0, "none": 0, "parse_fail": 0}

# Answers to a frozen reaction question ("Shield or take the hit?" /
# "take the opportunity attack?").
_PRE_REACT_USE = re.compile(
    r"^\W*((i\s+)?(cast|use)\s+)?(shield|uncanny(\s+dodge)?)\W*$"
    r"|^\W*(i\s+)?(take\s+the\s+)?(swing|opportunity(\s+attack)?)\W*$"
    r"|^\W*(i\s+)?(swing|attack|hit)(\s+(him|her|it|them))?\W*$"
    r"|^\W*(yes|yeah|do it|use it|use my reaction)\W*$", re.IGNORECASE)
_PRE_REACT_NO = re.compile(
    r"^\W*(no|nah|nope|decline|pass|take\s+(it|the\s+hit)|"
    r"hold(\s+(it|back|the\s+reaction))?|let\s+(him|her|it|them)\s+go|"
    r"save\s+(it|my\s+reaction))\W*$", re.IGNORECASE)


def _preparse_reaction_answer(message: str) -> Optional[bool]:
    msg = (message or "").strip()
    if _PRE_REACT_USE.match(msg):
        return True
    if _PRE_REACT_NO.match(msg):
        return False
    return None


def _combat_preparse(message: str, my_name: str,
                     encounter_id: int) -> Optional[list[dict]]:
    """Translate an unambiguous single-act message into intents without an LLM
    call. Returns None whenever there is any doubt — the model handles those."""
    msg = (message or "").strip()
    if _PRE_END.match(msg):
        return [{"verb": "end_turn", "actor": my_name}]
    for verb, pat in _PRE_SIMPLE.items():
        if pat.match(msg):
            return [{"verb": verb, "actor": my_name}]
    m = _PRE_ATTACK.match(msg)
    if m:
        target = _combat_find(encounter_id, m.group("target"))
        if target is not None and not target.defeated:
            intent = {"verb": "attack", "actor": my_name, "target": target.name}
            if m.group("weapon"):
                intent["arg"] = m.group("weapon").strip()
            return [intent]
        return None  # named someone we can't resolve — let the model try
    m = _PRE_CHARGE.match(msg)
    if m:
        target = _combat_find(encounter_id, m.group("target"))
        if target is not None and not target.defeated:
            return [{"verb": "move", "actor": my_name,
                     "arg": f"melee with {target.name}"}]
        return None
    if _PRE_RETREAT.match(msg):
        me = _combat_find(encounter_id, my_name)
        band = "near" if me is not None and \
            (me.position or "").lower().startswith("melee") else "far"
        return [{"verb": "move", "actor": my_name, "arg": band}]
    return None


_COMBAT_INTENT_SYSTEM = (
    "You translate ONE player's combat declaration into structured intents for a "
    "rules engine. Output ONLY intent lines, one act per line, in the order "
    "declared — no prose, no commentary:\n"
    "[[INTENT: attack | <target> | <weapon> | <rider: 'smite' or 'smite 2' when a "
    "paladin declares Divine Smite>]]\n"
    "[[INTENT: move | melee with <name>]]  or  [[INTENT: move | near]] / [[INTENT: move | far]]\n"
    "[[INTENT: dash]]  [[INTENT: disengage]]  [[INTENT: dodge]]  [[INTENT: hide]]\n"
    "[[INTENT: help | <ally>]]  [[INTENT: grapple | <target>]]  "
    "[[INTENT: shove | <target> | prone or push]]\n"
    "[[INTENT: use | <item>]]\n"
    "[[INTENT: cast | <spell> | <targets — one name, 'A, B, C', or 'all enemies'> | "
    "<slot level, only when upcast>]]\n"
    "[[INTENT: feature | action surge]]  (also: second wind, rage)\n"
    "[[INTENT: improvise | <what they attempt> | <STR/DEX/CON/INT/WIS/CHA vs DC n, if a check fits>]]\n"
    "[[INTENT: end_turn]]\n"
    "Rules:\n"
    "- Propose exactly what the player declared — nothing more. The engine refuses "
    "illegal acts itself; do not pre-judge legality.\n"
    "- If the player clearly finishes ('that's my turn', 'I'm done', 'I wait', "
    "'I brace'), append [[INTENT: end_turn]].\n"
    "- If the message is NOT a combat act at all (a question, table talk, looking "
    "around), output exactly [[INTENT: none]].\n"
    "\n"
    "Examples:\n"
    "Player says: I rush the bandit leader and hit him with everything, then duck "
    "behind the cart\n"
    "Intents:\n"
    "[[INTENT: move | melee with Bandit Leader]]\n"
    "[[INTENT: attack | Bandit Leader]]\n"
    "[[INTENT: move | near]]\n"
    "\n"
    "Player says: I drink my healing potion and back off — that's my turn\n"
    "Intents:\n"
    "[[INTENT: use | Potion of Healing]]\n"
    "[[INTENT: move | near]]\n"
    "[[INTENT: end_turn]]\n"
    "\n"
    "Player says: I cast hold person on the cultist and use action surge to "
    "attack him too\n"
    "Intents:\n"
    "[[INTENT: cast | Hold Person | Cultist]]\n"
    "[[INTENT: feature | action surge]]\n"
    "[[INTENT: attack | Cultist]]\n"
    "\n"
    "Player says: wait, how many goblins are left?\n"
    "Intents:\n"
    "[[INTENT: none]]\n"
)

INTENT_PATTERN = re.compile(r"\[\[INTENT:(.+?)\]\]", re.IGNORECASE)

# verb -> mapping of positional hook fields onto engine intent keys
_INTENT_FIELDS: Dict[str, tuple] = {
    "attack": ("target", "arg", "rider"), "move": ("arg",), "dash": (),
    "disengage": (), "dodge": (), "hide": (), "help": ("target",),
    "grapple": ("target",), "shove": ("target", "arg"), "use": ("arg",),
    "cast": ("arg", "target", "slot"), "feature": ("arg",),
    "improvise": ("arg", "target"), "react": ("arg",), "end_turn": (),
}


def _parse_intents(text: str, actor_name: str) -> Optional[list[dict]]:
    """Parse [[INTENT: ...]] lines. Returns None when the message wasn't a
    combat act (explicit 'none' or nothing parseable)."""
    intents: list[dict] = []
    for m in INTENT_PATTERN.finditer(text or ""):
        parts = _split_hook(m.group(1))
        verb = (parts[0] or "").strip().lower().replace(" ", "_")
        if verb == "none":
            return None
        fields = _INTENT_FIELDS.get(verb)
        if fields is None:
            continue
        intent: dict = {"verb": verb, "actor": actor_name}
        for key, val in zip(fields, parts[1:]):
            if val:
                intent[key] = val.strip()
        if verb == "improvise" and intent.get("target"):
            # check spec rides in arg for the engine ("... STR vs 15")
            intent["arg"] = f"{intent.get('arg', '')} {intent.pop('target')}".strip()
        intents.append(intent)
    return intents or None


# The extractor stashes its most recent raw output here so the turn logger can
# capture exactly what the model produced (per-request; read immediately after
# the call in the same thread).
_LAST_EXTRACT_RAW: _contextvars.ContextVar = _contextvars.ContextVar(
    "last_extract_raw", default=None)


def _combat_extract_intents(my_name: str, remaining: dict, board: str,
                            profile: Optional[PCProfile],
                            message: str) -> Optional[list[dict]]:
    gear = ""
    if profile:
        w = ", ".join(f"{x.name} ({'ranged' if x.ranged else 'melee'})"
                      for x in profile.weapons) or "unarmed"
        feats = ", ".join(sorted(profile.features)) or "none"
        gear = f"Weapons: {w}\nFeatures: {feats}\n"
    rem = (f"Remaining this turn — action: {'yes' if remaining.get('action') else 'no'}, "
           f"bonus: {'yes' if remaining.get('bonus') else 'no'}, "
           f"move steps: {remaining.get('move_steps', 0)}\n") if remaining else ""
    user = (f"{board}\n\nActing player character: {my_name}\n{rem}{gear}"
            f"\nPlayer says: {message}\n\nIntents:")
    _LAST_EXTRACT_RAW.set(None)
    try:
        out = _call_extractor_llm([
            {"role": "system", "content": _COMBAT_INTENT_SYSTEM},
            {"role": "user", "content": user}])
    except Exception as e:
        print(f"[combat-engine] intent extraction failed: {e}")
        _LAST_EXTRACT_RAW.set(f"<extractor error: {e}>")
        return None
    _LAST_EXTRACT_RAW.set(out)
    parsed = _parse_intents(out, my_name)
    if parsed is None and "[[INTENT" in (out or "") \
            and "none" not in (out or "").lower():
        _COMBAT_INTENT_STATS["parse_fail"] += 1
    return parsed


def _combat_log(*, session_id: Optional[str], encounter_id: Optional[int],
                round: Optional[int], character: Optional[str],
                user_id: Optional[str], message: Optional[str], kind: str,
                parse_source: Optional[str], raw_llm: Optional[str] = None,
                intents: Optional[list] = None, events: Optional[list] = None,
                report: Optional[str] = None,
                flags: Optional[list] = None) -> None:
    """Append one telemetry row. Best-effort — never raises into the turn."""
    try:
        with Session(engine) as s:
            s.add(CombatLog(
                session_id=session_id, encounter_id=encounter_id, round=round,
                character=character, user_id=user_id,
                player_message=(message or "")[:2000], kind=kind,
                parse_source=parse_source,
                raw_llm=(raw_llm[:4000] if raw_llm else None),
                intents=intents, events=events,
                report=(report[:8000] if report else None),
                flags=flags or []))
            s.commit()
    except Exception as e:
        print(f"[combat-log] write failed: {e}")


def _combat_fight_over(encounter_id: int) -> Optional[str]:
    """If one side is wiped, close the encounter and say so."""
    order = combat.order(encounter_id)
    foes = [c for c in order if c.kind != "pc"]
    pcs = [c for c in order if c.kind == "pc"]
    if foes and all(c.defeated for c in foes):
        combat.end_encounter(encounter_id)
        return "ALL FOES DOWN — the fight is over (encounter closed)."
    if pcs and all(c.defeated for c in pcs):
        combat.end_encounter(encounter_id)
        return ("THE PARTY IS DOWN — the fight is over (encounter closed); "
                "resolve the aftermath in narration.")
    return None


def _combat_engine_turn(session_id: str, user_id: Optional[str],
                        message: str) -> Optional[str]:
    """Run one deterministic combat exchange for this player message.

    Returns the certified-resolution block for the narration prompt, or None
    when the message wasn't a combat act (caller falls back to normal flow)."""
    enc = combat.get_active(session_id)
    if enc is None:
        return None
    state = _load_session_state(session_id)
    member = _acting_member(state.get("meta", {}) or {}, user_id)
    char_id = (member or {}).get("character_id") or \
        _resolve_session_character(session_id, user_id)
    if not char_id:
        return None
    _combat_enroll_pcs(enc.id, session_id)  # seat late joiners
    my = next((c for c in combat.order(enc.id) if c.character_id == char_id), None)
    if my is None:
        # Session meta didn't list this player (legacy session shapes) — seat
        # their PC directly and roll it into the running order.
        with Session(engine) as s:
            ch = s.get(Character, char_id)
            if not ch or ch.current_hp <= 0:
                return None
            _combat_seat_pc(enc.id, ch)
        combat.roll_initiative(enc.id, reset_turn=False)
        my = next((c for c in combat.order(enc.id)
                   if c.character_id == char_id), None)
        if my is None:
            return None

    profiles: dict[int, PCProfile] = {}
    with Session(engine) as s:
        for c in combat.order(enc.id):
            if c.character_id:
                ch = s.get(Character, c.character_id)
                if ch:
                    try:
                        profiles[c.character_id] = _combat_pc_profile(ch)
                    except Exception as e:
                        print(f"[combat-engine] profile for {ch.name} failed: {e}")

    blocks: list[str] = []
    rolls_out: list[dict] = []
    all_events: list[dict] = []

    def take(rep) -> None:
        txt = CombatEngine.render_report(rep)
        if txt.strip():
            blocks.append(txt)
        rolls_out.extend(rep.rolls())
        all_events.extend(rep.events)

    def run_monsters(limit: int = 12) -> None:
        for _ in range(limit):
            if combat.get_active(session_id) is None:
                return
            c = combat.current_combatant(enc.id)
            if c is None or c.kind == "pc":
                return
            rep = combat_engine.run_monster_turn(enc.id, profiles=profiles)
            take(rep)
            if rep.paused:
                return  # frozen on a player's reaction decision
            over = _combat_fight_over(enc.id)
            if over:
                blocks.append(over)
                return

    _round = enc.round
    parse_source: Optional[str] = None
    raw_llm: Optional[str] = None
    kind = "turn"

    # ---- a frozen reaction question owns the table until answered ----
    pending = combat.get_pending_reaction(enc.id)
    intents: Optional[list[dict]] = None
    if pending:
        kind = "reaction"
        owner = pending.get("target_char_id")
        if owner and owner != char_id:
            _combat_log(session_id=session_id, encounter_id=enc.id, round=_round,
                        character=my.name, user_id=str(user_id), message=message,
                        kind="waiting", parse_source="waiting",
                        flags=["paused"])
            return ("# Combat resolution (certified — already applied to the tracker)\n"
                    f"WAITING: the fight is frozen on {pending.get('target')}'s "
                    f"reaction — only they can answer.\n"
                    f"({pending.get('question')})")
        decision = _preparse_reaction_answer(message)
        parse_source = "reaction-preparse" if decision is not None else "reaction-llm"
        if decision is None:
            got = _combat_extract_intents(
                my.name, {},
                combat.render(enc.id)
                + f"\nPENDING REACTION for {my.name}: {pending.get('question')} "
                  "If the player answers it, output [[INTENT: react | use]] or "
                  "[[INTENT: react | decline]] FIRST.",
                profiles.get(char_id), message)
            raw_llm = _LAST_EXTRACT_RAW.get()
            if got:
                lead = next((i for i in got if i.get("verb") == "react"), None)
                if lead is not None:
                    a = (lead.get("arg") or "").lower()
                    decision = not any(w in a for w in
                                       ("decline", "no", "pass", "hold", "save"))
                    intents = [i for i in got if i is not lead] or None
                else:
                    intents = got
        if decision is None:
            decision = False
            blocks.append(f"NOTE: no clear answer — {my.name} lets the "
                          "reaction pass (declined).")
        rrep = combat_engine.resume_reaction(enc.id, use=bool(decision),
                                             profiles=profiles)
        take(rrep)
        if not rrep.paused:
            over = _combat_fight_over(enc.id)
            if over:
                blocks.append(over)
    else:
        cur = combat.current_combatant(enc.id)
        remaining = {}
        if cur is not None and cur.id == my.id:
            fresh = combat.get_combatant(my.id)
            remaining = {"action": not fresh.action_used,
                         "bonus": not fresh.bonus_used,
                         "move_steps": max(0, fresh.move_left)}
        # Cheap deterministic path first: simple declarations skip the model.
        intents = _combat_preparse(message, my.name, enc.id)
        if intents is not None:
            _COMBAT_INTENT_STATS["preparsed"] += 1
            parse_source = "preparse"
        else:
            intents = _combat_extract_intents(
                my.name, remaining, combat.render(enc.id),
                profiles.get(char_id), message)
            raw_llm = _LAST_EXTRACT_RAW.get()
            _COMBAT_INTENT_STATS["llm" if intents else "none"] += 1
            parse_source = "llm" if intents else "none"
        print(f"[combat-intents] {_COMBAT_INTENT_STATS} :: "
              f"{[i['verb'] for i in intents] if intents else 'no-act'}")
        if intents is None:
            # A miss the extractor couldn't turn into acts — capture the raw
            # output; this row IS the data you tune the intent prompt against.
            _fail = bool(raw_llm and "[[INTENT" in raw_llm
                         and "none" not in raw_llm.lower())
            _combat_log(session_id=session_id, encounter_id=enc.id, round=_round,
                        character=my.name, user_id=str(user_id), message=message,
                        kind="parse_miss", parse_source=parse_source,
                        raw_llm=raw_llm,
                        flags=(["parse_fail"] if _fail else ["none"]))
            return None  # not a combat act — normal narration flow handles it

    def halted() -> bool:
        return combat.get_pending_reaction(enc.id) is not None

    # Monsters with earlier initiative act first (fight may have just begun).
    if not halted():
        run_monsters()

    if not halted() and combat.get_active(session_id) is not None and intents:
        cur = combat.current_combatant(enc.id)
        if cur is not None and cur.id == my.id:
            rep = combat_engine.resolve(enc.id, intents, profiles)
            take(rep)
            over = _combat_fight_over(enc.id)
            if over:
                blocks.append(over)
            elif rep.turn_over and not rep.paused:
                run_monsters()
        elif cur is not None and cur.kind == "pc":
            blocks.append(f"REFUSED: It is {cur.name}'s turn — "
                          f"{my.name} must wait for it to come around.")

    if not halted() and combat.get_active(session_id) is not None:
        nxt = combat.current_combatant(enc.id)
        if nxt is not None:
            blocks.append(f"NOW: {nxt.name}'s turn.")

    # Persist spell-slot spends onto their owners' sheets. An event's
    # slot_char_id (e.g. a Shield reaction) wins; otherwise the acting PC.
    spends: dict[int, list[int]] = {}
    for e in all_events:
        if e.get("slot_spent"):
            spends.setdefault(e.get("slot_char_id") or char_id, []).append(
                e["slot_spent"])
    if spends:
        try:
            with Session(engine) as s:
                for cid, levels in spends.items():
                    ch = s.get(Character, cid)
                    if not ch:
                        continue
                    used = {int(k): int(v) for k, v in
                            (ch.spell_slots_used or {}).items()}
                    for lv in levels:
                        used[lv] = used.get(lv, 0) + 1
                    ch.spell_slots_used = {str(k): v for k, v in used.items()}
                    s.add(ch)
                s.commit()
        except Exception as e:
            print(f"[combat-engine] slot persist failed: {e}")

    # Mirror every PC's tracker HP back onto the character sheet — in engine
    # mode the tracker is where damage/healing lands first.
    try:
        with Session(engine) as s:
            for c in combat.order(enc.id):
                if not c.character_id:
                    continue
                ch = s.get(Character, c.character_id)
                if ch and (ch.current_hp != c.current_hp
                           or (getattr(ch, "temp_hp", 0) or 0) != c.temp_hp):
                    ch.current_hp = c.current_hp
                    ch.temp_hp = c.temp_hp
                    s.add(ch)
            s.commit()
    except Exception as e:
        print(f"[combat-engine] PC HP mirror failed: {e}")

    collector = _ACTIVITY_ROLLS.get()
    if collector is not None:
        collector.extend(rolls_out)

    report = "\n".join(blocks)
    # Telemetry: one durable, replayable row for this exchange.
    _flags: list[str] = []
    if combat.get_pending_reaction(enc.id):
        _flags.append("paused")
    if any(e.get("kind") == "reaction_prompt" for e in all_events):
        _flags.append("reaction_prompt")
    if "REFUSED:" in report:
        _flags.append("rejected")
    if "the fight is over" in report or "DOWN" in report:
        _flags.append("fight_over")
    _combat_log(session_id=session_id, encounter_id=enc.id,
                round=combat.get_encounter(enc.id).round if combat.get_encounter(enc.id) else _round,
                character=my.name, user_id=str(user_id), message=message,
                kind=kind, parse_source=parse_source, raw_llm=raw_llm,
                intents=intents, events=all_events, report=report, flags=_flags)
    return ("# Combat resolution (certified — already applied to the tracker)\n"
            + report)


_COMBAT_NARRATE_CONTRACT = (
    "# Narration contract (deterministic combat engine)\n"
    "A rules engine has ALREADY resolved this exchange — the 'Combat resolution' "
    "block above is certified fact, applied to the tracker. Your narration must:\n"
    "- Describe exactly those results, vividly and in order. You may state the "
    "numbers (damage, HP) but never change them. Do not invent attacks, damage, "
    "deaths, conditions, or movement the log doesn't show.\n"
    "- REFUSED lines: that act did NOT happen. In the DM's voice, tell the player "
    "plainly why and ask what they do instead.\n"
    "- 'TURN STILL OPEN — remaining: ...': close by telling the player what they "
    "still have and ask for the rest of their turn (or a declaration they're done).\n"
    "- 'NOW: <name>'s turn': end by setting up that creature's moment.\n"
    "- 'REACTION?' + 'FIGHT PAUSED': the fight is frozen mid-attack awaiting that\n"
    "  player's reaction decision. Put the question to them vividly, list the\n"
    "  options, and STOP — do not narrate past the frozen moment or decide for them.\n"
    "- Do not emit [[ROLL: ...]] hooks or COMBAT damage/heal/temp/move hooks — the "
    "dice are already rolled. [[COMBAT: add | ...]] (reinforcements) and "
    "[[COMBAT: end]] (the fight resolves narratively) remain available, and "
    "story-driven lasting conditions still use [[CONDITION]] hooks.\n"
)


# ----- OpenRouter LLM call -----

def _retry_after_seconds(resp: "requests.Response") -> Optional[float]:
    """Extract a retry delay (seconds) from a rate-limited OpenRouter response.

    Prefers the standard ``Retry-After`` header, then falls back to the
    provider-specific ``retry_after_seconds`` field in the JSON error body.
    Returns None if no hint is present.
    """
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except (TypeError, ValueError):
            pass
    try:
        meta = resp.json().get("error", {}).get("metadata", {})
        val = meta.get("retry_after_seconds") or meta.get("retry_after_seconds_raw")
        if val is not None:
            return float(val)
    except (ValueError, AttributeError, TypeError):
        pass
    return None


# CJK/fullwidth ranges. Local Qwen models occasionally "drift" mid-reply into
# Chinese (usually restating what they already said in English). One prompt
# line isn't a reliable fix, so replies are also sanitized after the fact.
_CJK_RE = re.compile(
    "[ᄀ-ᇿ　-ヿ㐀-䶿一-鿿"
    "가-힯豈-﫿＀-￯]"
)


def _strip_cjk_drift(text: str) -> str:
    """Cut a reply short at the point where it drifts into CJK text.

    Truncates at the last sentence boundary before the first CJK character so
    the player sees a clean English reply. If the drift starts too early to
    leave a usable prefix, the text is returned unchanged (better odd than
    empty) — in practice Qwen drifts late, after the English content.
    """
    m = _CJK_RE.search(text)
    if not m:
        return text
    prefix = text[: m.start()]
    # Prefer a clean sentence end; fall back to the last newline.
    cut = max(prefix.rfind(". "), prefix.rfind("! "), prefix.rfind("? "),
              prefix.rfind(".\n"), prefix.rfind("!\n"), prefix.rfind("?\n"),
              prefix.rfind("\n"))
    salvaged = prefix[: cut + 1].rstrip() if cut >= 40 else prefix.strip()
    if len(salvaged) < 40:
        print("[LLM] CJK drift detected too early to trim cleanly; leaving reply as-is")
        return text
    print(f"[LLM] trimmed CJK drift: kept {len(salvaged)}/{len(text)} chars")
    return salvaged


def call_openrouter_chat(
    messages: List[Dict[str, str]],
    *,
    max_tokens: Optional[int] = None,
    timeout_seconds: int = 60,
    model_override: Optional[str] = None,
    temperature: Optional[float] = None,
) -> str:
    """Call the configured LLM chat endpoint (OpenRouter or a local server).

    When ``LLM_NUM_CTX`` is set and ``LLM_BASE_URL`` looks like Ollama's
    OpenAI-compatible shim (path ends in ``/v1/chat/completions``), each attempt
    first tries Ollama's NATIVE ``/api/chat`` endpoint instead — the shim
    silently ignores context-size options, so a big system prompt gets
    truncated to Ollama's small default context window (typically 4096) with no
    error, while the native endpoint honors ``options.num_ctx``. Any failure
    talking to the native endpoint (connection error, non-2xx, bad response
    shape) is logged and falls through to the OpenAI-compatible path for the
    rest of the call, so OpenRouter users (and older Ollama builds without
    ``/api/chat``) are completely unaffected.

    ``model_override``, when given, replaces the normal [LLM_MODEL,
    *LLM_FALLBACK_MODELS] list with a single fixed model (used by the world-
    extraction side-call, which targets EXTRACTOR_MODEL specifically).
    """
    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
        # Optional metadata (ignored by local servers):
        "HTTP-Referer": "http://localhost",
        "X-Title": "Oracle DM",
    }

    # Try the primary model, then any configured fallbacks. Each model gets a
    # few attempts so transient upstream 429s (common on :free models) don't
    # immediately surface as a failed DM turn.
    models = [model_override] if model_override else [LLM_MODEL, *LLM_FALLBACK_MODELS]
    max_attempts_per_model = 3
    max_backoff_seconds = 20
    last_error: Optional[str] = None

    # Only meaningful for Ollama's OpenAI-compat shim; disabled for OpenRouter
    # and any other endpoint. Once it fails once, we stop retrying it for the
    # rest of this call and rely purely on the OpenAI-compat path below.
    try_native = bool(LLM_NUM_CTX) and LLM_BASE_URL.rstrip("/").endswith("/v1/chat/completions")
    native_url = LLM_BASE_URL.replace("/v1/chat/completions", "/api/chat") if try_native else None

    for model in models:
        payload: Dict = {
            "model": model,
            "messages": messages,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if temperature is not None:
            payload["temperature"] = temperature

        for attempt in range(1, max_attempts_per_model + 1):
            if try_native:
                native_options: Dict[str, Any] = {"num_ctx": LLM_NUM_CTX}
                if temperature is not None:
                    native_options["temperature"] = temperature
                if max_tokens is not None:
                    native_options["num_predict"] = max_tokens
                native_payload = {
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": native_options,
                }
                native_resp = None
                try:
                    native_resp = requests.post(
                        native_url, json=native_payload, timeout=timeout_seconds,
                    )
                except requests.RequestException as e:
                    last_error = f"native request error: {e}"
                    print(
                        f"[LLM native error] {model} attempt {attempt}: {last_error} "
                        "— falling back to OpenAI-compat endpoint"
                    )
                    try_native = False

                if native_resp is not None:
                    if native_resp.status_code == 200:
                        try:
                            return _strip_cjk_drift(
                                native_resp.json()["message"]["content"])
                        except Exception as e:
                            last_error = f"native parse error: {e}"
                            print(
                                f"[LLM native parse error] {model} attempt {attempt}: {last_error} "
                                "— falling back to OpenAI-compat endpoint"
                            )
                            try_native = False
                    else:
                        last_error = f"native HTTP {native_resp.status_code}: {native_resp.text}"
                        print(f"[LLM native error] {model} attempt {attempt}: {last_error}")
                        if native_resp.status_code == 429 or native_resp.status_code >= 500:
                            if attempt < max_attempts_per_model:
                                time.sleep(min(2 ** attempt, max_backoff_seconds))
                                continue
                        # Non-retryable (or exhausted): drop native for the rest
                        # of this call and fall through to the compat path below.
                        try_native = False

            try:
                resp = requests.post(
                    LLM_BASE_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout_seconds,
                )
            except requests.RequestException as e:
                last_error = f"request error: {e}"
                print(f"[LLM error] {model} attempt {attempt}: {last_error}")
                time.sleep(min(2 ** attempt, max_backoff_seconds))
                continue

            if resp.status_code == 200:
                data = resp.json()
                try:
                    return _strip_cjk_drift(data["choices"][0]["message"]["content"])
                except Exception as e:
                    print(f"[LLM parse error] {e} | data={data}")
                    raise RuntimeError("Failed to parse LLM response")

            last_error = f"HTTP {resp.status_code}: {resp.text}"
            print(f"[LLM error] {model} attempt {attempt}: {last_error}")

            # Retry on rate-limit / transient server errors; otherwise move on.
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < max_attempts_per_model:
                    delay = _retry_after_seconds(resp)
                    if delay is None:
                        delay = min(2 ** attempt, max_backoff_seconds)
                    time.sleep(min(delay, max_backoff_seconds))
                    continue
            # Non-retryable status (or attempts exhausted): try next model.
            break

    raise RuntimeError(f"LLM call failed after retries: {last_error}")


def call_openrouter_dm(messages: List[Dict[str, str]]) -> str:
    """Call OpenRouter for live DM narration."""
    return call_openrouter_chat(messages)


def _call_extractor_llm(messages: List[Dict[str, str]]) -> str:
    """LLM call for the world-extraction second pass (see _run_world_extraction).

    Targets EXTRACTOR_MODEL (falls back to LLM_MODEL when unset) at a low
    temperature and a small max_tokens budget — this call only ever emits a
    strict JSON delta, never prose, so it doesn't need the DM call's budget.
    """
    return call_openrouter_chat(
        messages,
        model_override=EXTRACTOR_MODEL,
        temperature=0.1,
        max_tokens=700,
    )


# DM-narration error fallback text (see chat_endpoint) — never worth extracting
# a world-state delta from an error message.
_DM_ERROR_FALLBACK_MARKER = "The Oracle strains to speak"


def _session_members(meta: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """The session's seated players: {user_id: {character_id, character_name,
    pc_slug}}. Solo sessions predate the members map — synthesize one entry
    from the legacy single-PC meta keys so every code path sees one shape."""
    members = dict(meta.get("members") or {})
    if not members and meta.get("user_id") and meta.get("character_id"):
        members[str(meta["user_id"])] = {
            "character_id": meta.get("character_id"),
            "character_name": meta.get("character_name"),
            "pc_slug": meta.get("pc_slug"),
        }
    return members


def _acting_member(meta: Dict[str, Any], user_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Resolve the ACTING player's seat at the table (falls back to the
    session owner for legacy/solo sessions)."""
    members = _session_members(meta)
    if user_id and str(user_id) in members:
        return members[str(user_id)]
    if len(members) == 1:
        return next(iter(members.values()))
    if meta.get("character_id"):
        return {"character_id": meta.get("character_id"),
                "character_name": meta.get("character_name"),
                "pc_slug": meta.get("pc_slug")}
    return None


def _pc_busy_until(pc_slug: Optional[str]) -> Optional[int]:
    """Days remaining on a PC's downtime commitment, or None when free."""
    if not pc_slug:
        return None
    try:
        e = world.get_entity(pc_slug)
        if e is None:
            return None
        busy = (e.attributes or {}).get("busy_until_day")
        if busy is None:
            return None
        today = world.current_day()
        return int(busy) - today if int(busy) > today else None
    except Exception:
        return None


def _reconcile_bubble_time(session_id: str, days: int) -> None:
    """Fold a turn's fictional days into this session's time bubble.

    Bubbles run in PARALLEL world time: each session tracks its own start day
    and accumulated days, and the world clock ratchets to the bubble's end via
    max() — a hundred concurrent sessions advance the clock no faster than the
    single longest one. Skips beyond BUBBLE_SKIP_CAP_DAYS become a personal
    downtime commitment on the PC (busy_until_day) instead of dragging the
    shared clock.
    """
    if days <= 0:
        return
    try:
        state = _load_session_state(session_id)
        meta = dict(state.get("meta", {}))
        start = meta.get("bubble_start_day")
        if start is None:
            start = world.current_day()
        skip = min(days, BUBBLE_SKIP_CAP_DAYS)
        # Lead cap: the bubble may not push the canonical clock more than
        # WORLD_LEAD_CAP_DAYS ahead of the wall floor. Days the world won't
        # absorb become personal commitments, same as skip-cap overflow.
        prev_bubble = int(meta.get("bubble_days", 0))
        floor = world.wall_floor(days_per_real_day=WORLD_DAYS_PER_REAL_DAY)
        if floor is not None:
            headroom = max(0, (floor + WORLD_LEAD_CAP_DAYS)
                           - (int(start) + prev_bubble))
            skip = min(skip, headroom)
        extra = days - skip
        bubble_days = prev_bubble + skip
        meta["bubble_start_day"] = int(start)
        meta["bubble_days"] = bubble_days
        _set_session_meta(session_id, meta)

        new_day = world.ratchet_day(int(start) + bubble_days)
        print(f"[clock] {session_id}: bubble +{skip}d "
              f"(start day {start}, total {bubble_days}) -> world day {new_day}")

        if extra > 0:
            # The commitment binds every PC seated at the table alike.
            for m in _session_members(meta).values():
                if not m.get("pc_slug"):
                    continue
                pc_e = world.get_entity(m["pc_slug"])
                if pc_e is None:
                    continue
                busy_until = new_day + extra
                world.upsert_entity(pc_e.name, pc_e.type, slug=pc_e.slug,
                                    status=pc_e.status,
                                    attributes={"busy_until_day": busy_until})
                print(f"[clock] {pc_e.name}: long skip — committed to downtime "
                      f"until world day {busy_until} (world unaffected)")

        ent = world_entropy.run_if_due(world)
        if any(ent.values()):
            print(f"[entropy] {ent}")
    except Exception as e:
        print(f"[clock] bubble reconcile failed for {session_id}: {e}")


def _build_memorial(char: Optional["Character"], pc_slug: str) -> Optional[dict]:
    """One-page life record for a fallen PC: LLM-written eulogy over the
    graph's canon, with a deterministic fallback. Never raises."""
    record = world_entropy.build_life_record(world, pc_slug)
    if record is None:
        return None
    name = record["name"]
    ident = ""
    if char is not None:
        bits = [b for b in (char.race, char.char_class) if b]
        ident = f"{' '.join(bits)}, level {char.level}" if bits else f"level {char.level}"

    stats = [f"Adventured {record['days_adventured']} days",
             f"fell on {record['date_str']}"]
    if record["places"]:
        stats.append(f"walked {len(record['places'])} places")
    footer = " • ".join(stats)

    fallback_lines = [f"**{name}**" + (f" — {ident}" if ident else ""), ""]
    if record["deeds"]:
        fallback_lines.append("Deeds: " + "; ".join(
            d.split(") ", 1)[-1] for d in record["deeds"][-5:]))
    if record["quests"]:
        fallback_lines.append("Quests: " + "; ".join(record["quests"]))
    if record["friends"] or record["companions"]:
        fallback_lines.append(
            "Remembered by: " + ", ".join(
                (record["companions"] + record["friends"])[:8]))
    fallback_lines += ["", f"*{footer}*"]
    text = "\n".join(fallback_lines)

    try:
        eulogy = call_openrouter_chat(
            [{"role": "system", "content": (
                "You are a chronicler writing a memorial for a fallen adventurer. "
                "Write AT MOST 220 words of flowing in-world prose (no headers, no "
                "lists): who they were, their greatest questing achievements drawn "
                "from the deeds given, the places and people that mattered, and how "
                "they will be remembered. Solemn but warm. English only.")},
             {"role": "user", "content": json.dumps(
                 {"name": name, "identity": ident, **{k: record[k] for k in
                  ("deeds", "quests", "places", "companions", "friends")}},
                 ensure_ascii=False)}],
            max_tokens=400, temperature=0.8, timeout_seconds=90,
        ).strip()
        if eulogy:
            text = f"**{name}**" + (f" — {ident}" if ident else "") + \
                   f"\n\n{eulogy}\n\n*{footer}*"
    except Exception as e:
        print(f"[memorial] eulogy LLM failed, using fallback: {e}")

    return {"title": f"⚰️ In Memoriam — {name}", "text": text[:3800],
            "character": name}


def _maybe_memorialize(session_id: str) -> Optional[dict]:
    """If this session's PC just died, canonize the death (permanent) and
    return the memorial payload exactly once."""
    state = _load_session_state(session_id)
    meta = state.get("meta", {}) or {}
    pc_slug = meta.get("pc_slug")
    if not pc_slug:
        return None
    pc = world.get_entity(pc_slug)
    if pc is None or (pc.attributes or {}).get("memorialized"):
        return None

    char = None
    char_id = meta.get("character_id")
    if char_id:
        with Session(engine) as s:
            char = s.get(Character, char_id)

    dead_by_saves = bool(char and char.death_save_failures >= 3
                         and char.current_hp <= 0 and not char.stable)
    dead_by_canon = pc.status == "dead"
    if not (dead_by_saves or dead_by_canon):
        return None

    # Permadeath: canonized in the world graph, memorial fires exactly once.
    world.upsert_entity(
        pc.name, pc.type, slug=pc.slug, status="dead",
        attributes={"memorialized": True, "died_day": world.current_day()},
    )
    world.add_event(f"{pc.name} fell, and their tale passed into memory.",
                    involved=[pc.slug])
    print(f"[memorial] {pc.name} has died — building life record")
    return _build_memorial(char, pc.slug)


def _run_world_extraction(
    session_id: str,
    player_action: str,
    dm_narration: str,
    world_context_text: str,
    context_entity_ids: Optional[List[int]] = None,
) -> None:
    """Background task: turn a completed DM turn into world-graph mutations.

    Called via BackgroundTasks after the DM's reply has already been sent to
    the player — Starlette runs sync background tasks in a threadpool, so the
    blocking extractor LLM call here doesn't hold up the response. Never
    raises: a bad extraction should never surface as a play-loop error.
    """
    if _DM_ERROR_FALLBACK_MARKER in (dm_narration or ""):
        print(f"[world-extract] {session_id}: skipped — DM narration was an error fallback")
        return
    try:
        _delta, summary = extract_and_apply(
            world,
            _call_extractor_llm,
            player_action=player_action,
            dm_narration=dm_narration,
            world_context=world_context_text or "",
            session_id=session_id,
            defer_clock=True,  # bubble time: this session owns its days
            context_entity_ids=set(context_entity_ids or []),
        )
        if summary.get("error"):
            print(f"[world-extract] {session_id}: failed — {summary['error']}")
        else:
            print(f"[world-extract] {session_id}: applied {summary}")
            _reconcile_bubble_time(session_id, int(summary.get("days") or 0))
            # Keep the DB bounded: archive far-off detail / compact old events
            # once the caps are exceeded. Cheap no-op while under them.
            try:
                caps = world.enforce_world_caps(
                    max_entities=int(os.getenv("WORLD_MAX_ENTITIES", "3000")),
                    max_events=int(os.getenv("WORLD_MAX_EVENTS", "4000")),
                )
                if caps.get("archived") or caps.get("events_compacted"):
                    print(f"[world-caps] {caps}")
            except Exception as e:
                print(f"[world-caps] sweep failed: {e}")
    except Exception as e:
        print(f"[world-extract] {session_id}: unexpected failure: {e}")


# ----- Single-GPU VRAM time-sharing helpers -----

# True when ComfyUI may be holding a model in VRAM (an image was rendered since
# the last free). Starts True so the first chat after a backend restart clears
# whatever a previous run may have left loaded.
_DIFFUSION_VRAM_DIRTY = True


def _mark_diffusion_dirty() -> None:
    """Record that diffusion models were (probably) loaded into VRAM."""
    global _DIFFUSION_VRAM_DIRTY
    _DIFFUSION_VRAM_DIRTY = True


def _free_diffusion_vram() -> None:
    """Unload the ComfyUI diffusion model so the LLM can reclaim VRAM.

    No-op unless VRAM_TIMESHARE is enabled, and skipped entirely unless an
    image render actually loaded diffusion models since the last free — the
    diffusion side only occupies VRAM when a job runs, so text-only sessions
    never need this. Best-effort and never raises.
    """
    global _DIFFUSION_VRAM_DIRTY
    if not VRAM_TIMESHARE or not _DIFFUSION_VRAM_DIRTY:
        return
    try:
        from imagery.comfy_client import client_from_config

        cfg = get_config().imagery
        if not cfg.enabled:
            return
        client = client_from_config(cfg)
        if client.free_memory():
            print("[vram] freed diffusion VRAM for LLM")
            _DIFFUSION_VRAM_DIRTY = False
    except Exception as e:
        print(f"[vram] free diffusion failed: {e}")


def _unload_local_llm() -> None:
    """Evict the local LLM (Ollama) from VRAM so diffusion can reclaim it.

    Uses Ollama's native endpoint with keep_alive=0. No-op unless VRAM_TIMESHARE
    is enabled. Best-effort and never raises.
    """
    if not VRAM_TIMESHARE:
        return
    try:
        requests.post(
            OLLAMA_UNLOAD_URL,
            json={"model": LLM_MODEL, "keep_alive": 0},
            timeout=10,
        )
        print("[vram] unloaded local LLM for diffusion")
    except Exception as e:
        print(f"[vram] unload LLM failed: {e}")


# ----- "DM brain" using OpenRouter + Avrae hooks -----

# Full per-condition rules cheat-sheet, injected only when a Combat block or an
# 'Active conditions' line is actually present this turn (see generate_dm_reply).
_CONDITIONS_FULL = (
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
    "  Conditions PERSIST between encounters. The 'Character resources' block shows an\n"
    "  'Active conditions' line for whatever currently afflicts the player — honor it every\n"
    "  turn, even outside a fight. When a lasting condition BEGINS or ENDS on the player\n"
    "  outside the initiative tracker, record it so it carries forward by emitting a hook on\n"
    "  its own line (removed from what the player sees):\n"
    "    [[CONDITION: add | poisoned | giant spider venom | until the end of a long rest]]\n"
    "    [[CONDITION: remove | frightened]]\n"
    "  Fields: action (add/remove/clear) | condition | source (optional) | duration (optional).\n"
    "  Put the REMOVAL TRIGGER in the duration field so you remember how it ends — e.g.\n"
    "  'for 1 minute', 'until it takes damage', 'until the source is out of sight', 'save at\n"
    "  end of each turn', 'until the end of a long rest'. Track that trigger and, the moment it\n"
    "  is met (time elapses, the required action or event happens, or a [[ROLL]] save succeeds),\n"
    "  emit the matching [[CONDITION: remove | ...]] hook and tell the player they're free of it.\n"
    "  Use it for things that outlast the moment (a lingering poison, a curse, ongoing fear);\n"
    "  don't spam it for effects that resolve within the same scene. Exhaustion is tracked\n"
    "  separately — don't emit a CONDITION hook for it.\n"
    "  When a fight or tense encounter ENDS, recap any conditions still on the player in the\n"
    "  narration (e.g. 'the wolves are dead, but the spider's venom still burns in your veins')\n"
    "  so they know what lingers, and remind them of the trigger that will clear it.\n"
)

# Compact stand-in for _CONDITIONS_FULL when no combat/active-condition context is
# present this turn — keeps the [[CONDITION]] hook syntax visible at all times so
# the model can still emit it, without paying for the full cheat-sheet every turn.
_CONDITIONS_SHORT = (
    "- Conditions bind every creature in play; apply their mechanical effects whenever\n"
    "  one is active. When a lasting condition starts or ends on the player, emit a hook on\n"
    "  its own line (removed from what the player sees) so it carries forward:\n"
    "    [[CONDITION: add | poisoned | giant spider venom | until the end of a long rest]]\n"
    "    [[CONDITION: remove | frightened]]\n"
    "  Fields: action (add/remove/clear) | condition | source (optional) | duration (optional).\n"
)

# Full physical-limits detail block, injected only when the resource block shows a
# 'Physical limits' line AND the player's message reads as a physical feat.
_PHYSICAL_LIMITS_FULL = (
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
)

# Compact stand-in for _PHYSICAL_LIMITS_FULL for turns that aren't about a physical feat.
_PHYSICAL_LIMITS_SHORT = (
    "- A 'Physical limits' line caps the PC's speed, jump distance, and lift/carry without\n"
    "  magic — treat it as a hard ceiling and call for a check near its edge.\n"
)

# Always-on contract with the world-graph layer: keeps travel/exploration grounded
# in the 'Beyond the map' entries the world layer supplies, instead of inventing
# settlements or biomes the graph doesn't support.
_WORLD_BUILDING_BLOCK = (
    "World-building & travel:\n"
    "- The World state block is canon. A 'Beyond the map' section lists unexplored areas\n"
    "  adjacent to here, each with a biome, danger, largest-possible-settlement ceiling,\n"
    "  and seed motifs.\n"
    "- When the party travels into unexplored territory, stay INSIDE that area's\n"
    "  constraints: match its biome and danger, and never introduce a settlement larger\n"
    "  than its ceiling. No exceptions — if no 'Beyond the map' entry allows a town, there\n"
    "  is no town nearby.\n"
    "- You may freely invent SMALL discoveries (a shrine, a hollow tree, a peddler's camp,\n"
    "  a ruin entrance) — weave the listed seed motifs into what the party finds before\n"
    "  inventing new ones.\n"
    "- Introduce at most ONE new named place per reply, and give distances in travel time\n"
    "  (hours/days), keeping them consistent with what was said before.\n"
    "- The world is a globe with real positions: nearby places and 'Beyond the map' lines\n"
    "  show a compass direction and travel time — treat those bearings as exact. When you\n"
    "  introduce a new place, SAY which direction it lies and roughly how far (the\n"
    "  mapkeeper places it on the world map from your words). The climate note on the\n"
    "  current location is ground truth — northern latitudes are colder, southern warmer.\n"
    "- Time: a '# Time passed' note means world days elapsed while the player was away.\n"
    "  Welcome them back, mention one or two things that changed, and offer to spend that\n"
    "  time as SRD downtime (training, crafting, work, carousing — lifestyle costs apply)\n"
    "  or to play it out as a short personal side-tale. A side-tale set in past days must\n"
    "  stay personal and local, and may NOT contradict anything already established.\n"
    "- An NPC marked 'memory of you has faded' or 'barely recalls you' hasn't seen this\n"
    "  player in a long while — play the fumbling recognition honestly.\n"
    "- A 'Census' line means the settlement is FULL of implied people beyond the named\n"
    "  NPCs: its population, wards, and trades are real. Freely invent minor folk, shops,\n"
    "  and street detail consistent with the census — a crowded market should FEEL crowded.\n"
    "  Anyone who matters to the story gets remembered automatically; the rest stay crowd.\n"
    "- 'Signs of:' on an unexplored area names the creatures that hunt there. Foreshadow\n"
    "  them (tracks, kills, distant cries) and draw encounters from that list first.\n"
    "Cartography — maps are earned, never given:\n"
    "- There is NO free world map. A character may draft one only with Cartographer's\n"
    "  Tools in inventory: call for [[ROLL: 1d20+<Wis mod, +PB if proficient with the\n"
    "  tools> | Cartography (Wisdom) | DC 15]] (advantage if also proficient in a\n"
    "  relevant skill such as Survival). Then, from the resolved roll, emit on its own\n"
    "  line: [[MAP: draft-success | <area>]] or [[MAP: draft-failure | <area>]].\n"
    "- NEVER reveal that a failed map is wrong. Hand it over with the same confidence —\n"
    "  its owner discovers the errors by getting lost. Describe drafting time honestly\n"
    "  (an hour or more of careful sightlines and measurements).\n"
    "- A drafter can only chart what they KNOW — places they have visited or genuinely\n"
    "  learned of (an NPC's directions, a bought map). The system enforces this: unknown\n"
    "  places simply won't appear on their parchment, however well they roll.\n"
    "- In settlements, a map-maker (market wards often keep one) sells a regional map\n"
    "  for ~25 gp: emit [[MAP: purchase | <region>]] once the sale is agreed. Bought\n"
    "  maps also mark RUMORED, uncharted sites — hooks to places no one has explored —\n"
    "  and studying one teaches the buyer those places for their own future drafts.\n"
    "Commerce — merchants' stock lines are ground truth:\n"
    "- NPCs with a 'stock (this week)' line sell exactly those items at exactly those\n"
    "  prices. Haggle in the fiction, but when a deal is STRUCK, emit on its own line:\n"
    "    [[TRADE: buy | Longsword | 15]]   [[TRADE: sell | wolf pelt | 2]]\n"
    "  The system moves the real coin and items and appends the receipt — NEVER adjust\n"
    "  the purse yourself. Selling used gear fetches about half its list value.\n"
    "- Gambling (needs a den or den keeper present): agree the stake (1-50 gp), then\n"
    "  emit [[TRADE: wager | dice | 5]]. The system rolls it out; the house wins ties.\n"
    "  Narrate the den's atmosphere around the appended result.\n"
    "- Mounts: stablemasters sell them (buy via [[TRADE]]). A mounted character travels\n"
    "  at a fast pace (~30 miles/day vs 24 on foot) and can gallop short bursts at double\n"
    "  speed; mounts need feed and stabling (charge for both), can panic in combat, and\n"
    "  can't follow into dungeons, dense woods, or up ropes — make ownership matter.\n"
    "- A '# Danger assessment' block means the party is outmatched nearby: seed WARNINGS\n"
    "  into the world (an NPC interdicts, fresh kills on the trail, refugees, rumors) so\n"
    "  informed choice is always possible. Scale avoidable encounters to the party; NEVER\n"
    "  scale down a danger they were warned about and chose anyway. Player death is\n"
    "  PERMANENT — no rescues you didn't foreshadow, no take-backs. Make death mean\n"
    "  something: let it be earned, witnessed, and remembered.\n"
)


# Hard rules injected for cc_guide:* sessions. Kept terse and imperative — the
# local model follows short laws better than prose (see memory: terse beats
# encoded). These are 5e-RAW level-1 creation constraints.
_CC_RULES_BLOCK = (
    "CHARACTER CREATION LAWS (binding — refuse anything outside them, kindly, "
    "and offer legal alternatives):\n"
    "1. Level 1 only. No starting feats, multiclassing, or magic items unless a "
    "rule below grants them.\n"
    "2. Ability scores come from ONE of: standard array (15,14,13,12,10,8); "
    "point buy; or rolling 4d6-drop-lowest per score.\n"
    "3. NEVER invent dice results. To roll ability scores, output EXACTLY six "
    "hooks, each on its own line, then STOP and wait:\n"
    "[[ROLL: 4d6kh3 | Ability roll 1]]\n"
    "[[ROLL: 4d6kh3 | Ability roll 2]]\n"
    "[[ROLL: 4d6kh3 | Ability roll 3]]\n"
    "[[ROLL: 4d6kh3 | Ability roll 4]]\n"
    "[[ROLL: 4d6kh3 | Ability roll 5]]\n"
    "[[ROLL: 4d6kh3 | Ability roll 6]]\n"
    "The system replaces each hook with a real roll. NEXT turn, ask the player "
    "to assign the six results to STR/DEX/CON/INT/WIS/CHA.\n"
    "4. Score bounds: base scores 3-18 BEFORE racial bonuses; no final score "
    "above 20. If a total would exceed 20, it is illegal — fix it.\n"
    "5. Racial bonuses: the chosen race's standard bonuses. A CUSTOM race/"
    "lineage is allowed but gets exactly: +2 to ONE ability, one skill "
    "proficiency, darkvision OR one extra language, and ONE 1st-level feat the "
    "character QUALIFIES for. Nothing more.\n"
    "6. Feat prerequisites are binding. War Caster and Elemental Adept require "
    "the ability to cast at least one spell: at level 1 only bard, cleric, "
    "druid, sorcerer, warlock, and wizard qualify (NOT barbarian, fighter, "
    "monk, rogue, ranger, or paladin). If the player already chose a feat and "
    "later picks a class that breaks its prerequisite, point out the conflict "
    "and make them change one of the two.\n"
    "7. NEVER invent feats, class features, racial traits, or mechanics — no "
    "homebrew. Only officially published feats exist. If the player wants a "
    "unique concept, deliver it through LEGAL choices: ability placement, "
    "skill proficiencies, background, and roleplay (e.g. a charismatic "
    "barbarian = high CHA + Intimidation or Persuasion proficiency), and "
    "suggest real feats by their official names only.\n"
    "8. Racial ability bonuses apply EXACTLY ONCE, to exactly the abilities "
    "the race grants (custom lineage: +2 to the ONE ability chosen when the "
    "race was picked — it never moves, splits, or repeats). When recapping, "
    "show each score once as its final value; do not re-add bonuses that are "
    "already included.\n"
    "9. When restating the sheet, list FINAL scores (base + racial) and never "
    "contradict numbers already established this conversation.\n"
)


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

    # Token diet for the 14B local model: the full per-condition rules cheat-sheet
    # and the full physical-limits detail block are only worth their weight when
    # they're actually relevant this turn. Detect relevance from the context
    # blocks already assembled for this turn (world slice, combat board,
    # character-resources block) plus a cheap keyword scan of the player's
    # message, and fall back to a compact summary otherwise. The hook syntax
    # stays visible either way so the model can always emit it.
    _combined_ctx = "\n".join(extra_context or [])
    _combat_present = "# Combat" in _combined_ctx
    _active_conditions_present = "Active conditions" in _combined_ctx
    _show_conditions_detail = _combat_present or _active_conditions_present

    _physical_limits_present = "Physical limits" in _combined_ctx
    _physical_keywords = (
        "jump", "climb", "lift", "throw", "swim", "leap", "carry", "push", "drag",
    )
    _looks_physical = any(kw in message.lower() for kw in _physical_keywords)
    _show_physical_detail = _physical_limits_present and _looks_physical

    _physical_limits_block = (
        _PHYSICAL_LIMITS_FULL if _show_physical_detail else _PHYSICAL_LIMITS_SHORT
    )
    _conditions_block = (
        _CONDITIONS_FULL if _show_conditions_detail else _CONDITIONS_SHORT
    )

    system_prompt = (
        "You are an imaginative, fair Dungeon Master for a 5e-style tabletop RPG. "
        "You narrate the world, voice NPCs, and adjudicate the outcomes of actions. "
        "ALWAYS respond in English only, regardless of the model's default language.\n\n"
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
        "- Guardrails apply across ALL systems (combat, exploration, social, downtime,\n"
        "  crafting, hazards, travel). Before allowing an action, verify prerequisites:\n"
        "  required gear/tools in inventory, required capability (class features, spells,\n"
        "  level, movement/reach/conditions), and required fiction state (position, line of\n"
        "  sight, free hands, etc.). If a prerequisite is missing, do not allow the action;\n"
        "  explain what's missing and offer valid alternatives.\n"
        "- Crafting guardrail: don't narrate crafting success if prerequisites are missing.\n"
        "  Require relevant tools and capability for the item tier. For very rare or higher\n"
        "  magic item crafting, require a matching recipe/formula book first.\n"
        "- Specific hard prerequisites the backend also enforces (respect them in narration):\n"
        "  * Disarming a mechanical trap requires thieves' tools in inventory.\n"
        "  * Picking a lock requires thieves' tools; forcing it uses Strength instead.\n"
        "  * Lighting/burning a light source requires carrying it (a lantern also needs oil).\n"
        "  * Curing a disease or affliction needs a real means: a suitable spell known\n"
        "    (e.g. lesser/greater restoration) or a fitting item — never 'cured with nothing'.\n"
        "  * Bastions require the level minimum, funds for facilities, and one bastion per PC.\n"
        "  * LEVEL-UP GATE: when a PC earns enough XP for a level-up during an encounter or\n"
        "    exploration, they can't continue adventuring until they complete a long rest\n"
        "    (which flags them as pending level-up) and then call /level_up to choose feats,\n"
        "    ASIs, spells, or subclass. Once /level_up completes, they're ready to adventure.\n"

        + _physical_limits_block
        + _conditions_block +
        "- NPCs & the party. Most NPCs are LIGHTWEIGHT: a name, role, disposition, and a running\n"
        "  TRUST toward the party — you can invent and voice them freely without any stat sheet.\n"
        "  The world-state block lists nearby NPCs with their current attitude; play them to it.\n"
        "  * Trust grows or sours through play (favors, honesty, shared danger vs. insults,\n"
        "    betrayal, broken promises) and moves an NPC along hostile -> unfriendly -> indifferent\n"
        "    -> friendly -> helpful. When an interaction should shift how an NPC feels, record it on\n"
        "    its own line (removed from what the player sees):\n"
        "      [[TRUST: Marta Fenn | +5 | the party saved her tavern]]\n"
        "      [[TRUST: Old Ferran | -3 | they mocked his warnings]]\n"
        "    Fields: npc (name) | signed amount (small, ~1-10) | reason. Let the earned attitude,\n"
        "    not plot convenience, decide how far an NPC will go for the party.\n"
        "  * A friendly/helpful NPC may OFFER to travel with the party. Joining is the PLAYER's\n"
        "    call, not yours: surface the offer and ask whether they want the companion along and\n"
        "    whether THEY will run that companion or leave it to you (the DM). Don't auto-add an NPC\n"
        "    to the party. Once an NPC joins, they become a full character (class, subclass, and\n"
        "    level-appropriate features) who can fight and level up like the player — the system\n"
        "    handles that promotion when the party recruits them.\n"
        "  * Companions currently traveling with the party appear in a 'Party companions' list with\n"
        "    who runs each (player-run or DM-run). Run DM-run companions as loyal allies with their\n"
        "    own voice; for player-run companions, prompt the player for that character's actions.\n"

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
        "\n"
        + _WORLD_BUILDING_BLOCK
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
            "- Fields: kind (place|npc|creature|item|scene) | subject | context (environment/season/mood) | look.\n"
            "- Use kind 'scene' for a dramatic MOMENT involving the player or known figures —\n"
            "    [[IMAGE: scene | Kara hurls a fireball at the goblin | torchlit dungeon melee | desperate, embers flying]]\n"
            "  Name the participants exactly (the player's name, the creature) — the system uses\n"
            "  their existing portraits/art as visual references so the picture shows THEM.\n"
            "- The SAME subject in a different environment (a desert wolf vs a jungle wolf, an NPC in\n"
            "  town vs in the desert) is a distinct picture: keep 'subject' stable, vary 'context'.\n"
            "- If a subject's appearance changes PERMANENTLY (an NPC is maimed, a town burns down),\n"
            "  emit [[IMAGE-RESET: kind | subject | reason]] so outdated pictures are cleared.\n"
            f"- At most {_img_cfg.max_images_per_reply} image hook(s) per reply. Put each on its own line;\n"
            "  hooks are removed from what the player sees.\n"
        )

    messages.append({"role": "system", "content": system_prompt})

    # Character-creation sessions get the CREATION LAWS: legal stat generation
    # (real dice via ROLL hooks, never invented numbers), score caps, and feat
    # prerequisites. Without this the model improvises its own rules.
    if session_id.startswith("cc_guide:"):
        messages.append({"role": "system", "content": _CC_RULES_BLOCK})

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

    total_chars = sum(len(m.get("content", "")) for m in messages)
    print(f"[prompt] ~{total_chars // 4} tokens ({total_chars} chars, {len(messages)} messages)")

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
    # Anchor the world slice on the ACTING player's PC at a shared table.
    acting = _acting_member(meta or {}, user_id)
    anchor_slug = (acting or {}).get("pc_slug") or (meta or {}).get("pc_slug")
    if meta and anchor_slug:
        try:
            ctx_obj = world.get_world_context(anchor_slug, message)
            rendered = ctx_obj.render()
            if rendered.strip():
                texts.append(rendered)
        except Exception as e:
            print(f"[world context error] {e}")

        # Danger assessment: when the party is in (or beside) country whose
        # danger outstrips their level, tell the DM to warn and to scale —
        # but never to soften a fight the players walk into anyway.
        try:
            # Danger keys on the LOWEST-level PC at the table: warnings must
            # protect the most fragile member, not the average.
            char_level = 1
            member_ids = [m.get("character_id")
                          for m in _session_members(meta).values()
                          if m.get("character_id")]
            if member_ids:
                with Session(engine) as s:
                    levels = [max(1, int(ch.level or 1))
                              for cid in member_ids
                              if (ch := s.get(Character, cid))]
                if levels:
                    char_level = min(levels)
            danger_floor = {"low": 1, "moderate": 3, "high": 5}
            hot: list[str] = []
            if ctx_obj is not None:
                spots = list(ctx_obj.entities)
                if ctx_obj.location is not None:
                    spots.append(ctx_obj.location)
                for e in spots:
                    a = e.attributes or {}
                    lvl_needed = danger_floor.get(str(a.get("danger", "")).lower())
                    if lvl_needed and lvl_needed > char_level:
                        denizens = ", ".join(a.get("denizens") or []) or "unknown threats"
                        hot.append(f"- {e.name}: danger {a['danger']} "
                                   f"(suits level {lvl_needed}+; party is level "
                                   f"{char_level}). Known threats: {denizens}.")
            if hot:
                texts.append(
                    "# Danger assessment\n"
                    "The party is under-leveled for these nearby areas:\n"
                    + "\n".join(hot) +
                    "\nForeshadow the threat and have locals warn or interdict "
                    "(a hunter blocks the road, tracks and kills, fearful talk). "
                    "Scale encounters to the encounter-building guidance when the "
                    "fight is avoidable — but if they knowingly press into danger, "
                    "play it honestly and lethally. Death is permanent here."
                )
        except Exception as e:
            print(f"[danger assessment error] {e}")

        # Away-time notice: if world days passed since this PC last acted
        # (another party's bubble, or the wall-clock floor), tell the DM so
        # they can welcome the player back and offer downtime catch-up.
        try:
            pc_e = world.get_entity(anchor_slug)
            today = ctx_obj.world_day if ctx_obj else world.current_day()
            if pc_e is not None:
                last = (pc_e.attributes or {}).get("last_active_day")
                gap = (today - int(last)) if last is not None else 0
                if gap >= 3:
                    texts.append(
                        f"# Time passed\n{gap} world days have passed since "
                        f"{pc_e.name} last acted. Welcome them back and offer to "
                        "spend that time as SRD downtime (training, crafting, "
                        "work, carousing — lifestyle costs apply) or as a short "
                        "personal side-tale covering those days."
                    )
                if last is None or today > int(last):
                    world.upsert_entity(pc_e.name, pc_e.type, slug=pc_e.slug,
                                        status=pc_e.status,
                                        attributes={"last_active_day": today})
        except Exception as e:
            print(f"[away-time notice error] {e}")

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


def _normalize_item_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _has_inventory_item(char: Character, name: str) -> bool:
    """True if an item matching ``name`` exists in the character inventory."""
    needle = _normalize_item_name(name)
    if not needle:
        return False
    for it in _inventory_items(char):
        nm = _normalize_item_name(str(it.get("name", "")))
        if not nm:
            continue
        if nm == needle or needle in nm or nm in needle:
            try:
                if int(it.get("quantity", 1) or 1) >= 1:
                    return True
            except (TypeError, ValueError):
                return True
    return False


def _add_inventory_item(char: Character, name: str, *, quantity: int = 1, extra: Optional[Dict[str, Any]] = None) -> None:
    """Add/increment an item in the character's JSON inventory."""
    inv = _inventory_items(char)
    target = _normalize_item_name(name)
    for it in inv:
        nm = _normalize_item_name(str(it.get("name", "")))
        if nm == target:
            try:
                it["quantity"] = int(it.get("quantity", 1) or 1) + quantity
            except (TypeError, ValueError):
                it["quantity"] = quantity
            char.inventory = inv
            return
    item: Dict[str, Any] = {"name": name, "quantity": quantity}
    if extra:
        item.update(extra)
    inv.append(item)
    char.inventory = inv


def _character_spell_names(char: Character) -> List[str]:
    """Normalized names of spells known/prepared on the character sheet."""
    out: List[str] = []
    for raw in (char.spells or []):
        if isinstance(raw, str):
            out.append(_normalize_item_name(raw))
        elif isinstance(raw, dict):
            nm = raw.get("name") or raw.get("spell") or ""
            if nm:
                out.append(_normalize_item_name(str(nm)))
    return [s for s in out if s]


def _has_spell(char: Character, name: str) -> bool:
    """True if the character knows/has prepared a spell matching ``name``."""
    needle = _normalize_item_name(name)
    if not needle:
        return False
    for nm in _character_spell_names(char):
        if nm == needle or needle in nm or nm in needle:
            return True
    return False


# Spells/effects that can end a disease or similar affliction in 5e.
_DISEASE_CURE_SPELLS = {
    "lesser restoration",
    "greater restoration",
    "protection from poison",
    "heal",
    "mass heal",
    "purify food and drink",
}

# Light sources map to the inventory item that must be carried to burn one.
_LIGHT_SOURCE_ITEMS = {
    "torch": "torch",
    "lantern": "lantern",
    "hooded lantern": "hooded lantern",
    "bullseye lantern": "bullseye lantern",
    "candle": "candle",
    "lamp": "lamp",
}


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


# The 5e conditions we recognize for persistence. Exhaustion is handled separately
# as a numeric level, so it is intentionally NOT in this set.
_KNOWN_CONDITIONS = {
    "blinded", "charmed", "deafened", "frightened", "grappled", "incapacitated",
    "invisible", "paralyzed", "petrified", "poisoned", "prone", "restrained",
    "stunned", "unconscious",
}
_CONDITION_ALIASES = {
    "scared": "frightened", "afraid": "frightened", "fear": "frightened",
    "blind": "blinded", "deaf": "deafened", "poison": "poisoned",
    "knocked prone": "prone", "knocked out": "unconscious", "ko": "unconscious",
    "grapple": "grappled", "restrain": "restrained", "stun": "stunned",
    "paralysis": "paralyzed", "petrify": "petrified", "charm": "charmed",
}


def _normalize_condition_name(name: str) -> str:
    n = (name or "").strip().lower()
    n = _CONDITION_ALIASES.get(n, n)
    return n


def _character_conditions(char: Character) -> List[Dict[str, Any]]:
    """Normalize the persistent conditions blob into a list of dicts."""
    out: List[Dict[str, Any]] = []
    for raw in (char.conditions or []):
        if isinstance(raw, str):
            out.append({"name": _normalize_condition_name(raw)})
        elif isinstance(raw, dict) and raw.get("name"):
            entry = dict(raw)
            entry["name"] = _normalize_condition_name(entry["name"])
            out.append(entry)
    return out


def _conditions_summary(char: Character) -> Optional[str]:
    """A 'Conditions:' line for the DM prompt, or None if the PC has none."""
    conds = _character_conditions(char)
    if not conds:
        return None
    parts = []
    for c in conds:
        label = c["name"]
        extra = []
        if c.get("source"):
            extra.append(f"from {c['source']}")
        if c.get("duration"):
            extra.append(str(c["duration"]))
        if extra:
            label += f" ({', '.join(extra)})"
        parts.append(label)
    return "Active conditions (persist between encounters): " + "; ".join(parts) + "."


def _apply_condition_op(char: Character, op: Dict[str, Any]) -> bool:
    """Add/remove/clear a persistent condition on the character in place.

    ``op`` = {action: add|remove|clear, name, source?, note?, duration?,
    clears_on_long_rest?}. Returns True if the character's conditions changed.
    """
    action = (op.get("action") or "add").strip().lower()
    conds = _character_conditions(char)

    if action == "clear":
        changed = bool(conds)
        char.conditions = []
        return changed

    name = _normalize_condition_name(op.get("name", ""))
    if not name:
        return False

    if action == "remove":
        new = [c for c in conds if c["name"] != name]
        changed = len(new) != len(conds)
        char.conditions = new
        return changed

    # add / update
    entry: Dict[str, Any] = {"name": name}
    if op.get("source"):
        entry["source"] = str(op["source"])[:120]
    if op.get("duration"):
        entry["duration"] = str(op["duration"])[:80]
    if op.get("note"):
        entry["note"] = str(op["note"])[:200]
    dur_l = (entry.get("duration") or "").lower()
    entry["clears_on_long_rest"] = bool(op.get("clears_on_long_rest")) or ("long rest" in dur_l)
    try:
        entry["applied_day"] = world.current_day()
    except Exception:
        pass
    # Replace any existing entry of the same name (update semantics).
    new = [c for c in conds if c["name"] != name]
    new.append(entry)
    char.conditions = new
    return True


def _clear_long_rest_conditions(char: Character) -> List[str]:
    """Drop conditions flagged to end on a long rest. Returns removed names."""
    conds = _character_conditions(char)
    keep, removed = [], []
    for c in conds:
        if c.get("clears_on_long_rest"):
            removed.append(c["name"])
        else:
            keep.append(c)
    if removed:
        char.conditions = keep
    return removed


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
            "temp_hp": getattr(char, "temp_hp", 0) or 0,
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
        "conditions": _character_conditions(char),
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

            # Current weather at the PC's position: latitude-derived climate
            # from the world globe, falling back to the home_region keyword guess.
            try:
                day = world.current_day()
                month = ((day // 30) % 12) + 1
                try:
                    climate = world.location_climate(slugify(char.name))
                except Exception:
                    climate = None
                climate = climate or _region_climate(char.home_region)
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

        # Persistent conditions/status effects carried between encounters.
        cond_line = _conditions_summary(char)
        if cond_line:
            lines.append(cond_line)

        # Physical limits — movement, jumps, lifting, reach — so the DM can gate
        # feats of athletics that are impossible without magic or special items.
        lines.append(_physical_capabilities_summary(char))

        # Class + subclass + species features the character has at this level
        # (exact mechanics, so the DM adjudicates by the numbers, not from
        # memory). Only features up to the PC's current level are included.
        try:
            from rules.query import RulesLibrary
            _lib = RulesLibrary(engine=engine)

            cfeats = _lib.class_features_up_to(char.char_class or "", char.level)
            cls_row = _get_class_row(session, char.char_class)
            sfeats = (_subclass_features_up_to(session, cls_row, char.subclass, char.level)
                      if cls_row else [])
            class_all = cfeats + [f for f in sfeats]
            if class_all:
                rendered = "; ".join(
                    f"{f['name']} ({(f.get('summary') or '')[:130].rstrip()})"
                    for f in class_all[:10])
                label = char.char_class + (f"/{char.subclass}" if char.subclass else "")
                lines.append(f"Class features — {label}: {rendered}")

            rfeats = _lib.race_features_up_to(char.race or "", char.level)
            if rfeats:
                rendered = "; ".join(
                    f"{f['name']} ({f['summary'][:150].rstrip()})" for f in rfeats[:8])
                lines.append(f"Species features — {char.race}: {rendered}")
        except Exception as e:
            print(f"[features block] {e}")
        return "\n".join(lines)


# ----- Routes -----

# Embedded Discord Activities always load at the mapped origin ROOT, so "/"
# serves the built Activity SPA when present. Programmatic health lives at
# /healthz; the launcher's check only inspects status, and "/" still 200s.
_ACTIVITY_INDEX_PATH = Path(__file__).resolve().parent.parent / "activity-ui" / "dist" / "index.html"


@app.get("/")
async def root():
    if _ACTIVITY_INDEX_PATH.is_file():
        return FileResponse(str(_ACTIVITY_INDEX_PATH))
    return {"status": "ok", "message": "Oracle DM backend with OpenRouter + Avrae hooks running"}


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "message": "Oracle DM backend with OpenRouter + Avrae hooks running"}


@app.post("/chat", response_model=ChatResponse)
def chat_endpoint(req: ChatRequest, background_tasks: BackgroundTasks):
    # NOTE: deliberately a sync (def) endpoint. Everything inside — the LLM call,
    # ComfyUI image renders (up to timeout_seconds), VRAM unloads — uses blocking
    # `requests`. As a sync route FastAPI runs it in the threadpool, so long
    # renders no longer freeze the event loop for every other player.
    """
    Main entry point from the Discord bot.
    Tracks per-session history and asks the DM brain for a reply.
    """

    game_state = _load_session_state(req.session_id)

    # Per-PC gates: they bind the ACTING player's character, never the table.
    member = _acting_member(game_state.get("meta", {}) or {}, req.user_id)
    char_id = (member or {}).get("character_id")
    if char_id:
        with Session(engine) as session:
            char = session.get(Character, char_id)
            if char and char.pending_level_up:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"{char.name} has earned a level-up (XP {char.xp}). "
                        "Complete level-up choices via /level_up before continuing."
                    ),
                )
        # Downtime commitment: a PC mid-commitment can't act until the
        # canonical clock reaches their busy_until_day.
        wait = _pc_busy_until((member or {}).get("pc_slug"))
        if wait:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{(member or {}).get('character_name') or 'This character'} "
                    f"is committed to downtime for {wait} more world day(s) — "
                    "the tale resumes when they are free."
                ),
            )

    # Player turn
    state = _append_turn(req.session_id, Turn(role="player", user=req.username, content=req.message))

    # Ratchet clock: apply the wall-time floor and any due entropy pass BEFORE
    # assembling context, so the DM narrates the world as time has left it.
    try:
        world.sync_clock(days_per_real_day=WORLD_DAYS_PER_REAL_DAY)
        _ent = world_entropy.run_if_due(world)
        if any(_ent.values()):
            print(f"[entropy] {_ent}")
    except Exception as e:
        print(f"[entropy] clock sync failed: {e}")

    # DM reply
    ctx_obj = None
    _engine_block: Optional[str] = None
    try:
        # Single-GPU time-share: free the diffusion model from VRAM before the
        # LLM call so a self-hosted 70B/32B has room to load.
        _free_diffusion_vram()
        # Ground the turn in the local world slice, referenced rules, and — if a
        # fight is underway — the live initiative/HP board.
        ctx_obj, ctx_texts = assemble_context(req.session_id, req.message, user_id=req.user_id)
        active_enc = combat.get_active(req.session_id)
        if active_enc:
            # Deterministic path: resolve the player's declared acts through
            # the engine FIRST; the board below then reflects the new state.
            try:
                _engine_block = _combat_engine_turn(
                    req.session_id, req.user_id, req.message)
            except Exception as e:
                print(f"[combat-engine] turn failed, hook fallback: {e}")
            board = combat.render(active_enc.id)
            if board.strip():
                ctx_texts.append(board)
            if _engine_block is not None:
                ctx_texts.append(_engine_block)
                ctx_texts.append(_COMBAT_NARRATE_CONTRACT)
            else:
                ctx_texts.append(_COMBAT_HOOKS_ACTIVE)
        else:
            ctx_texts.append(_COMBAT_HOOKS_IDLE)
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
    # Single-GPU time-share: only evict the local LLM when we actually need the
    # GPU for a render, so text-only turns keep the model warm.
    if image_reqs or reset_reqs:
        _unload_local_llm()
        _mark_diffusion_dirty()
    try:
        image_payloads = process_image_hooks(
            image_reqs, reset_reqs,
            pc_name=(state.get("meta", {}) or {}).get("character_name"),
            ctx_entities=list(ctx_obj.entities) if ctx_obj else None,
            mature=bool((state.get("meta", {}) or {}).get("mature", False)),
        )
        # Player cartography: rendered map artifacts ride the image channel.
        dm_text, map_ops = extract_map_hooks(dm_text)
        if map_ops:
            try:
                image_payloads.extend(process_map_hooks(map_ops, req.session_id))
            except Exception as e:
                print(f"[map] hook processing failed: {e}")

        # Commerce: agreed trades move real coin/items; outcomes are appended.
        dm_text, trade_ops = extract_trade_hooks(dm_text)
        if trade_ops:
            try:
                trade_notes = process_trade_hooks(trade_ops, req.session_id, ctx_obj)
                if trade_notes:
                    dm_text = dm_text.rstrip() + "\n\n" + "\n".join(trade_notes)
            except Exception as e:
                print(f"[trade] hook processing failed: {e}")
    except Exception as e:
        print(f"[imagery] hook processing failed: {e}")
        image_payloads = []

    # Persist any lasting conditions the DM applied/cleared on the PC so they
    # carry into the next encounter.
    dm_text, condition_ops = extract_condition_hooks(dm_text)
    if condition_ops:
        try:
            state = _load_session_state(req.session_id)
            character_id = (_acting_member(state.get("meta", {}) or {}, req.user_id) or {}).get("character_id")
            if not character_id:
                character_id = _resolve_session_character(req.session_id, req.user_id)
            apply_condition_hooks(character_id, condition_ops)
        except Exception as e:
            print(f"[conditions] hook processing failed: {e}")

    # Record any NPC trust shifts the DM signalled during the scene.
    dm_text, trust_ops = extract_trust_hooks(dm_text)
    if trust_ops:
        try:
            state = _load_session_state(req.session_id)
            character_id = (_acting_member(state.get("meta", {}) or {}, req.user_id) or {}).get("character_id")
            if not character_id:
                character_id = _resolve_session_character(req.session_id, req.user_id)
            apply_trust_hooks(character_id, trust_ops)
        except Exception as e:
            print(f"[trust] hook processing failed: {e}")

    # Keep the initiative tracker true to the narration: start fights, seat
    # combatants, record damage/healing, advance turns, end the battle.
    # When the deterministic engine resolved this exchange, the DM may only
    # add reinforcements or end the fight — everything else is engine-owned.
    dm_text, combat_ops = extract_combat_hooks(dm_text)
    if combat_ops and _engine_block is not None:
        combat_ops = [op for op in combat_ops if op["action"] in ("add", "end")]
    if combat_ops:
        try:
            combat_notes = apply_combat_hooks(req.session_id, combat_ops)
            if combat_notes:
                dm_text = dm_text.rstrip() + "\n\n" + "\n".join(combat_notes)
        except Exception as e:
            print(f"[combat] hook processing failed: {e}")

    # Store DM turn
    state = _append_turn(req.session_id, Turn(role="dm", user="Oracle DM", content=dm_text))
    if len(state.get("recent_turns", [])) > get_config().session_memory.compaction_threshold:
        _schedule_session_compaction(background_tasks, req.session_id)

    # Write this turn's world-state changes to the graph in the background —
    # never blocks the reply the player is waiting on.
    background_tasks.add_task(
        _run_world_extraction,
        req.session_id,
        req.message,
        dm_text,
        ctx_obj.render() if ctx_obj else "",
        [e.id for e in ctx_obj.entities] if ctx_obj else [],
    )

    # Permadeath check: if this turn confirmed the PC's death, canonize it and
    # attach the one-page memorial for the bot to post (fires exactly once).
    memorial_payload = None
    try:
        memorial_payload = _maybe_memorialize(req.session_id)
    except Exception as e:
        print(f"[memorial] check failed: {e}")

    return ChatResponse(reply=dm_text, music=music_query,
                        images=image_payloads or None,
                        memorial=memorial_payload)

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
    def _is_alive(c) -> bool:
        # Owner-scoped lookup: another player's same-named dead PC must not
        # shadow this player's living one (identities are per-owner now).
        e = world.find_pc(req.user_id, c.name)
        return e is None or e.status != "dead"

    if not chosen:
        # Auto-choice prefers the living: dead characters stay in the DB as
        # canon (memorials/events reference them) but never get picked here.
        chosen = next((c for c in chars if _is_alive(c)), chars[0])

    # Permadeath gate: a character whose world entity is dead stays dead.
    try:
        pc_entity = world.find_pc(req.user_id, chosen.name)
        if pc_entity is not None and pc_entity.status == "dead":
            living = [c.name for c in chars if _is_alive(c)]
            return EnterResponse(
                status="character_dead",
                message=(
                    f"{chosen.name} has passed into legend — their tale is told and "
                    "cannot be resumed. "
                    + (f"You may enter as: {', '.join(living)}. " if living else "")
                    + "Or create a new character to begin a new tale."
                ),
                characters=living,
            )
    except Exception as e:
        print(f"[enterworld permadeath check error] {e}")

    # Create a new session id (guild-based namespace)
    session_id = f"{req.guild_id}:{uuid.uuid4().hex}"

    # Place the PC in the persistent world graph and remember it for this session.
    # Use the RETURNED entity's slug — identities have unique slugs now, so the
    # slug may differ from slugify(name) when names repeat across players.
    pc_slug = slugify(chosen.name)
    try:
        pc_ent = place_pc(
            world,
            chosen.name,
            discord_user_id=req.user_id,
            location_slug="the-silver-tankard",
            attributes={"race": chosen.race, "class": chosen.char_class, "subclass": chosen.subclass, "level": chosen.level},
        )
        if pc_ent is not None and getattr(pc_ent, "slug", None):
            pc_slug = pc_ent.slug
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

    # Resolve any opening scene picture the DM asked for — and GUARANTEE an
    # establishing shot of the starting locale even when it emitted no hook, so
    # the very first thing a player sees is a drawn scene, not a wall of text.
    intro, image_reqs, reset_reqs = extract_image_hooks(intro)
    if not image_reqs:
        try:
            loc = world.location_of(pc_slug)
            place_name = getattr(loc, "name", None) or "the Silver Tankard"
        except Exception:
            place_name = "the Silver Tankard"
        image_reqs = [{
            "kind": "place",
            "subject": place_name,
            "context": f"arrival at {getattr(chosen, 'home_region', 'Greenfields')}",
            "look": "",
        }]
    opening_images: list[dict] = []
    try:
        if image_reqs or reset_reqs:
            _unload_local_llm()   # single-GPU: free the LLM before we render
            _mark_diffusion_dirty()
        opening_images = process_image_hooks(
            image_reqs, reset_reqs,
            pc_name=chosen.name,
            ctx_entities=list(_ctx_obj.entities) if _ctx_obj else None,
            mature=_table_is_mature(session_id),
        )
    except Exception as e:
        print(f"[enterworld imagery error] {e}")

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
        images=opening_images or None,
    )


# SRD starting equipment by class — granted whenever a new character has an
# empty inventory (guided creation, manual, or a DDB import whose gear was all
# dropped by validation). Names align with the equipment guardrails.
_CLASS_STARTING_KITS: Dict[str, List[tuple]] = {
    "barbarian": [("Greataxe", 1), ("Handaxe", 2), ("Javelin", 4), ("Explorer's Pack", 1)],
    "bard": [("Rapier", 1), ("Dagger", 1), ("Leather Armor", 1), ("Lute", 1), ("Entertainer's Pack", 1)],
    "cleric": [("Mace", 1), ("Scale Mail", 1), ("Shield", 1), ("Holy Symbol", 1), ("Priest's Pack", 1)],
    "druid": [("Quarterstaff", 1), ("Leather Armor", 1), ("Druidic Focus", 1), ("Explorer's Pack", 1)],
    "fighter": [("Chain Mail", 1), ("Longsword", 1), ("Shield", 1), ("Light Crossbow", 1), ("Crossbow Bolts", 20), ("Dungeoneer's Pack", 1)],
    "monk": [("Shortsword", 1), ("Dart", 10), ("Dungeoneer's Pack", 1)],
    "paladin": [("Chain Mail", 1), ("Longsword", 1), ("Shield", 1), ("Holy Symbol", 1), ("Priest's Pack", 1)],
    "ranger": [("Scale Mail", 1), ("Shortsword", 2), ("Longbow", 1), ("Arrows", 20), ("Explorer's Pack", 1)],
    "rogue": [("Rapier", 1), ("Shortbow", 1), ("Arrows", 20), ("Leather Armor", 1), ("Dagger", 2), ("Thieves' Tools", 1), ("Burglar's Pack", 1)],
    "sorcerer": [("Light Crossbow", 1), ("Crossbow Bolts", 20), ("Component Pouch", 1), ("Dagger", 2), ("Dungeoneer's Pack", 1)],
    "warlock": [("Light Crossbow", 1), ("Crossbow Bolts", 20), ("Component Pouch", 1), ("Leather Armor", 1), ("Dagger", 2), ("Scholar's Pack", 1)],
    "wizard": [("Quarterstaff", 1), ("Component Pouch", 1), ("Spellbook", 1), ("Scholar's Pack", 1)],
    # Allowed homebrew classes (owned third-party content).
    "illrigger": [("Longsword", 1), ("Shield", 1), ("Chain Shirt", 1), ("Javelin", 5), ("Priest's Pack", 1)],
    "gunslinger": [("Leather Armor", 1), ("Dagger", 2), ("Revolver", 1), ("Bullets", 50), ("Explorer's Pack", 1)],
}


# SRD background packages: equipment + the signature feature (stored on the
# character's tags so the DM prompt can honor it).
# 2024 model: each background lists the 3 ability scores it can boost (the
# player spreads +2/+1 across two of them, or +1/+1/+1 across all three) and
# grants a specific Origin feat. Abilities are 3-letter codes; origin_feat is a
# slug into the feat pool (rules/feats_seed). Skills/items/feature unchanged.
_BACKGROUND_KITS: Dict[str, Dict[str, Any]] = {
    "acolyte": {"skills": ['Insight', 'Religion'], "abilities": ["wis", "int", "cha"],
                "origin_feat": "magic-initiate",
                "items": [("Holy Symbol", 1), ("Prayer Book", 1), ("Incense", 5), ("Vestments", 1)],
                "feature": "Shelter of the Faithful"},
    "charlatan": {"skills": ['Deception', 'Sleight of Hand'], "abilities": ["dex", "con", "cha"],
                  "origin_feat": "skilled",
                  "items": [("Fine Clothes", 1), ("Disguise Kit", 1), ("Weighted Dice", 1)],
                  "feature": "False Identity"},
    "criminal": {"skills": ['Deception', 'Stealth'], "abilities": ["dex", "con", "int"],
                 "origin_feat": "alert",
                 "items": [("Crowbar", 1), ("Dark Common Clothes", 1)],
                 "feature": "Criminal Contact"},
    "entertainer": {"skills": ['Acrobatics', 'Performance'], "abilities": ["str", "dex", "cha"],
                    "origin_feat": "musician",
                    "items": [("Musical Instrument", 1), ("Costume", 1)],
                    "feature": "By Popular Demand"},
    "folk hero": {"skills": ['Animal Handling', 'Survival'], "abilities": ["str", "con", "wis"],
                  "origin_feat": "tough",
                  "items": [("Artisan's Tools", 1), ("Shovel", 1), ("Iron Pot", 1), ("Common Clothes", 1)],
                  "feature": "Rustic Hospitality"},
    "guild artisan": {"skills": ['Insight', 'Persuasion'], "abilities": ["str", "dex", "int"],
                      "origin_feat": "crafter",
                      "items": [("Artisan's Tools", 1), ("Letter of Introduction", 1), ("Traveler's Clothes", 1)],
                      "feature": "Guild Membership"},
    "hermit": {"skills": ['Medicine', 'Religion'], "abilities": ["con", "wis", "cha"],
               "origin_feat": "healer",
               "items": [("Scroll Case", 1), ("Winter Blanket", 1), ("Herbalism Kit", 1)],
               "feature": "Discovery"},
    "noble": {"skills": ['History', 'Persuasion'], "abilities": ["str", "int", "cha"],
              "origin_feat": "skilled",
              "items": [("Fine Clothes", 1), ("Signet Ring", 1), ("Scroll of Pedigree", 1)],
              "feature": "Position of Privilege"},
    "outlander": {"skills": ['Athletics', 'Survival'], "abilities": ["str", "con", "wis"],
                  "origin_feat": "tough",
                  "items": [("Staff", 1), ("Hunting Trap", 1), ("Traveler's Clothes", 1)],
                  "feature": "Wanderer"},
    "sage": {"skills": ['Arcana', 'History'], "abilities": ["con", "int", "wis"],
             "origin_feat": "magic-initiate",
             "items": [("Bottle of Ink", 1), ("Quill", 1), ("Small Knife", 1), ("Letter from a Dead Colleague", 1)],
             "feature": "Researcher"},
    "sailor": {"skills": ['Athletics', 'Perception'], "abilities": ["str", "dex", "wis"],
               "origin_feat": "tavern-brawler",
               "items": [("Belaying Pin", 1), ("Silk Rope (50 ft)", 1), ("Lucky Charm", 1)],
               "feature": "Ship's Passage"},
    "soldier": {"skills": ['Athletics', 'Intimidation'], "abilities": ["str", "dex", "con"],
                "origin_feat": "savage-attacker",
                "items": [("Insignia of Rank", 1), ("Trophy from a Fallen Enemy", 1), ("Set of Bone Dice", 1)],
                "feature": "Military Rank"},
    "urchin": {"skills": ['Sleight of Hand', 'Stealth'], "abilities": ["dex", "con", "wis"],
               "origin_feat": "lucky",
               "items": [("Small Knife", 1), ("Map of Home City", 1), ("Pet Mouse", 1)],
               "feature": "City Secrets"},
}


_LOCAL_BG_LOADED = False


def _slugify_bg_feat(name: Optional[str]) -> Optional[str]:
    """Feat name -> slug ('Magic Initiate' -> 'magic-initiate')."""
    if not name:
        return None
    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-") or None


def _ensure_local_backgrounds() -> None:
    """Merge locally-ingested 2024 backgrounds (owned_books/backgrounds.json,
    gitignored) into _BACKGROUND_KITS once. 2024 versions overwrite the 2014
    entries; new ones (Artisan, Farmer, Guard...) are added. Existing equipment
    is preserved for overlapping names."""
    global _LOCAL_BG_LOADED
    if _LOCAL_BG_LOADED:
        return
    _LOCAL_BG_LOADED = True
    path = Path(__file__).resolve().parent.parent / "owned_books" / "backgrounds.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[cc] local backgrounds load failed: {e}")
        return
    n = 0
    for b in data:
        name = (b.get("name") or "").strip()
        if not name or not b.get("skills"):
            continue
        key = name.lower()
        prev = _BACKGROUND_KITS.get(key, {})
        # The parsed feat is the Origin feat (slug); keep the descriptive
        # background feature name from the repo default when we have one.
        origin_feat = b.get("origin_feat") or (
            _slugify_bg_feat(b.get("feat")) if b.get("feat") else None)
        # Prefer curated repo equipment; else take the parsed book equipment
        # (FR 2025 backgrounds have no repo default). JSON items are [name, qty].
        items = prev.get("items") or [
            tuple(it) if isinstance(it, (list, tuple)) else (it, 1)
            for it in b.get("items", [])]
        _BACKGROUND_KITS[key] = {
            "skills": b.get("skills") or prev.get("skills") or [],
            "items": items,
            "feature": prev.get("feature") or b.get("feat"),
            "abilities": b.get("abilities") or prev.get("abilities") or [],
            "origin_feat": origin_feat or prev.get("origin_feat"),
        }
        n += 1
    print(f"[cc] merged {n} local (2024) backgrounds")


def _apply_background(char: Character, background: Optional[str],
                      *, grant_items: bool = True) -> Optional[str]:
    """Store the background, grant its kit, tag its feature. Returns feature name.

    ``grant_items=False`` on re-import sync: identity updates must not
    duplicate gear into an inventory play may have changed.
    """
    _ensure_local_backgrounds()
    if not background:
        return None
    char.background = background
    kit = _BACKGROUND_KITS.get(background.strip().lower())
    if not kit:
        return None
    if grant_items:
        for name, qty in kit["items"]:
            _add_inventory_item(char, name, quantity=qty)
    tags = list(char.tags or [])
    feature_tag = f"background-feature: {kit['feature']}"
    if feature_tag not in tags:
        tags.append(feature_tag)
    char.tags = tags
    return kit["feature"]


def _validate_spells_for_class(spell_names: List[str], char_class: Optional[str]
                               ) -> tuple[List[str], List[str], List[str]]:
    """Split imported spells into (kept, dropped_reasons, warnings) using the
    SRD rules DB: must exist, be castable at level 1 (cantrip or 1st), and be
    on the character's class list."""
    if not spell_names:
        return [], [], []
    from rules.models import Spell as _Spell
    with Session(rules_lib.engine) as s:
        seeded = s.exec(select(func.count(_Spell.id))).one()
    if not seeded:
        return list(spell_names), [], [
            "spell legality unchecked — SRD rules DB not seeded (run rules ingest)"]
    kept: List[str] = []
    drops: List[str] = []
    cls = (char_class or "").strip().lower()
    for name in spell_names:
        sp = rules_lib.get_spell(name)
        if sp is None:
            drops.append(f"{name} (not an SRD spell)")
        elif int(sp.level or 0) > 1:
            drops.append(f"{name} (level {sp.level} — beyond a level-1 caster)")
        elif cls and sp.classes and cls not in {str(c).lower() for c in sp.classes}:
            drops.append(f"{name} (not a {char_class} spell)")
        else:
            kept.append(name)
    return kept, drops, []


def _grant_starting_kit(char: Character) -> List[str]:
    """Fill an empty inventory with the class's SRD starting kit. Returns names."""
    if _inventory_items(char):
        return []
    kit = _CLASS_STARTING_KITS.get((char.char_class or "").strip().lower())
    if not kit:
        kit = [("Dagger", 1), ("Traveler's Clothes", 1), ("Explorer's Pack", 1)]
    for name, qty in kit:
        _add_inventory_item(char, name, quantity=qty)
    return [f"{name} x{qty}" if qty > 1 else name for name, qty in kit]


# Average starting gold (gp) by class when a player forgoes the equipment
# package and buys gear instead — the standard 5e per-class rolls, taken at
# average (dice formulas are game facts). Falls back to config starting_gold.
_CLASS_STARTING_GOLD: Dict[str, int] = {
    "barbarian": 50, "bard": 125, "cleric": 125, "druid": 50, "fighter": 125,
    "monk": 12, "paladin": 125, "ranger": 125, "rogue": 100, "sorcerer": 75,
    "warlock": 100, "wizard": 100, "illrigger": 125, "gunslinger": 100,
}


def _starting_gold_for(char_class: Optional[str]) -> int:
    return _CLASS_STARTING_GOLD.get(
        (char_class or "").strip().lower(), get_config().economy.starting_gold)


def _item_cost_gp(session: Session, name: str) -> Optional[float]:
    """Look up a mundane item's price (gp) from rules_item by name."""
    from rules.models import Item as _Item
    row = session.exec(
        select(_Item).where(_Item.name == name)).first()
    return row.cost_gp if row else None


def _grant_bought_items(char: Character, session: Session,
                        bought: List[Dict[str, Any]], budget: int) -> Dict[str, Any]:
    """Buy-mode gear: add purchased items, deduct cost from ``budget``. Returns a
    summary. Over-budget carts are clamped (server-side price is authoritative)."""
    spent = 0.0
    granted: List[str] = []
    for entry in bought or []:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        qty = max(1, int(entry.get("quantity", 1) or 1))
        unit = _item_cost_gp(session, name)
        if unit is None:
            continue  # unknown/unpriced item — never silently free it
        line = unit * qty
        if spent + line > budget:
            continue  # skip anything that would overspend
        spent += line
        _add_inventory_item(char, name, quantity=qty)
        granted.append(f"{name} x{qty}" if qty > 1 else name)
    char.gp = max(0, int(round(budget - spent)))
    return {"granted": granted, "spent_gp": round(spent, 2), "gp_left": char.gp}


def _grant_wondrous_item(char: Character, session: Session,
                         slug: str) -> Optional[str]:
    """Grant the chosen free common wondrous item. Returns its name if valid."""
    from rules.models import Item as _Item
    row = session.exec(
        select(_Item).where(_Item.index_slug == slug,
                            _Item.rarity == "Common")).first()
    if not row:
        return None
    _add_inventory_item(char, row.name, quantity=1,
                        extra={"magic": True, "rarity": "Common",
                               "item_type": row.item_type})
    return row.name


@app.post("/register_character")
async def register_character(req: RegisterCharacterRequest):
    """Register a new character for a discord user.

    Basic validation applied (level range and unique name per owner). Approved flag can be used by admins later.
    """
    # Basic validation
    if not req.name or not req.discord_user_id:
        raise HTTPException(status_code=400, detail="Missing required fields: name and discord_user_id.")

    # Validate allowed source values
    if req.source and req.source not in ("avrae", "guided", "manual", "ddb"):
        raise HTTPException(status_code=400, detail="Invalid source. Allowed: avrae, guided, manual, ddb.")

    # Level validation: characters are always CREATED at level 1. Advancement is
    # tracked in-system via the /level_up flow (SRD-based), so any import or manual
    # entry must start at level 1.
    if req.level != 1:
        raise HTTPException(
            status_code=400,
            detail="Characters must be created at level 1. Use /level_up to advance.",
        )

    # Validate stats if present. Characters are always created at level 1:
    # legal generation (array / point buy / 4d6kh3) plus racial bonuses can
    # never produce a score above 20 (or below 3).
    if req.stats:
        for k, v in req.stats.items():
            if not isinstance(v, int) or v < 3 or v > 20:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid stat value for {k}: {v} (level-1 scores must be 3-20).")

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

        # Validate every chosen feat's prerequisites against the FINAL stats
        # (base + background boosts are already applied client-side). Unmet,
        # verifiable prerequisites are rejected — the CC can't hand out a feat
        # the character doesn't qualify for.
        if req.feats:
            from rules.models import Feat as _Feat
            fstats = req.stats or {}
            for ft in req.feats:
                frow = session.exec(select(_Feat).where(
                    _Feat.index_slug == ft)).first()
                if frow is None:
                    continue  # unknown slug (e.g. owned-ingest feat) — allow
                met, why = _feat_prereq_met(
                    frow.prerequisite, frow.min_level, fstats,
                    req.char_class, req.level)
                if not met:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Feat '{frow.name}' — prerequisite not met ({why}).")

        # Skill proficiencies + feats from the CC wizard, tagged onto the sheet.
        if req.skills or req.feats:
            tags = list(char.tags or [])
            for sk in (req.skills or []):
                t = f"skill: {sk}"
                if t not in tags:
                    tags.append(t)
            for ft in (req.feats or []):
                t = f"feat: {ft}"
                if t not in tags:
                    tags.append(t)
            char.tags = tags

        # Starting gear: either the class/background package, or buy-your-own.
        buy_mode = (req.gear_mode or "kit").strip().lower() == "buy"
        purchase = None
        if buy_mode:
            # Skip the class kit + background gear; grant starting gold to spend.
            budget = _starting_gold_for(req.char_class)
            purchase = _grant_bought_items(char, session, req.bought_items or [], budget)
            kit_granted = purchase["granted"] or None
            bg_feature = _apply_background(char, req.background, grant_items=False)
        else:
            kit_granted = _grant_starting_kit(char)
            bg_feature = _apply_background(char, req.background)

        # A free common wondrous item (independent of the gear choice).
        wondrous_granted = None
        if req.wondrous_item:
            wondrous_granted = _grant_wondrous_item(char, session, req.wondrous_item)

        session.add(char)
        session.commit()
        session.refresh(char)

    return {"status": "ok", "message": "Character registered", "character_id": char.id,
            "starting_kit": kit_granted or None,
            "background_feature": bg_feature,
            "purchase": purchase,
            "wondrous_item": wondrous_granted}


# ----- Deterministic character creation (the CC wizard's data source) -----

_ABILITY_WORDS = {
    "strength": "str", "str": "str", "dexterity": "dex", "dex": "dex",
    "constitution": "con", "con": "con", "intelligence": "int", "int": "int",
    "wisdom": "wis", "wis": "wis", "charisma": "cha", "cha": "cha",
}
_ABILITY_FULL = {"str": "strength", "dex": "dexterity", "con": "constitution",
                 "int": "intelligence", "wis": "wisdom", "cha": "charisma"}


def _feat_prereq_met(prerequisite: Optional[str], min_level: int,
                     stats: Dict[str, int], char_class: Optional[str],
                     level: int, existing_feats: Optional[list] = None
                     ) -> tuple[bool, Optional[str]]:
    """Check whether a character meets a feat's prerequisites.

    Verifiable prereqs: level minimum, ability-score minimums ('Strength 13'),
    and 'Spellcasting' (a spellcasting class). Unrecognized prereq clauses are
    NOT blocked (best-effort — we don't silently forbid a legal pick). Returns
    (met, reason_if_not)."""
    if level < int(min_level or 1):
        return False, f"requires level {min_level}"
    if not prerequisite:
        return True, None

    def _score(code: str) -> int:
        full = _ABILITY_FULL.get(code, code)
        for k in (code, code.upper(), full, full.capitalize(), full.upper()):
            if stats.get(k) is not None:
                return int(stats[k])
        return 0

    for clause in re.split(r"[;,]| and ", prerequisite):
        c = clause.strip().lower()
        if not c:
            continue
        # ability minimum, e.g. "strength 13" / "dex 13 or higher"
        m = re.search(r"(strength|dexterity|constitution|intelligence|wisdom|"
                      r"charisma|str|dex|con|int|wis|cha)\D*(\d+)", c)
        if m:
            code = _ABILITY_WORDS.get(m.group(1))
            need = int(m.group(2))
            if code and _score(code) < need:
                return False, f"needs {m.group(1).title()} {need}+"
            continue
        if "spellcast" in c or "cast at least one spell" in c or "cast a spell" in c:
            if (char_class or "").strip().lower() not in _CASTING_ABILITY:
                return False, "needs a spellcasting class"
            continue
        # Unparseable clause (e.g. an armor proficiency): don't block.
    return True, None


@app.get("/cc/options")
def cc_options():
    """Everything the deterministic CC wizard needs: races, classes (with
    level-1 skill choices), backgrounds, and the legal ability-score methods.
    All values come from the rules DB / server constants — never an LLM."""
    _ensure_local_backgrounds()
    from rules.models import (Race as _Race, DndClass as _Cls, Feat as _Feat,
                              Item as _Item)
    with Session(rules_lib.engine) as s:
        races = s.exec(select(_Race)).all()
        classes = s.exec(select(_Cls)).all()
        # The whole feat pool (origin + general). The UI gates by context:
        # backgrounds/Human draw from origin feats; Custom Lineage ("any")
        # draws from all, with prerequisites enforced client- and server-side.
        try:
            feats = s.exec(select(_Feat).order_by(_Feat.category, _Feat.name)).all()
        except Exception:
            feats = []
        # Free common magic-item pick — ALL common items (wondrous, wands,
        # staves, common armor/weapons, potions...), not just wondrous. Level-1
        # balance is held by the Common-rarity gate. Empty degrades gracefully.
        try:
            common_items = s.exec(select(_Item).where(
                _Item.rarity == "Common").order_by(_Item.name)).all()
        except Exception:
            common_items = []
        # Buyable mundane gear for the "buy your own" path.
        try:
            buyable = s.exec(select(_Item).where(
                _Item.category != "magic-item",
                _Item.cost_gp.is_not(None)).order_by(_Item.name)).all()
        except Exception:
            buyable = []
    return {
        "races": [{
            "slug": r.index_slug, "name": r.name,
            "ability_bonuses": r.ability_bonuses or {},
            "choose_bonus": r.choose_bonus or [],
            "speed": r.speed, "size": r.size, "darkvision": bool(r.darkvision),
            "languages": r.languages, "traits": r.traits or [],
            # 2024 additions: flavor lineages (no ASI) and any species-granted
            # feat choice ("origin" | "any").
            "lineages": getattr(r, "lineages", None) or [],
            "lineage_label": getattr(r, "lineage_label", None),
            "feat_choice": getattr(r, "feat_choice", None),
        } for r in races],
        "classes": [{
            "slug": c.index_slug, "name": c.name, "hit_die": c.hit_die,
            "primary_ability": c.primary_ability,
            "spellcasting_ability": c.spellcasting_ability,
            "saving_throws": c.saving_throws or [],
            "skill_choices_n": c.skill_choices_n or 2,
            "skill_options": c.skill_options or [],
        } for c in classes],
        "feats": [{
            "slug": f.index_slug, "name": f.name, "category": f.category,
            "prerequisite": f.prerequisite, "min_level": f.min_level,
            "brief": (f.benefit or "")[:160],
        } for f in feats],
        "backgrounds": [{
            "slug": bg, "name": bg.title(),
            "skills": kit.get("skills") or [],
            "feature": kit.get("feature"),
            # 2024: the background grants the ability boosts. 3-letter codes;
            # empty list (legacy backgrounds) means "any ability".
            "abilities": [(a or "")[:3].upper() for a in (kit.get("abilities") or [])],
            # 2024: the Origin feat this background grants (slug into the pool).
            "origin_feat": kit.get("origin_feat"),
        } for bg, kit in _BACKGROUND_KITS.items()],
        "ability_methods": {
            "standard_array": [15, 14, 13, 12, 10, 8],
            "point_buy": {"budget": 27, "min": 8, "max": 15,
                          "costs": {"8": 0, "9": 1, "10": 2, "11": 3,
                                     "12": 4, "13": 5, "14": 7, "15": 9}},
            "roll": {"expr": "4d6kh3", "count": 6},
        },
        "common_items": [{
            "slug": w.index_slug, "name": w.name,
            "item_type": w.item_type,
            "attunement": bool(w.requires_attunement),
            "brief": (w.desc or "")[:160],
        } for w in common_items],
        "buyable_items": [{
            "slug": b.index_slug, "name": b.name, "category": b.category,
            "cost_gp": b.cost_gp,
        } for b in buyable],
        "starting_gold": {
            "by_class": _CLASS_STARTING_GOLD,
            "default": get_config().economy.starting_gold,
        },
    }


@app.post("/cc/roll_abilities")
def cc_roll_abilities():
    """Six real 4d6-drop-lowest rolls from the internal dice engine."""
    rolls = [dice_roll("4d6kh3") for _ in range(6)]
    return {"rolls": [{"total": r.total, "kept": r.rolls, "dropped": r.dropped,
                       "detail": r.detail} for r in rolls]}


class DDBImportRequest(BaseModel):
    discord_user_id: str
    url: str
    home_region: Optional[str] = None


def _import_ddb_portrait(parsed: dict, character_name: str, report: dict) -> None:
    """Fetch the DDB avatar and store it as the character's portrait. Best-effort."""
    url = parsed.get("avatar_url")
    if not url:
        return
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200 or not (1024 < len(r.content) <= 8_000_000):
            return
        image_store.set_portrait_from_bytes(
            character_name, r.content,
            caption=f"{character_name} (D&D Beyond portrait)")
        report.setdefault("warnings", []).append("portrait imported from D&D Beyond")
    except Exception as e:
        print(f"[ddb portrait] fetch/store failed: {e}")


@app.post("/import_ddb")
def import_ddb_endpoint(req: DDBImportRequest):
    """Import a PUBLIC D&D Beyond character sheet (Avrae-free path).

    Fetches + parses the DDB JSON, enforces world rules (level 1 start,
    stat caps, no magic/homebrew gear), registers the character, fills the
    inventory (kept mundane gear, else the class starting kit), and returns a
    validation report the AI DM uses to follow up on missing pieces and
    mention dropped extras.
    """
    import ddb_import
    try:
        parsed, report = ddb_import.import_from_url(req.url)
    except ddb_import.DDBImportError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Import failed: {e}")

    if not parsed.get("name"):
        raise HTTPException(status_code=400,
                            detail="The sheet has no character name; set one on "
                                   "D&D Beyond and re-import.")

    # Spell legality against the SRD rules DB (exists, level-1 castable, on
    # the class list). Dropped spells are itemized for the DM to mention.
    spells_kept, spell_drops, spell_warnings = _validate_spells_for_class(
        parsed.get("spells") or [], parsed.get("char_class"))
    report["dropped"].extend(spell_drops)
    report["warnings"].extend(spell_warnings)
    parsed["spells"] = spells_kept

    import ddb_import as _ddb
    new_ddb_id = _ddb.extract_character_id(req.url)

    with Session(engine) as session:
        # Re-import as SYNC: same DDB character id, or same name for this user.
        existing = None
        if new_ddb_id:
            for c in session.exec(select(Character).where(
                    Character.discord_user_id == req.discord_user_id)).all():
                if c.ddb_url and _ddb.extract_character_id(c.ddb_url) == new_ddb_id:
                    existing = c
                    break
        if existing is None:
            existing = session.exec(select(Character).where(
                Character.discord_user_id == req.discord_user_id,
                Character.name == parsed["name"])).first()

        if existing is not None:
            if existing.level > 1:
                raise HTTPException(
                    status_code=400,
                    detail=f"{existing.name} has grown beyond the sheet (level "
                           f"{existing.level}) — re-import can no longer overwrite "
                           "them. Their story lives here now.")
            # Identity sync: race/class/stats/spells refresh; inventory, coin,
            # and anything earned in play are untouched.
            existing.name = parsed["name"]
            existing.race = parsed.get("race")
            existing.char_class = parsed.get("char_class")
            existing.stats = parsed.get("stats")
            existing.spells = parsed.get("spells") or None
            existing.ddb_url = req.url
            _apply_background(existing, parsed.get("background"), grant_items=False)
            cls_row = _get_class_row(session, parsed.get("char_class"))
            hit_die = f"d{cls_row.hit_die}" if cls_row and cls_row.hit_die else "d8"
            con_mod = ability_modifier((parsed.get("stats") or {}).get("constitution"))
            existing.hit_die = hit_die
            existing.max_hp = max(1, int(hit_die[1:]) + con_mod)
            existing.current_hp = existing.max_hp
            existing.updated_at = _utcnow()
            session.add(existing)
            session.commit()
            session.refresh(existing)
            report.setdefault("warnings", []).append(
                "existing character synced from the sheet — identity refreshed, "
                "inventory and progress untouched")
            _import_ddb_portrait(parsed, existing.name, report)
            return {"status": "ok", "character_id": existing.id,
                    "name": existing.name, "synced": True, "report": report}

        cls_row = _get_class_row(session, parsed.get("char_class"))
        hit_die = f"d{cls_row.hit_die}" if cls_row and cls_row.hit_die else "d8"
        con_mod = ability_modifier((parsed.get("stats") or {}).get("constitution"))
        start_hp = max(1, int(hit_die[1:]) + con_mod)
        surv = get_config().survival

        char = Character(
            discord_user_id=req.discord_user_id,
            name=parsed["name"],
            race=parsed.get("race"),
            char_class=parsed.get("char_class"),
            subclass=None,                       # chosen in-system at subclass level
            level=1,
            gp=get_config().economy.starting_gold,
            stats=parsed.get("stats"),
            spells=parsed.get("spells") or None,
            ddb_url=req.url,
            approved=True,
            home_region=req.home_region,
            max_hp=start_hp, current_hp=start_hp,
            hit_die=hit_die, hit_dice_total=1, hit_dice_remaining=1,
            rations=surv.starting_rations, water=surv.starting_water,
        )
        # Inventory: mundane gear that survived validation, else the class kit.
        for it in parsed.get("items") or []:
            _add_inventory_item(char, it["name"], quantity=it.get("quantity", 1))
        kit = _grant_starting_kit(char)
        if kit:
            report.setdefault("warnings", []).append(
                "issued class starting kit: " + ", ".join(kit))

        # Background: stored + its equipment + its feature tag.
        feature = _apply_background(char, parsed.get("background"))
        if feature:
            report.setdefault("warnings", []).append(
                f"background {parsed['background']} applied "
                f"(feature: {feature}, kit added)")

        session.add(char)
        session.commit()
        session.refresh(char)
        char_id = char.id

    # DDB avatar becomes the character portrait (best-effort, post-commit).
    _import_ddb_portrait(parsed, parsed["name"], report)

    return {"status": "ok", "character_id": char_id,
            "name": parsed["name"], "report": report}


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
        char.updated_at = _utcnow()
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
        result = _progression(session, char, target_subclass=req.subclass, apply=True)
        # If the level-up was successfully applied, clear the pending flag.
        if result.get("applied"):
            char.pending_level_up = False
            session.add(char)
            session.commit()
        return result


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


# ===================== NPCs & party companions =============================
#
# Design: MOST NPCs are lightweight world-graph entities (a name, role,
# disposition, and a running trust toward the party) — cheap to spin up and
# plenty for talking, trading, and scheming. An NPC is only promoted to a FULL
# Character sheet (class/subclass/level + level-appropriate features, HP, Hit
# Dice) when they JOIN THE PARTY as an adventuring companion, so they can fight,
# level up, and be run in combat. The player chooses whether they or the DM run
# that companion.

# A neutral default ability array for a promoted NPC when none is supplied.
_DEFAULT_NPC_STATS = {
    "strength": 14, "dexterity": 13, "constitution": 14,
    "intelligence": 10, "wisdom": 12, "charisma": 10,
}


def _pc_entity_slug(char: Character) -> str:
    """The world-graph PC entity slug for a backend character.

    Identity-aware: resolves via (owner, name) since slugs are unique per
    entity and names may repeat; falls back to the plain name slug for
    pre-identity worlds.
    """
    try:
        pc = world.find_pc(char.discord_user_id, char.name)
        if pc is not None:
            return pc.slug
    except Exception:
        pass
    return slugify(char.name)


def _subclass_features_up_to(session: Session, cls: DndClass, subclass_name: Optional[str], level: int) -> list:
    """Subclass features gained at or below ``level`` (SRD data only)."""
    if not subclass_name:
        return []
    sc = _find_subclass(session, cls, subclass_name)
    if not sc or not sc.features:
        return []
    out = []
    for f in sc.features:
        if isinstance(f, dict) and int(f.get("level", 0) or 0) <= level:
            out.append({"level": f.get("level"), "name": f.get("name"), "summary": f.get("summary")})
    return out


def _build_leveled_npc_character(
    session: Session,
    *,
    name: str,
    char_class: str,
    level: int,
    subclass: Optional[str] = None,
    stats: Optional[dict] = None,
    race: Optional[str] = None,
    controller: str = "dm",
) -> Character:
    """Create a FULL NPC character sheet at a target level.

    HP/Hit Dice are derived from the class hit die + Constitution across levels,
    and the subclass is applied when the class is high enough to choose one. This
    reuses the same rules the PCs do so a companion is a real, levelable character.
    """
    cls = _get_class_row(session, char_class)
    if cls is None:
        raise HTTPException(status_code=400, detail=f"Unknown class for NPC: {char_class!r}")
    stats = stats or dict(_DEFAULT_NPC_STATS)
    level = max(1, int(level))

    die_faces = int(cls.hit_die or 8)
    con_mod = ability_modifier(stats.get("constitution") or stats.get("con") or stats.get("CON"))
    # L1 = max die + CON; each later level adds the die's average (rounded up) + CON.
    avg_per_level = die_faces // 2 + 1
    max_hp = max(1, die_faces + con_mod)
    for _ in range(2, level + 1):
        max_hp += max(1, avg_per_level + con_mod)

    # Only apply a subclass once the class is high enough to have chosen it.
    applied_subclass = None
    if subclass and level >= (cls.subclass_level or 3):
        sc = _find_subclass(session, cls, subclass)
        applied_subclass = sc.name if sc else subclass

    char = Character(
        name=name,
        race=race,
        char_class=cls.name,
        subclass=applied_subclass,
        level=level,
        is_npc=True,
        controller=controller if controller in ("player", "dm") else "dm",
        stats=stats,
        max_hp=max_hp,
        current_hp=max_hp,
        hit_die=f"d{die_faces}",
        hit_dice_total=level,
        hit_dice_remaining=level,
        approved=True,
    )
    session.add(char)
    session.commit()
    session.refresh(char)
    return char


def _npc_character_summary(session: Session, character_id: Optional[int]) -> Optional[dict]:
    """Compact mechanical sheet for a promoted NPC (class/subclass/level/HP/features)."""
    if not character_id:
        return None
    char = session.get(Character, character_id)
    if not char:
        return None
    cls = _get_class_row(session, char.char_class)
    features = _subclass_features_up_to(session, cls, char.subclass, char.level) if cls else []
    return {
        "character_id": char.id,
        "class": char.char_class,
        "subclass": char.subclass,
        "level": char.level,
        "hp": {"current": char.current_hp, "max": char.max_hp},
        "hit_dice": {"remaining": char.hit_dice_remaining, "total": char.hit_dice_total, "die": char.hit_die},
        "controller": char.controller,
        "subclass_features": features,
        "stats": char.stats or {},
    }


def _npc_dossier(session: Session, ent, pc_char: Optional[Character] = None) -> dict:
    """Full NPC record: narrative attributes + trust (toward a PC) + any sheet."""
    attrs = ent.attributes or {}
    trust = None
    if pc_char is not None:
        trust = world.get_trust(ent.slug, _pc_entity_slug(pc_char))
    return {
        "slug": ent.slug,
        "name": ent.name,
        "status": ent.status,
        "description": attrs.get(NpcAttr.DESCRIPTION),
        "race": attrs.get(NpcAttr.RACE),
        "role": attrs.get(NpcAttr.ROLE),
        "disposition": attrs.get(NpcAttr.DISPOSITION),
        "attitude": attrs.get(NpcAttr.ATTITUDE),
        "voice": attrs.get(NpcAttr.VOICE),
        "goals": attrs.get(NpcAttr.GOALS),
        "trust": trust,
        "is_full_character": bool(ent.character_id),
        "sheet": _npc_character_summary(session, ent.character_id),
    }


class NpcCreateRequest(BaseModel):
    name: str
    race: Optional[str] = None
    role: Optional[str] = None
    disposition: Optional[str] = None
    attitude: Optional[str] = None
    voice: Optional[str] = None
    goals: Optional[str] = None
    description: Optional[str] = None
    location: Optional[str] = None      # place slug/name to place the NPC at
    tags: Optional[List[str]] = None
    # Optional: seed an initial trust toward a specific PC.
    character_id: Optional[int] = None
    initial_trust: Optional[int] = None


@app.post("/npc/create")
async def npc_create(req: NpcCreateRequest):
    """Quickly spin up a LIGHTWEIGHT narrative NPC in the world graph.

    No stat sheet is created — that only happens if/when the NPC joins a party.
    """
    attrs = {}
    if req.description:
        attrs[NpcAttr.DESCRIPTION] = req.description
    if req.race:
        attrs[NpcAttr.RACE] = req.race
    if req.role:
        attrs[NpcAttr.ROLE] = req.role
    if req.disposition:
        attrs[NpcAttr.DISPOSITION] = req.disposition
    if req.attitude:
        attrs[NpcAttr.ATTITUDE] = req.attitude
    if req.voice:
        attrs[NpcAttr.VOICE] = req.voice
    if req.goals:
        attrs[NpcAttr.GOALS] = req.goals

    ent = world.upsert_entity(
        req.name, EntityType.NPC, attributes=attrs, tags=req.tags or ["npc"],
    )
    if req.location:
        try:
            world.move_entity(ent.slug, req.location)
        except Exception as e:
            print(f"[npc] could not place '{req.name}' at '{req.location}': {e}")

    with Session(engine) as session:
        pc_char = session.get(Character, req.character_id) if req.character_id else None
        if pc_char is not None and req.initial_trust is not None:
            world.adjust_trust(ent.slug, _pc_entity_slug(pc_char), int(req.initial_trust),
                               reason="first impression")
        # Re-read the entity so mirrored attitude (if trust set) is reflected.
        return {"status": "ok", "npc": _npc_dossier(session, world.get_entity(ent.slug), pc_char)}


@app.get("/npc/{slug}")
async def npc_get(slug: str, character_id: Optional[int] = None):
    ent = world.get_entity(slug)
    if not ent or ent.type != EntityType.NPC:
        raise HTTPException(status_code=404, detail=f"NPC '{slug}' not found.")
    with Session(engine) as session:
        pc_char = session.get(Character, character_id) if character_id else None
        return _npc_dossier(session, ent, pc_char)


class NpcTrustRequest(BaseModel):
    npc: str                    # slug or name
    character_id: int
    delta: int
    reason: Optional[str] = None


@app.post("/npc/trust")
async def npc_trust(req: NpcTrustRequest):
    """Nudge an NPC's running trust toward a PC (shifts their attitude band)."""
    with Session(engine) as session:
        pc_char = session.get(Character, req.character_id)
        if not pc_char:
            raise HTTPException(status_code=404, detail="Character not found.")
        ent = world.get_entity(req.npc)
        if not ent or ent.type != EntityType.NPC:
            raise HTTPException(status_code=404, detail=f"NPC '{req.npc}' not found.")
        result = world.adjust_trust(ent.slug, _pc_entity_slug(pc_char), int(req.delta),
                                    reason=req.reason or "")
        if result is None:
            raise HTTPException(status_code=400, detail="Could not adjust trust.")
        try:
            world.add_event(
                f"{ent.name}'s trust toward {pc_char.name} shifts to {result['attitude']}"
                + (f" ({req.reason})" if req.reason else ""),
                involved=[ent.slug],
            )
        except Exception:
            pass
        return {"status": "ok", "npc": ent.slug, **result}


class TableMaturityRequest(BaseModel):
    session_id: str
    enabled: bool
    confirm_adult: bool = False   # explicit adult opt-in; required to enable


@app.post("/table/maturity")
async def table_maturity(req: TableMaturityRequest):
    """Toggle a table's opt-in to mature (NSFW-capable) scene art.

    Per-table and OFF by default — a shared Discord surface, so mature imagery
    is never global. Enabling requires ``confirm_adult`` (an explicit adult
    opt-in). This is a no-op for actual rendering unless a mature checkpoint is
    also configured (``ImageryConfig.checkpoint_mature``)."""
    if req.enabled and not req.confirm_adult:
        raise HTTPException(
            status_code=400,
            detail="Enabling mature content requires an explicit adult opt-in (confirm_adult=true).",
        )
    val = _set_table_mature(req.session_id, req.enabled)
    model_ready = bool(getattr(get_config().imagery, "checkpoint_mature", None))
    return {
        "status": "ok",
        "session_id": req.session_id,
        "mature": val,
        "model_ready": model_ready,
        "note": None if model_ready else
                "Mature enabled, but no mature checkpoint is configured — renders stay safe.",
    }


class NpcActivityRequest(BaseModel):
    npc: str
    activity: str
    move_to: Optional[str] = None   # place slug/name to relocate the NPC


@app.post("/npc/activity")
async def npc_activity(req: NpcActivityRequest):
    """Log what an NPC is doing (and optionally move them) — living-world tracking."""
    ent = world.get_entity(req.npc)
    if not ent or ent.type != EntityType.NPC:
        raise HTTPException(status_code=404, detail=f"NPC '{req.npc}' not found.")
    if req.move_to:
        try:
            world.move_entity(ent.slug, req.move_to)
        except Exception as e:
            print(f"[npc] move failed: {e}")
    world.add_event(f"{ent.name}: {req.activity}", involved=[ent.slug])
    return {"status": "ok", "npc": ent.slug, "logged": req.activity}


class PartyRecruitRequest(BaseModel):
    character_id: int               # the PC whose party the NPC joins
    npc: str                        # slug or name of an existing NPC
    control: str = CompanionControl.DM   # "player" or "dm" — the PLAYER's choice
    role: Optional[str] = None
    # Promotion parameters (used only if the NPC has no character sheet yet):
    char_class: Optional[str] = None
    subclass: Optional[str] = None
    level: Optional[int] = None
    stats: Optional[dict] = None
    race: Optional[str] = None


@app.post("/party/recruit")
async def party_recruit(req: PartyRecruitRequest):
    """An NPC joins a PC's party. Promotes the NPC to a full character sheet.

    If the NPC has no sheet yet, one is built at the requested level (defaulting to
    the recruiting PC's level) with class/subclass features — so the companion can
    fight and advance. The ``control`` field records whether the player or the DM
    runs the companion.
    """
    control = req.control if req.control in CompanionControl.ALL else CompanionControl.DM
    with Session(engine) as session:
        pc_char = session.get(Character, req.character_id)
        if not pc_char:
            raise HTTPException(status_code=404, detail="Recruiting character not found.")
        ent = world.get_entity(req.npc)
        if not ent or ent.type != EntityType.NPC:
            raise HTTPException(status_code=404, detail=f"NPC '{req.npc}' not found.")

        # Promote to a full character sheet if not already one.
        if not ent.character_id:
            char_class = req.char_class
            if not char_class:
                raise HTTPException(
                    status_code=400,
                    detail="Joining the party requires a class for the companion "
                           "(NPCs are promoted to full characters when they adventure).",
                )
            npc_char = _build_leveled_npc_character(
                session,
                name=ent.name,
                char_class=char_class,
                level=req.level or pc_char.level or 1,
                subclass=req.subclass,
                stats=req.stats,
                race=req.race or (ent.attributes or {}).get(NpcAttr.RACE),
                controller=control,
            )
            world.upsert_entity(ent.name, EntityType.NPC, character_id=npc_char.id)
            ent = world.get_entity(ent.slug)
        else:
            # Already a full character; just update who controls it.
            npc_char = session.get(Character, ent.character_id)
            if npc_char:
                npc_char.controller = control
                session.add(npc_char)
                session.commit()

        world.recruit_companion(ent.slug, _pc_entity_slug(pc_char),
                                control=control, role=req.role or "")
        try:
            world.add_event(
                f"{ent.name} joins {pc_char.name}'s party ({'player' if control=='player' else 'DM'}-run).",
                involved=[ent.slug],
            )
        except Exception:
            pass
        return {
            "status": "ok",
            "companion": _npc_dossier(session, ent, pc_char),
            "control": control,
        }


class PartyControlRequest(BaseModel):
    character_id: int
    npc: str
    control: str    # "player" or "dm"


@app.post("/party/control")
async def party_control(req: PartyControlRequest):
    """Switch who runs a companion — the player or the DM."""
    if req.control not in CompanionControl.ALL:
        raise HTTPException(status_code=400, detail="control must be 'player' or 'dm'.")
    with Session(engine) as session:
        pc_char = session.get(Character, req.character_id)
        if not pc_char:
            raise HTTPException(status_code=404, detail="Character not found.")
        ent = world.get_entity(req.npc)
        if not ent or ent.type != EntityType.NPC:
            raise HTTPException(status_code=404, detail=f"NPC '{req.npc}' not found.")
        mode = world.set_companion_control(ent.slug, _pc_entity_slug(pc_char), req.control)
        if mode is None:
            raise HTTPException(status_code=400, detail=f"{ent.name} is not in the party.")
        if ent.character_id:
            npc_char = session.get(Character, ent.character_id)
            if npc_char:
                npc_char.controller = req.control
                session.add(npc_char)
                session.commit()
        return {"status": "ok", "npc": ent.slug, "control": mode}


class PartyDismissRequest(BaseModel):
    character_id: int
    npc: str
    reason: Optional[str] = None


@app.post("/party/dismiss")
async def party_dismiss(req: PartyDismissRequest):
    """An NPC leaves the party (their character sheet is kept for later)."""
    with Session(engine) as session:
        pc_char = session.get(Character, req.character_id)
        if not pc_char:
            raise HTTPException(status_code=404, detail="Character not found.")
        ent = world.get_entity(req.npc)
        if not ent or ent.type != EntityType.NPC:
            raise HTTPException(status_code=404, detail=f"NPC '{req.npc}' not found.")
        closed = world.dismiss_companion(ent.slug, _pc_entity_slug(pc_char))
        if closed:
            try:
                world.add_event(
                    f"{ent.name} parts ways with {pc_char.name}'s party"
                    + (f" ({req.reason})" if req.reason else "."),
                    involved=[ent.slug],
                )
            except Exception:
                pass
        return {"status": "ok", "npc": ent.slug, "left": bool(closed)}


@app.get("/party/{character_id}")
async def party_list(character_id: int):
    """List the PC's current companions with control mode + sheet summary."""
    with Session(engine) as session:
        pc_char = session.get(Character, character_id)
        if not pc_char:
            raise HTTPException(status_code=404, detail="Character not found.")
        companions = world.list_companions(_pc_entity_slug(pc_char))
        out = []
        for c in companions:
            ent = c["npc"]
            out.append({
                "slug": c["slug"],
                "name": c["name"],
                "control": c["control"],
                "role": c["role"],
                "trust": world.get_trust(c["slug"], _pc_entity_slug(pc_char)),
                "sheet": _npc_character_summary(session, ent.character_id),
            })
        return {"character_id": character_id, "companions": out}


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
    item: str  # slug/name/label for what is being crafted
    # Optional manual override when no priced rules item exists.
    target_cost_gp: Optional[float] = None
    # Optional display name for manual projects; falls back to ``item``.
    item_name: Optional[str] = None
    # Optional rarity hint for magic items when price is unknown.
    rarity: Optional[str] = None
    # Optional explicit tools to require; defaults are inferred from item type.
    required_tools: Optional[List[str]] = None
    is_magic: bool = False
    pay_materials: bool = True


class CraftingAdvanceRequest(BaseModel):
    project_id: int
    days: int
    advance_world: bool = True


class RecipeBookPurchaseRequest(BaseModel):
    character_id: int
    rarity: str
    quantity: int = 1


_MAGIC_ITEM_DEFAULT_COST_GP = {
    "common": 100.0,
    "uncommon": 500.0,
    "rare": 5000.0,
    "very rare": 50000.0,
    "legendary": 250000.0,
    "artifact": 500000.0,
}

# Very-rare+ items require a physical formula/tome in inventory.
_RARITY_REQUIRES_RECIPE_BOOK = {"very rare", "legendary", "artifact"}

# High-value recipe books that can be discovered or purchased in-world.
_RECIPE_BOOK_CATALOG: Dict[str, Dict[str, Any]] = {
    "very rare": {
        "name": "Archcrafter's Codex (Very Rare)",
        "cost_gp": 20000.0,
        "notes": "Required to craft very rare magic items.",
    },
    "legendary": {
        "name": "Legendwright Grimoire (Legendary)",
        "cost_gp": 100000.0,
        "notes": "Required to craft legendary magic items.",
    },
    "artifact": {
        "name": "Mythic Artificer's Testament (Artifact)",
        "cost_gp": 500000.0,
        "notes": "Required to attempt artifact-grade crafting.",
    },
}

_MAGIC_MIN_LEVEL_BY_RARITY = {
    "common": 1,
    "uncommon": 3,
    "rare": 6,
    "very rare": 11,
    "legendary": 17,
    "artifact": 20,
}

_CASTER_CLASSES = {
    "bard", "cleric", "druid", "sorcerer", "warlock", "wizard",
    "paladin", "ranger", "artificer",
}


def _normalize_rarity(rarity: Optional[str]) -> str:
    val = re.sub(r"\s+", " ", str(rarity or "").strip().lower())
    # Common variant from some sources.
    if val == "very-rare":
        return "very rare"
    return val


def _recipe_book_for_rarity(rarity: Optional[str]) -> Optional[Dict[str, Any]]:
    return _RECIPE_BOOK_CATALOG.get(_normalize_rarity(rarity))


def _default_crafting_tools(it: Optional[Item], *, is_magic: bool) -> List[str]:
    """Conservative default tools by item category + magic intent."""
    out: List[str] = []
    cat = (it.category or "").lower() if it else ""
    itype = (it.item_type or "").lower() if it else ""
    if "armor" in cat or "armor" in itype or "weapon" in cat or "weapon" in itype:
        out.append("smith's tools")
    elif "tool" in cat or "adventuring" in cat:
        out.append("artisan's tools")
    else:
        out.append("artisan's tools")
    if is_magic:
        out.append("arcane focus")
    # Stable order with no duplicates.
    seen = set()
    deduped: List[str] = []
    for t in out:
        k = _normalize_item_name(t)
        if k and k not in seen:
            seen.add(k)
            deduped.append(t)
    return deduped


def _magic_crafting_capability_error(char: Character, *, rarity: Optional[str]) -> Optional[str]:
    """Return an explanatory guardrail error if this PC can't craft this magic tier."""
    rarity_norm = _normalize_rarity(rarity)
    min_level = _MAGIC_MIN_LEVEL_BY_RARITY.get(rarity_norm, 1)
    if int(char.level or 1) < min_level:
        return f"Crafting a {rarity_norm or 'magic'} item requires level {min_level}+ (current {char.level})."

    class_name = (char.char_class or "").strip().lower()
    has_spell_list = bool(char.spells)
    is_caster = class_name in _CASTER_CLASSES or has_spell_list
    if not is_caster:
        return (
            "Magic-item crafting requires magical capability (a spellcasting class "
            "or known spells on the character sheet)."
        )
    return None


def _magic_cost_from_rarity(rarity: Optional[str]) -> Optional[float]:
    if not rarity:
        return None
    key = str(rarity).strip().lower()
    return _MAGIC_ITEM_DEFAULT_COST_GP.get(key)


def _infer_magic_rarity_from_cost(cost_gp: float) -> str:
    """Rough rarity inference used only for guardrail gating when rarity is omitted."""
    c = float(cost_gp)
    if c >= 500000:
        return "artifact"
    if c >= 250000:
        return "legendary"
    if c >= 50000:
        return "very rare"
    if c >= 5000:
        return "rare"
    if c >= 500:
        return "uncommon"
    return "common"


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
    item_slug: Optional[str] = it.index_slug if it else None
    item_name = (req.item_name or (it.name if it else req.item)).strip() or req.item
    target_cost_gp: Optional[float] = None
    cost_source = ""
    rarity = _normalize_rarity(req.rarity or (it.rarity if it else None))

    # Preferred path: use canonical rules price when available.
    if it and it.cost_gp is not None:
        target_cost_gp = float(it.cost_gp)
        cost_source = "rules_item"
    # Manual override supports crafting any custom or source-book item.
    elif req.target_cost_gp is not None:
        target_cost_gp = float(req.target_cost_gp)
        cost_source = "manual"
    # Magic fallback by rarity for entries with no explicit market price.
    elif req.is_magic:
        guessed = _magic_cost_from_rarity(rarity)
        if guessed is not None:
            target_cost_gp = guessed
            cost_source = f"rarity:{str(rarity).strip().lower()}"

    if target_cost_gp is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"No craft price found for '{req.item}'. "
                "Provide target_cost_gp (or rarity for magic items)."
            ),
        )
    if target_cost_gp <= 0:
        raise HTTPException(status_code=400, detail="target_cost_gp must be > 0")

    if req.is_magic and not rarity:
        rarity = _infer_magic_rarity_from_cost(target_cost_gp)

    with Session(engine) as session:
        char = session.get(Character, req.character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")

        # Guardrail: enforce D&D-style prerequisites before crafting begins.
        required_tools = [t for t in (req.required_tools or _default_crafting_tools(it, is_magic=req.is_magic)) if t]
        missing_tools = [t for t in required_tools if not _has_inventory_item(char, t)]
        if missing_tools:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Missing required crafting tools: "
                    + ", ".join(missing_tools)
                    + ". Add them to inventory before crafting."
                ),
            )

        if req.is_magic:
            cap_error = _magic_crafting_capability_error(char, rarity=rarity)
            if cap_error:
                raise HTTPException(status_code=400, detail=cap_error)

            # Guardrail: very rare+ crafting requires a recipe/formula book in inventory.
            if rarity in _RARITY_REQUIRES_RECIPE_BOOK:
                book = _recipe_book_for_rarity(rarity)
                if not book or not _has_inventory_item(char, str(book["name"])):
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Crafting {rarity} items requires the recipe book '"
                            f"{book['name'] if book else 'required recipe book'}' in inventory. "
                            "Find or purchase it first."
                        ),
                    )

        try:
            plan = start_crafting(target_cost_gp, is_magic=req.is_magic)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if req.pay_materials:
            mat_cp = plan["materials_cp"]
            if to_cp(_char_purse(char)) < mat_cp:
                raise HTTPException(status_code=400, detail="Not enough coin for materials.")
            _apply_cp_delta(char, -mat_cp)

        project = CraftingProject(
            character_id=char.id,
            item_slug=item_slug,
            item_name=item_name,
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
            "cost_source": cost_source,
            "rarity": rarity or None,
            "required_tools": required_tools,
            "purse": _char_purse(char),
        }


@app.get("/crafting/recipe_books")
async def crafting_recipe_books():
    """List high-tier recipe books required for very-rare+ crafting."""
    econ = get_config().economy
    out = []
    for rarity, info in _RECIPE_BOOK_CATALOG.items():
        base_cost = float(info["cost_gp"])
        final_cost = round(base_cost * econ.item_cost_multiplier, 2)
        out.append({
            "rarity": rarity,
            "name": info["name"],
            "base_cost_gp": base_cost,
            "cost_gp": final_cost,
            "notes": info.get("notes"),
        })
    return {"items": out}


@app.post("/crafting/recipe_books/buy")
async def crafting_recipe_book_buy(req: RecipeBookPurchaseRequest):
    """Purchase a rare recipe book and add it to character inventory."""
    rarity = _normalize_rarity(req.rarity)
    entry = _recipe_book_for_rarity(rarity)
    if not entry:
        raise HTTPException(status_code=404, detail=f"No recipe book catalog entry for rarity '{req.rarity}'.")
    if req.quantity < 1:
        raise HTTPException(status_code=400, detail="quantity must be >= 1")

    econ = get_config().economy
    unit_cp = round(float(entry["cost_gp"]) * econ.item_cost_multiplier * 100)
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
        _add_inventory_item(
            char,
            str(entry["name"]),
            quantity=req.quantity,
            extra={"category": "recipe-book", "rarity": rarity, "notes": entry.get("notes") or ""},
        )
        session.add(char)
        session.commit()

        return {
            "status": "ok",
            "item": entry["name"],
            "rarity": rarity,
            "quantity": req.quantity,
            "spent_gp": round(total_cp / 100, 2),
            "purse": _char_purse(char),
            "display": format_purse(_char_purse(char)),
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
        project.updated_at = _utcnow()

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
        existing = session.exec(
            select(Bastion).where(Bastion.character_id == char.id)
        ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"{char.name} already owns a bastion ('{existing.name}').",
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
        b.updated_at = _utcnow()
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
    # When supplied, require the light source (and any fuel) in inventory.
    character_id: Optional[int] = None


@app.post("/survival/light")
async def survival_light(req: LightBurnRequest):
    if req.character_id is not None:
        with Session(engine) as session:
            char = session.get(Character, req.character_id)
            if not char:
                raise HTTPException(status_code=404, detail="Character not found.")
            item_name = _LIGHT_SOURCE_ITEMS.get(
                _normalize_item_name(req.kind), req.kind
            )
            if not _has_inventory_item(char, item_name):
                raise HTTPException(
                    status_code=400,
                    detail=f"No {item_name} in inventory to light.",
                )
            # Lanterns burn oil; enforce a flask of oil unless one is clearly carried.
            if "lantern" in _normalize_item_name(item_name) and not (
                _has_inventory_item(char, "oil") or _has_inventory_item(char, "flask of oil")
            ):
                raise HTTPException(
                    status_code=400,
                    detail="A lantern needs a flask of oil to burn.",
                )
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
        # Pact Magic: a warlock's slots come back on a short rest.
        if (char.char_class or "").strip().lower() == "warlock":
            char.spell_slots_used = None
            result["pact_slots_restored"] = True
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
        char.spell_slots_used = None  # all spell slots return on a long rest
        # Conditions flagged to end on a long rest (e.g. a poison "until long rest").
        cleared = _clear_long_rest_conditions(char)
        # Level-up gate: after a long rest, check if they've earned a level.
        # If so, set pending_level_up flag; they must /level_up before adventuring.
        eligible_level = _level_for_xp(char.xp)
        if eligible_level > char.level:
            char.pending_level_up = True
            result["level_up_available"] = True
            result["eligible_level"] = eligible_level
        session.add(char)
        session.commit()
        if cleared:
            result["conditions_cleared"] = cleared
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
            dmg = req.amount
            th = int(getattr(char, "temp_hp", 0) or 0)
            if th > 0 and dmg > 0:                 # temp HP absorbs damage first
                absorbed = min(th, dmg)
                char.temp_hp = th - absorbed
                dmg -= absorbed
                if absorbed:
                    note = f"{absorbed} absorbed by temporary hit points. "
            char.current_hp -= dmg
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
                "temp_hp": char.temp_hp, "stable": char.stable, "note": note}


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
    # When a character self-cures, they must have the means (spell or item).
    character_id: Optional[int] = None
    method: Optional[str] = None


@app.post("/hazards/cure")
async def hazards_cure(req: CureRequest):
    with Session(engine) as session:
        aff = session.get(Affliction, req.affliction_id)
        if not aff:
            raise HTTPException(status_code=404, detail="Affliction not found.")
        # Guardrail: a PC ending a disease/affliction needs a real means to do it.
        if req.character_id is not None:
            char = session.get(Character, req.character_id)
            if not char:
                raise HTTPException(status_code=404, detail="Character not found.")
            method = _normalize_item_name(req.method or "")
            if not method:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Specify how it is cured (a spell like 'lesser restoration' "
                        "or an item) — a PC cannot cure an affliction with nothing."
                    ),
                )
            has_means = (
                _has_spell(char, method)
                or _has_inventory_item(char, method)
                or (method in _DISEASE_CURE_SPELLS and _has_spell(char, method))
            )
            if not has_means:
                raise HTTPException(
                    status_code=400,
                    detail=f"{char.name} lacks the means '{req.method}' (no such spell known or item carried).",
                )
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
    # When a character is supplied, enforce the tools needed to disarm a trap.
    character_id: Optional[int] = None
    required_tools: Optional[List[str]] = None


@app.post("/hazards/trap/disarm")
async def hazards_trap_disarm(req: TrapDisarmRequest):
    # Guardrail: disarming a mechanical trap needs thieves' tools in hand.
    if req.character_id is not None:
        with Session(engine) as session:
            char = session.get(Character, req.character_id)
            if not char:
                raise HTTPException(status_code=404, detail="Character not found.")
            needed = [t for t in (req.required_tools or ["thieves' tools"]) if t]
            missing = [t for t in needed if not _has_inventory_item(char, t)]
            if missing:
                raise HTTPException(
                    status_code=400,
                    detail="Missing required tools to disarm: " + ", ".join(missing) + ".",
                )
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
        rep.updated_at = _utcnow()
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


class PositionRequest(BaseModel):
    combatant_id: int
    position: Optional[str] = None  # "melee with <name>" | "near" | "far" | None clears


@app.post("/combat/position")
async def combat_set_position(req: PositionRequest):
    try:
        combatant = combat.set_position(req.combatant_id, req.position)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "combatant": combatant}


@app.get("/combat/log")
async def combat_log_read(session_id: Optional[str] = None,
                          flag: Optional[str] = None,
                          parse_source: Optional[str] = None,
                          kind: Optional[str] = None,
                          limit: int = 50):
    """Pull recent combat telemetry rows (newest first). Filter by session,
    a flag ('parse_fail'/'rejected'/'paused'/'fight_over'), parse_source, or
    kind. This is the "reproduce a bad turn / tune the extractor" endpoint."""
    limit = max(1, min(500, int(limit)))
    with Session(engine) as s:
        stmt = select(CombatLog).order_by(CombatLog.id.desc())  # type: ignore[attr-defined]
        if session_id:
            stmt = stmt.where(CombatLog.session_id == session_id)
        if parse_source:
            stmt = stmt.where(CombatLog.parse_source == parse_source)
        if kind:
            stmt = stmt.where(CombatLog.kind == kind)
        rows = list(s.exec(stmt.limit(limit if not flag else 500)).all())
    if flag:
        rows = [r for r in rows if flag in (r.flags or [])][:limit]
    return {"count": len(rows), "rows": [{
        "id": r.id, "at": r.created_at.isoformat() if r.created_at else None,
        "session_id": r.session_id, "encounter_id": r.encounter_id,
        "round": r.round, "character": r.character, "kind": r.kind,
        "parse_source": r.parse_source, "message": r.player_message,
        "raw_llm": r.raw_llm, "intents": r.intents, "events": r.events,
        "report": r.report, "flags": r.flags or []} for r in rows]}


@app.get("/combat/log/stats")
async def combat_log_stats(session_id: Optional[str] = None):
    """Durable extractor/telemetry rollup from the log table (survives
    restarts, unlike the in-memory counters). Parse-source mix + flag counts."""
    with Session(engine) as s:
        stmt = select(CombatLog)
        if session_id:
            stmt = stmt.where(CombatLog.session_id == session_id)
        rows = list(s.exec(stmt).all())
    by_source: dict[str, int] = {}
    by_flag: dict[str, int] = {}
    for r in rows:
        by_source[r.parse_source or "?"] = by_source.get(r.parse_source or "?", 0) + 1
        for f in (r.flags or []):
            by_flag[f] = by_flag.get(f, 0) + 1
    llm = by_source.get("llm", 0) + by_source.get("none", 0)
    fails = by_flag.get("parse_fail", 0)
    return {
        "total_rows": len(rows),
        "by_parse_source": by_source,
        "by_flag": by_flag,
        "extractor_llm_calls": llm,
        "extractor_parse_fail_rate": round(fails / llm, 3) if llm else 0.0,
        "session_counters": dict(_COMBAT_INTENT_STATS),
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


class CharacterConditionRequest(BaseModel):
    action: str = "add"          # add | remove | clear
    condition: str = ""          # e.g. "poisoned" (ignored for clear)
    source: Optional[str] = None
    duration: Optional[str] = None
    note: Optional[str] = None
    clears_on_long_rest: Optional[bool] = None


@app.get("/imagery/status")
def imagery_status():
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
def imagery_ensure(req: ImageEnsureRequest):
    """Generate or reuse a stored picture for (subject x context)."""
    cfg = get_config().imagery
    if not cfg.enabled:
        raise HTTPException(status_code=503, detail="Imagery is disabled in config.")
    _mark_diffusion_dirty()
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
def imagery_temp(req: ImageTempRequest):
    """Generate a throwaway image (never stored)."""
    cfg = get_config().imagery
    if not cfg.enabled or not cfg.allow_temp:
        raise HTTPException(status_code=503, detail="Temp imagery is disabled.")
    _mark_diffusion_dirty()
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


@app.get("/character/{character_id}/conditions")
async def character_conditions(character_id: int):
    """List the persistent conditions currently on the character."""
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        return {
            "character_id": char.id,
            "name": char.name,
            "conditions": _character_conditions(char),
        }


@app.post("/character/{character_id}/condition")
async def character_condition(character_id: int, req: CharacterConditionRequest):
    """Add, remove, or clear a persistent condition on the character."""
    with Session(engine) as session:
        char = session.get(Character, character_id)
        if not char:
            raise HTTPException(status_code=404, detail="Character not found.")
        op = {
            "action": req.action,
            "name": req.condition,
            "source": req.source,
            "duration": req.duration,
            "note": req.note,
            "clears_on_long_rest": req.clears_on_long_rest,
        }
        changed = _apply_condition_op(char, op)
        if changed:
            session.add(char)
            session.commit()
            session.refresh(char)
        return {
            "status": "ok",
            "changed": changed,
            "conditions": _character_conditions(char),
        }


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
    _mark_diffusion_dirty()
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


# ===========================================================================
# Discord Activity web UI — WebSocket play surface (activity-ui/)
# ===========================================================================
# The Activity is a full play surface: the player acts from the web client,
# the same /chat pipeline runs (world, rules, hooks, extraction), and the
# reply is streamed back as typed events: narration blocks interleaved with
# structured roll CARDS (captured via _ACTIVITY_ROLLS), sheet refreshes,
# scene images as data URLs, and a lexicon so the client can highlight
# meaningful names as the text types out.

import asyncio

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles


def _activity_char_id(session_id: str, user_id: str) -> Optional[int]:
    state = _load_session_state(session_id)
    member = _acting_member(state.get("meta", {}) or {}, user_id)
    char_id = (member or {}).get("character_id")
    if not char_id:
        char_id = _resolve_session_character(session_id, user_id)
    return char_id


def _activity_levelup(session_id: str, user_id: str) -> Optional[dict]:
    """When the PC has a pending level-up, the full overlay payload: report
    notes, core class features gained, and subclass options (with their
    features up to the new level) when the choice is due."""
    char_id = _activity_char_id(session_id, user_id)
    if not char_id:
        return None
    with Session(engine) as session:
        char = session.get(Character, char_id)
        if not char or not char.pending_level_up:
            return None
        char_race = char.race
        prog = _progression(session, char, target_subclass=None, apply=False)
    new_level = prog["next_level"]
    race_feats: list = []
    try:
        from rules.query import RulesLibrary
        _lib = RulesLibrary(engine=engine)
        class_feats = _lib.class_features_at(prog["class"], new_level)
        race_feats = _lib.race_features_at(char_race or "", new_level)
        options = []
        for o in (prog.get("subclass_options") or []):
            sc = _lib.get_subclass(o["slug"])
            feats = [f for f in ((sc.features if sc else None) or [])
                     if f.get("level", 99) <= new_level]
            options.append({**o, "features": feats})
    except Exception as e:
        print(f"[activity] level-up enrichment failed: {e}")
        class_feats, options = [], prog.get("subclass_options") or []
    return {
        "character_id": char_id,
        "current_level": prog["current_level"],
        "next_level": new_level,
        "class": prog["class"],
        "subclass": prog.get("subclass"),
        "subclass_required": prog.get("subclass_required"),
        "subclass_label": prog.get("subclass_label"),
        "notes": (prog.get("report") or {}).get("notes") or [],
        "class_features": class_feats,
        "race_features": race_feats,
        "subclass_options": options,
    }


# Standard single-class 5e spell-slot progressions (slot counts for spell
# levels 1..9 at each character level). Used counts aren't tracked in the
# narrative game, so the Activity shows total capacity (used=0).
_FULL_CASTER_SLOTS = {
    1: [2], 2: [3], 3: [4, 2], 4: [4, 3], 5: [4, 3, 2], 6: [4, 3, 3],
    7: [4, 3, 3, 1], 8: [4, 3, 3, 2], 9: [4, 3, 3, 3, 1], 10: [4, 3, 3, 3, 2],
    11: [4, 3, 3, 3, 2, 1], 12: [4, 3, 3, 3, 2, 1], 13: [4, 3, 3, 3, 2, 1, 1],
    14: [4, 3, 3, 3, 2, 1, 1], 15: [4, 3, 3, 3, 2, 1, 1, 1], 16: [4, 3, 3, 3, 2, 1, 1, 1],
    17: [4, 3, 3, 3, 2, 1, 1, 1, 1], 18: [4, 3, 3, 3, 3, 1, 1, 1, 1],
    19: [4, 3, 3, 3, 3, 2, 1, 1, 1], 20: [4, 3, 3, 3, 3, 2, 2, 1, 1],
}
_HALF_CASTER_SLOTS = {
    1: [], 2: [2], 3: [3], 4: [3], 5: [4, 2], 6: [4, 2], 7: [4, 3], 8: [4, 3],
    9: [4, 3, 2], 10: [4, 3, 2], 11: [4, 3, 3], 12: [4, 3, 3], 13: [4, 3, 3, 1],
    14: [4, 3, 3, 1], 15: [4, 3, 3, 2], 16: [4, 3, 3, 2], 17: [4, 3, 3, 3, 1],
    18: [4, 3, 3, 3, 1], 19: [4, 3, 3, 3, 2], 20: [4, 3, 3, 3, 2],
}
# Warlock Pact Magic: (slot spell-level, slot count) by character level.
_PACT_SLOTS = {
    1: (1, 1), 2: (1, 2), 3: (2, 2), 4: (2, 2), 5: (3, 2), 6: (3, 2), 7: (4, 2),
    8: (4, 2), 9: (5, 2), 10: (5, 2), 11: (5, 3), 12: (5, 3), 13: (5, 3),
    14: (5, 3), 15: (5, 3), 16: (5, 3), 17: (5, 4), 18: (5, 4), 19: (5, 4), 20: (5, 4),
}
_FULL_CASTERS = {"bard", "cleric", "druid", "sorcerer", "wizard"}
_HALF_CASTERS = {"paladin", "ranger", "artificer"}


def _spell_slots_for(char_class: Optional[str], level: int,
                     used: Optional[dict] = None) -> list[dict]:
    cls = (char_class or "").strip().lower()
    lvl = max(1, min(20, int(level or 1)))
    used_map = {int(k): int(v) for k, v in (used or {}).items()}
    if cls in _FULL_CASTERS:
        counts = _FULL_CASTER_SLOTS.get(lvl, [])
    elif cls in _HALF_CASTERS:
        counts = _HALF_CASTER_SLOTS.get(lvl, [])
    elif cls == "warlock":
        slvl, n = _PACT_SLOTS.get(lvl, (1, 1))
        return [{"level": slvl, "total": n,
                 "used": min(n, used_map.get(slvl, 0))}]
    else:
        return []
    return [{"level": i, "total": c, "used": min(c, used_map.get(i, 0))}
            for i, c in enumerate(counts, start=1) if c > 0]


def _class_resources_for(char: Character) -> list[dict]:
    """Curated per-class pip resources (Bardic Inspiration, Ki, Rage…).
    Not exhaustive; classes without an entry simply show none."""
    cls = (char.char_class or "").strip().lower()
    lvl = int(char.level or 1)
    stats = char.stats or {}

    def amod(stat: str) -> int:
        for key in (stat, stat[:3], stat.capitalize(), stat[:3].capitalize(), stat.upper()):
            if key in stats and stats[key] is not None:
                return (int(stats[key]) - 10) // 2
        return 0

    if cls == "bard":
        die = "d6" if lvl < 5 else "d8" if lvl < 10 else "d10" if lvl < 15 else "d12"
        return [{"name": "Bardic Insp.", "total": max(1, amod("charisma")), "used": 0, "die": die}]
    if cls == "monk":
        return [{"name": "Ki", "total": lvl, "used": 0}]
    if cls == "barbarian":
        rage = 2 if lvl < 3 else 3 if lvl < 6 else 4 if lvl < 12 else 5 if lvl < 17 else 6
        return [{"name": "Rage", "total": rage, "used": 0}]
    if cls == "sorcerer" and lvl >= 2:
        return [{"name": "Sorcery Pts", "total": lvl, "used": 0}]
    return []


def _feat_kind(name: str) -> str:
    n = (name or "").lower()
    if any(w in n for w in ("breath", "flame", "fire", "ember", "dragon")):
        return "fire"
    if any(w in n for w in ("spell", "arcane", "magic", "cantrip", "incant", "eldritch", "invocation")):
        return "arcane"
    if any(w in n for w in ("attack", "weapon", "fighting", "martial", "rage", "strike", "maneuver", "ambush")):
        return "martial"
    return "other"


def _sheet_features(session: Session, char: Character) -> list[dict]:
    """Class + subclass + species features up to the PC's level, as compact
    {name, note, kind} rows for the Activity's Features tab."""
    out: list[dict] = []
    try:
        from rules.query import RulesLibrary
        _lib = RulesLibrary(engine=engine)
        cfeats = _lib.class_features_up_to(char.char_class or "", char.level)
        cls_row = _get_class_row(session, char.char_class)
        sfeats = _subclass_features_up_to(session, cls_row, char.subclass, char.level) if cls_row else []
        rfeats = _lib.race_features_up_to(char.race or "", char.level)
        seen: set[str] = set()
        for f in [*cfeats, *sfeats, *rfeats]:
            name = (f.get("name") or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            note = (f.get("summary") or "").strip()
            out.append({"name": name, "note": (note[:90] or None), "kind": _feat_kind(name)})
    except Exception as e:
        print(f"[activity features] {e}")
    return out[:12]


def _activity_portrait_url(character_name: str) -> Optional[str]:
    try:
        portrait = image_store.get_portrait(character_name)
        if portrait is None:
            return None
        pl = portrait.payload()
        return f"data:{pl.get('mime', 'image/webp')};base64,{pl['b64']}"
    except Exception:
        return None


_SPELLBOOK_HINTS = ("spellbook", "spell book", "book of spells", "tome of spells")


def _item_is_spellbook(name: Optional[str], item_type: Optional[str]) -> bool:
    n = (name or "").lower()
    t = (item_type or "").lower()
    return "spellbook" in t or any(h in n for h in _SPELLBOOK_HINTS)


def _brief_of(desc: Optional[str]) -> Optional[str]:
    """A one-line hover brief from a full item description."""
    if not desc:
        return None
    first = re.split(r"(?<=[.!?])\s", desc.strip().replace("\n", " "), maxsplit=1)[0]
    return (first[:150].rstrip() or None)


def _detect_charges(desc: Optional[str]) -> Optional[int]:
    """Max charges parsed from an item's description text, if any."""
    if not desc:
        return None
    m = re.search(r"(\d+)\s+charges", desc.lower())
    return int(m.group(1)) if m else None


def _attuned_count(char: Character) -> int:
    return sum(1 for it in _inventory_items(char) if it.get("attuned"))


_WEARABLE_TYPES = {"ring", "martial", "simple", "light armor", "medium", "heavy",
                   "shield", "wondrous item", "wondrous items"}


def _item_family(row, name: str) -> Optional[str]:
    """Coarse interactive family for an item: spellbook / consumable / charged /
    container / wearable, or None for a plain item."""
    n = (name or "").lower()
    it = (getattr(row, "item_type", "") or "").lower() if row else ""
    cat = (getattr(row, "category", "") or "").lower() if row else ""
    desc = getattr(row, "desc", "") if row else ""
    if "spellbook" in it or "spellbook" in n:
        return "spellbook"
    if "potion" in it or "scroll" in it or "ammunition" in it or "ration" in n:
        return "consumable"
    if any(k in it for k in ("wand", "staff", "rod")) or _detect_charges(desc):
        return "charged"
    if any(k in n for k in ("bag of holding", "haversack", "portable hole", "bag of tricks")):
        return "container"
    if cat in ("armor", "weapon") or it in _WEARABLE_TYPES or "armor" in it or "weapon" in it:
        return "wearable"
    return None


def _item_caps(row, name: str, inv_item: Optional[dict], char: Character,
               family: Optional[str]) -> dict:
    """Context-aware actions + live state (charges/equipped/attuned) for the
    inspector. Actions are the quick buttons; state renders as pips/badges."""
    caps: dict = {}
    actions: list[dict] = []
    inv_item = inv_item or {}
    desc = getattr(row, "desc", "") if row else ""
    needs_attune = bool(getattr(row, "requires_attunement", False)) if row else False
    equipped = bool(inv_item.get("equipped"))
    attuned = bool(inv_item.get("attuned"))

    charges_max = _detect_charges(desc)
    if charges_max:
        cur = inv_item.get("charges_current")
        cur = charges_max if cur is None else int(cur)
        caps["charges"] = {"current": max(0, min(cur, charges_max)), "max": charges_max}
        if cur > 0:
            actions.append({"id": "expend", "label": "Expend a charge"})
        actions.append({"id": "recharge", "label": "Recharge"})

    if family == "consumable":
        it = (getattr(row, "item_type", "") or "").lower() if row else ""
        label = "Drink" if "potion" in it else "Read the Scroll" if "scroll" in it else "Use"
        actions.append({"id": "use", "label": label})

    if needs_attune:
        caps["attuned"] = attuned
        actions.append({"id": "unattune" if attuned else "attune",
                        "label": "Break Attunement" if attuned else "Attune"})
    elif family == "wearable":
        caps["equipped"] = equipped
        actions.append({"id": "unequip" if equipped else "equip",
                        "label": "Unequip" if equipped else "Equip"})

    if actions:
        caps["actions"] = actions
    return caps


def _compute_ac(char: Character) -> int:
    """Armor Class from equipped armor/shield + attuned protection items.

    Unarmored is 10 + Dex; equipped armor uses its base + a Dex bonus capped by
    the armor (0 for heavy). A ``+N`` in an item's name adds its bonus; an
    attuned Ring/Cloak of Protection adds its bonus (default +1). Best effort —
    not every exotic AC-setter is modelled, but it beats the old flat 10."""
    dex = ability_modifier(_ability_score(char, "dexterity"))
    base = 10 + dex
    shield = 0
    misc = 0
    armored = False
    for it in _inventory_items(char):
        name = it.get("name") or ""
        low = name.lower()
        equipped = bool(it.get("equipped"))
        attuned = bool(it.get("attuned"))
        if not (equipped or attuned):
            continue
        m = re.search(r"\+(\d)", name)
        plus = int(m.group(1)) if m else 0
        if attuned and "protection" in low:      # Ring/Cloak of Protection
            misc += plus or 1
            continue
        if not equipped:
            continue
        try:
            row = rules_lib.get_item(name)
        except Exception:
            row = None
        it_type = (getattr(row, "item_type", "") or "").lower() if row else ""
        if "shield" in low or "shield" in it_type:
            shield = max(shield, 2 + plus)
            continue
        acb = getattr(row, "armor_class_base", None) if row else None
        if acb is not None or "armor" in it_type or "armor" in low:
            acb = acb if acb is not None else 11
            if getattr(row, "armor_dex_bonus", None) is False:
                dex_part = 0                       # heavy armor
            elif getattr(row, "armor_max_dex_bonus", None) is not None:
                dex_part = min(dex, int(row.armor_max_dex_bonus))
            else:
                dex_part = dex
            base = acb + dex_part + plus
            armored = True

    # Unarmored defenses + AC spells apply only with no worn armor.
    cls = (char.char_class or "").strip().lower()
    conds = {(c.get("name") or "").lower() for c in _character_conditions(char)}
    if not armored:
        if cls == "barbarian":
            base = max(base, 10 + dex + ability_modifier(_ability_score(char, "constitution")))
        elif cls == "monk":
            base = max(base, 10 + dex + ability_modifier(_ability_score(char, "wisdom")))
        if "mage armor" in conds:
            base = max(base, 13 + dex)
    if "shield of faith" in conds:
        misc += 2
    if "haste" in conds or "hasted" in conds:
        misc += 2

    total = base + shield + misc
    if "barkskin" in conds:                        # AC can't be less than 16
        total = max(total, 16)
    return total


def _consumable_heal(name: str) -> Optional[str]:
    """Healing dice for a Potion of Healing (by tier), else None."""
    n = (name or "").lower()
    if "potion of healing" not in n and "healing potion" not in n:
        return None
    if "supreme" in n:
        return "10d4+20"
    if "superior" in n:
        return "8d4+8"
    if "greater" in n:
        return "4d4+4"
    return "2d4+2"


def _consumable_effect(name: str) -> Optional[dict]:
    """Mechanical effect for a known consumable, or None (narrate + decrement).
    ``heal`` is a dice expr applied to HP; ``conditions`` are persistent-condition
    ops applied via _apply_condition_op (buffs are tracked as conditions since the
    sheet has no temp-HP field); ``remove_conditions`` clears afflictions;
    ``narration`` is a verb phrase completing '{name} drinks the X and {phrase}'."""
    heal = _consumable_heal(name)
    if heal:
        return {"heal": heal}
    n = (name or "").lower()

    def cond(cname: str, duration: str, note: Optional[str] = None) -> dict:
        op = {"action": "add", "name": cname, "source": name, "duration": duration}
        if note:
            op["note"] = note
        return op

    if "heroism" in n:
        return {"temp_hp": 10,
                "conditions": [cond("Heroism", "1 hour", "immune to being frightened")],
                "narration": "surges with fearless vigor, wreathed in 10 temporary hit points"}
    if "potion of growth" in n:
        return {"conditions": [cond("Enlarged", "1 minute", "advantage on Strength checks/saves, +1d4 weapon damage")],
                "narration": "swells to twice their size"}
    if "potion of diminution" in n:
        return {"conditions": [cond("Reduced", "1 minute", "disadvantage on Strength checks/saves, -1d4 weapon damage")],
                "narration": "shrinks to half their size"}
    if "invisibility" in n:
        return {"conditions": [cond("Invisible", "1 hour")], "narration": "fades from sight"}
    if "potion of speed" in n:
        return {"conditions": [cond("Hasted", "1 minute", "+2 AC, doubled speed, an extra action")],
                "narration": "blurs with sudden speed"}
    if "potion of flying" in n:
        return {"conditions": [cond("Flying", "1 hour", "a flying speed equal to their walking speed")],
                "narration": "rises weightless into the air"}
    if "water breathing" in n:
        return {"conditions": [cond("Water Breathing", "1 hour")], "narration": "can now breathe water"}
    if "potion of climbing" in n:
        return {"conditions": [cond("Climbing", "1 hour", "a climbing speed equal to their walking speed")],
                "narration": "finds every surface easy to climb"}
    if "mind reading" in n:
        return {"conditions": [cond("Detect Thoughts", "1 hour")], "narration": "hears the murmur of nearby thoughts"}
    if "potion of resistance" in n:
        return {"conditions": [cond("Damage Resistance", "1 hour", "resistance to one damage type")],
                "narration": "feels their flesh turn resilient"}
    if "antitoxin" in n:
        return {"conditions": [cond("Antitoxin", "1 hour", "advantage on saving throws against poison")],
                "narration": "steels their blood against poison"}
    if "elixir of health" in n or "potion of vitality" in n:
        return {"remove_conditions": True, "narration": "is restored to full vigor as every ill washes away"}
    return None


def _activity_inventory(char: Character) -> list[dict]:
    """Inventory as inspectable item objects: name, qty, and (when the catalog
    knows the item) type/rarity/brief + an ``interactive`` flag for special
    items like spellbooks. Full description + art are fetched on inspect."""
    try:
        from rules.query import RulesLibrary
        lib = RulesLibrary(engine=engine)
    except Exception:
        lib = None
    out: list[dict] = []
    for it in _inventory_items(char):
        name = it.get("name") or "Unknown item"
        obj: dict = {"name": name, "qty": it.get("quantity", 1) or 1}
        row = None
        if lib is not None:
            try:
                row = lib.get_item(name)
                if row:
                    if row.item_type or row.category:
                        obj["type"] = row.item_type or row.category
                    if row.rarity:
                        obj["rarity"] = row.rarity
                    brief = _brief_of(row.desc)
                    if brief:
                        obj["brief"] = brief
            except Exception:
                row = None
        if it.get("notes") and "brief" not in obj:
            obj["brief"] = str(it["notes"])[:150]
        # Interactive badge: spellbook/charged/consumable/container get their own
        # badge; attunement items are flagged too; plain wearables don't badge
        # (they still expose equip actions on click).
        fam = _item_family(row, name)
        if row is not None and getattr(row, "requires_attunement", False) and fam in (None, "wearable"):
            obj["interactive"] = "attunement"
        elif fam and fam != "wearable":
            obj["interactive"] = fam
        out.append(obj)
    return out


def _img_data_url(res) -> str:
    return f"data:{res.mime};base64,{res.b64()}"


def _item_stat_lines(row) -> list[str]:
    """A few mechanical stat lines for the inspector (weapon/armor/cost/weight)."""
    lines: list[str] = []
    if getattr(row, "damage_dice", None):
        d = row.damage_dice + (f" {row.damage_type}" if row.damage_type else "")
        lines.append(f"Damage: {d}")
    if getattr(row, "two_handed_damage_dice", None):
        lines.append(f"Versatile: {row.two_handed_damage_dice}")
    if getattr(row, "armor_class_base", None) is not None:
        lines.append(f"Base AC: {row.armor_class_base}")
    props = getattr(row, "properties", None)
    if isinstance(props, list) and props:
        lines.append("Properties: " + ", ".join(str(p) for p in props))
    if getattr(row, "cost_gp", None) is not None:
        lines.append(f"Cost: {row.cost_gp:g} gp")
    if getattr(row, "weight", None):
        lines.append(f"Weight: {row.weight:g} lb")
    return lines


def _spellbook_spells(char: Character) -> list[dict]:
    """The character's inscribed spells as {name, level}, sorted by level."""
    try:
        from rules.query import RulesLibrary
        lib = RulesLibrary(engine=engine)
    except Exception:
        lib = None
    out: list[dict] = []
    seen: set[str] = set()
    for s in (char.spells or []):
        name = s if isinstance(s, str) else (s.get("name") or s.get("spell")) if isinstance(s, dict) else None
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        level = s.get("level") if isinstance(s, dict) else None
        if level is None and lib is not None:
            try:
                sp = lib.get_spell(name)
                if sp is not None:
                    level = getattr(sp, "level", None)
            except Exception:
                pass
        out.append({"name": name, "level": level})
    out.sort(key=lambda x: (x["level"] if x["level"] is not None else 99, x["name"]))
    return out


def _can_inscribe(char: Character) -> bool:
    """Who may write into a spellbook: wizards, or anyone with a feat that grants
    a spellbook (Ritual Caster / a spellbook-granting feat)."""
    if (char.char_class or "").strip().lower() == "wizard":
        return True
    feats = getattr(char, "feats", None) or []
    return any(("spellbook" in str(f).lower() or "ritual caster" in str(f).lower()) for f in feats)


def _activity_item_detail(char: Character, name: str) -> dict:
    """Full inspector payload for one item: type/rarity/attunement/description
    + mechanical stats, cached art if present, and spellbook contents when the
    item is interactive. Art is (re)generated in the background by the caller."""
    detail: dict = {"name": name}
    item_type = None
    try:
        from rules.query import RulesLibrary
        row = RulesLibrary(engine=engine).get_item(name)
    except Exception:
        row = None
    if row:
        item_type = row.item_type or row.category
        if item_type:
            detail["type"] = item_type
        if row.rarity:
            detail["rarity"] = row.rarity
        detail["attunement"] = bool(getattr(row, "requires_attunement", False))
        if row.desc:
            detail["description"] = row.desc.strip()
        stats = _item_stat_lines(row)
        if stats:
            detail["stats"] = stats
    inv_item = None
    for it in _inventory_items(char):
        if _normalize_item_name(it.get("name")) == _normalize_item_name(name):
            inv_item = it
            break
    if "description" not in detail and inv_item and inv_item.get("notes"):
        detail["description"] = str(inv_item["notes"])
    family = _item_family(row, name)
    if family == "spellbook":
        detail["interactive"] = "spellbook"
        detail["spells"] = _spellbook_spells(char)
        detail["can_inscribe"] = _can_inscribe(char)
    else:
        if family == "container":
            detail["interactive"] = "container"
            contents: list[dict] = []
            for sub in ((inv_item or {}).get("contents") or []):
                if isinstance(sub, str):
                    contents.append({"name": sub, "qty": 1})
                elif isinstance(sub, dict):
                    contents.append({"name": sub.get("name", "?"), "qty": sub.get("qty", 1) or 1})
            detail["contents"] = contents
        detail.update(_item_caps(row, name, inv_item, char, family))
    return detail


def _activity_sheet(session_id: str, user_id: str) -> Optional[dict]:
    """Map the internal character sheet onto the Activity's compact shape."""
    char_id = _activity_char_id(session_id, user_id)
    if not char_id:
        return None
    with Session(engine) as session:
        char = session.get(Character, char_id)
        if not char:
            return None
        sheet = _build_character_sheet(char)
        # Derived, themeable extras for the v1 Activity sheet (all optional on
        # the client; computed while the character row is live).
        features = _sheet_features(session, char)
        resources = _class_resources_for(char)
        spell_slots = _spell_slots_for(char.char_class, char.level,
                                       char.spell_slots_used)
        background = char.background
        portrait = _activity_portrait_url(char.name)
        inventory = _activity_inventory(char)
        ac_val = _compute_ac(char)
    bits = [f"Level {sheet['level']} {sheet['char_class'] or '?'}"]
    if sheet.get("subclass"):
        bits[0] += f" ({sheet['subclass']})"
    if sheet.get("race"):
        bits.append(sheet["race"])
    return {
        "name": sheet["name"],
        "subtitle": " · ".join(bits),
        "hp": sheet["combat"]["current_hp"],
        "hp_max": sheet["combat"]["max_hp"],
        "temp_hp": sheet["combat"].get("temp_hp", 0),
        "ac": ac_val,
        "stats": {a[:3].upper(): v["score"] for a, v in sheet["abilities"].items()},
        "skills": [c for c in (sheet.get("conditions") or [])] or
                  [s for s in (sheet.get("spells") or [])][:6],
        "inventory": inventory[:24],
        "gold": (sheet.get("purse") or {}).get("gp"),
        # ---- v1 structured / themeable fields ----
        "race": sheet.get("race"),
        "char_class": sheet.get("char_class"),
        "subclass": sheet.get("subclass"),
        "background": background,
        "portrait": portrait,
        "spell_slots": spell_slots,
        "resources": resources,
        "features": features,
    }


def _activity_lexicon(session_id: str, pc_name: Optional[str],
                      reply_text: str) -> list[dict]:
    """Names worth colouring: the PC, world-slice entities, and any rules
    entities (spells/monsters) referenced in the reply."""
    entries: list[dict] = []
    seen: set[str] = set()

    def add(text: Optional[str], kind: str):
        if not text or len(text) < 3 or text.lower() in seen:
            return
        seen.add(text.lower())
        entries.append({"text": text, "kind": kind})

    add(pc_name, "name")
    try:
        ctx, _ = assemble_context(session_id, reply_text[:400])
        for e in getattr(ctx, "entities", []) or []:
            etype = (getattr(e, "type", "") or "").lower()
            kind = "place" if etype in (
                "place", "settlement", "region", "ward", "building") else "name"
            add(getattr(e, "name", None), kind)
    except Exception:
        pass
    try:
        for kind, obj in rules_lib.find_mentions(reply_text, limit=8):
            add(obj.name, "magic" if kind == "spell" else "name")
    except Exception:
        pass
    return entries[:40]


def _activity_segments(reply: str, rolls: list[dict]) -> list[dict]:
    """Split the reply into narration blocks interleaved with roll cards at
    the exact positions their inline '🎲 …' markers occupy."""
    events: list[dict] = []
    rest = reply

    def push_text(chunk: str):
        chunk = chunk.strip()
        if chunk:
            events.append({"t": "narration", "text": chunk})

    for r in rolls:
        marker = r.pop("marker", None)
        if marker and marker in rest:
            pre, rest = rest.split(marker, 1)
            push_text(pre)
        events.append({"t": "roll", "roll": r})
    push_text(rest)
    return events


# session_id -> live sockets; narration/rolls/scenes broadcast to the whole
# table so a multiplayer session is just several sockets sharing an id.
_ACTIVITY_SOCKETS: Dict[str, set] = {}
# channel -> the live table session in that channel (channel = table rule).
_ACTIVITY_TABLES: Dict[str, str] = {}
# (session_id, user_id) -> [timestamps] for the table-only action rate limit.
_ACTIVITY_ACTION_LOG: Dict[tuple, list] = {}
ACTIVITY_RATE_BURST = 3          # actions allowed in the window…
ACTIVITY_RATE_WINDOW_S = 60.0    # …per this many seconds (tables only)

# Latest DM music cue per channel (= Discord voice channel id), so the bot can
# poll and switch the voice-channel playlist to match the scene. The Activity
# plays via the browser and never touches the bot's voice player directly, so
# this seq-versioned store is the backend->bot bridge. GET /activity/music/{ch}.
_ACTIVITY_MUSIC: Dict[str, dict] = {}
_ACTIVITY_MUSIC_SEQ = 0


def _set_activity_music(channel: Optional[str], query: Optional[str]) -> None:
    """Record the DM's latest ambient-music cue for a channel (bumps the seq)."""
    global _ACTIVITY_MUSIC_SEQ
    if not channel or not query:
        return
    _ACTIVITY_MUSIC_SEQ += 1
    _ACTIVITY_MUSIC[str(channel)] = {"query": query, "seq": _ACTIVITY_MUSIC_SEQ}


@app.get("/activity/music/{channel}")
def activity_music(channel: str, since: int = 0):
    """The bot polls this per active table to sync the voice-channel playlist to
    the scene. Returns the latest cue if newer than ``since``, else a no-op."""
    cue = _ACTIVITY_MUSIC.get(str(channel))
    if cue and cue["seq"] > since:
        return {"query": cue["query"], "seq": cue["seq"]}
    return {"query": None, "seq": since}


async def _activity_broadcast(session_id: Optional[str], ev: dict,
                              fallback: Optional[WebSocket] = None):
    targets = list(_ACTIVITY_SOCKETS.get(session_id or "", ())) or (
        [fallback] if fallback else [])
    for sock in targets:
        try:
            await sock.send_json(ev)
        except Exception:
            pass


def _activity_characters(user_id: str, channel: str) -> list[dict]:
    """The landing page's character list: this player's PCs with liveness and
    a resumable session id when one exists for this channel."""
    with Session(engine) as session:
        chars = session.exec(select(Character).where(
            Character.discord_user_id == user_id,
            Character.is_npc == False)).all()  # noqa: E712
        out = []
        for c in chars:
            alive = True
            try:
                ent = world.find_pc(user_id, c.name)
                alive = ent is None or ent.status != "dead"
            except Exception:
                pass
            out.append({
                "id": c.id, "name": c.name, "race": c.race,
                "char_class": c.char_class, "subclass": c.subclass,
                "level": c.level, "alive": alive,
                "resume_session": _activity_find_session(
                    session, user_id, channel, c.name),
                # Downtime commitment: days until this PC is playable again.
                "returns_in": _pc_busy_until(
                    getattr(world.find_pc(user_id, c.name), "slug", None))
                    if alive else None,
            })
    return out


def _activity_party(session_id: str) -> list[dict]:
    """Party strip data for every seated member (live HP from Character)."""
    state = _load_session_state(session_id)
    members = _session_members(state.get("meta", {}) or {})
    out = []
    with Session(engine) as s:
        for m in members.values():
            if not m.get("character_id"):
                continue
            c = s.get(Character, m["character_id"])
            if c:
                out.append({"name": c.name, "hp": c.current_hp,
                            "hp_max": c.max_hp})
    return out


def _activity_join_table(table_sid: str, user_id: str, username: str,
                         char_name: str, channel: str) -> dict:
    """Seat a player's PC at a live table, gated by spatial plausibility:
    the PC's unwritten away-days must be able to contain the journey to the
    table. Returns {'ok': True, 'arrival': str} or {'ok': False, 'reason',
    'travel_days', 'away_days'}."""
    with Session(engine) as s:
        char = s.exec(select(Character).where(
            Character.discord_user_id == user_id,
            Character.name.ilike(char_name))).first()  # type: ignore[attr-defined]
    if not char:
        return {"ok": False, "reason": f"No character named {char_name!r}."}
    wait = _pc_busy_until(getattr(world.find_pc(user_id, char.name), "slug", None))
    if wait:
        return {"ok": False, "reason": f"{char.name} is committed to downtime "
                                       f"for {wait} more world day(s)."}

    state = _load_session_state(table_sid)
    meta = dict(state.get("meta", {}) or {})
    members = _session_members(meta)
    owner = next(iter(members.values()), None)
    table_loc = world.location_of(owner["pc_slug"]) if owner and owner.get("pc_slug") else None
    today = world.current_day()

    pc_ent = world.find_pc(user_id, char.name)
    arrival = f"{char.name} joins the tale."
    if pc_ent is None:
        # Never placed: spawn directly at the table.
        pc_ent = place_pc(world, char.name, discord_user_id=user_id,
                          location_slug=(table_loc.slug if table_loc
                                         else "the-silver-tankard"),
                          attributes={"race": char.race, "class": char.char_class,
                                      "subclass": char.subclass,
                                      "level": char.level})
        arrival = f"{char.name} arrives, new to these roads."
    elif table_loc is not None:
        pc_loc = world.location_of(pc_ent.slug)
        if pc_loc is None or pc_loc.slug != table_loc.slug:
            from eight_card_system import geo
            a = world.coords_of(pc_ent.slug)
            b = world.coords_of(table_loc.slug)
            travel_days = 0
            if a and b:
                travel_days = max(0, round(
                    geo.distance_mi(a, b) / geo.MILES_PER_DAY_ON_FOOT))
            last = (pc_ent.attributes or {}).get("last_active_day")
            away = (today - int(last)) if last is not None else 9999
            if travel_days > away + 1:
                return {"ok": False,
                        "reason": (f"{char.name} is roughly {travel_days} days' "
                                   f"travel from {table_loc.name}, but only "
                                   f"{away} unwritten day(s) have passed for "
                                   "them — they cannot be in this tale yet."),
                        "travel_days": travel_days, "away_days": away}
            world.move_entity(pc_ent.slug, table_loc.slug)
            arrival = (f"{char.name} arrives at {table_loc.name} after "
                       f"{max(1, travel_days)} day(s) on the road.")

    world.upsert_entity(pc_ent.name, pc_ent.type, slug=pc_ent.slug,
                        status=pc_ent.status,
                        attributes={"last_active_day": today})
    members[str(user_id)] = {"character_id": char.id,
                             "character_name": char.name,
                             "pc_slug": pc_ent.slug}
    meta["members"] = members
    meta["activity_channel"] = channel
    _set_session_meta(table_sid, meta)
    return {"ok": True, "arrival": arrival}


def _activity_find_session(session: Session, user_id: str, channel: str,
                           character_name: str) -> Optional[str]:
    """Most recent Activity session for (player, channel, character)."""
    import json as _json
    rows = session.exec(select(SessionMemory).order_by(
        SessionMemory.updated_at.desc()).limit(200)).all()  # type: ignore[attr-defined]
    for r in rows:
        try:
            meta = _json.loads(r.meta_json or "{}")
        except Exception:
            continue
        if (meta.get("user_id") == user_id
                and meta.get("activity_channel") == channel
                and (meta.get("character_name") or "").lower()
                == character_name.lower()):
            return r.session_id
    return None


@app.websocket("/ws/activity/{channel}")
async def activity_ws(ws: WebSocket, channel: str):
    await ws.accept()
    user_id = ws.query_params.get("user_id", "activity-dev")
    username = ws.query_params.get("username", "Adventurer")
    session_id: Optional[str] = None

    async def send_levelup():
        try:
            lv = _activity_levelup(session_id, user_id)
        except Exception as e:
            print(f"[activity] level-up check failed: {e}")
            lv = None
        await ws.send_json({"t": "levelup", "data": lv})

    async def send_hello():
        await ws.send_json({"t": "hello", "channel": channel,
                            "characters": _activity_characters(user_id, channel)})

    async def bind_session(sid: str):
        nonlocal session_id
        if session_id and ws in _ACTIVITY_SOCKETS.get(session_id, set()):
            _ACTIVITY_SOCKETS[session_id].discard(ws)
        session_id = sid
        _ACTIVITY_SOCKETS.setdefault(sid, set()).add(ws)

    async def send_state():
        nonlocal sheet
        sheet = _activity_sheet(session_id, user_id)
        if sheet:
            await ws.send_json({"t": "sheet", "sheet": sheet})
            party = _activity_party(session_id) or [{
                "name": sheet["name"], "hp": sheet["hp"],
                "hp_max": sheet["hp_max"]}]
            await _activity_broadcast(session_id, {"t": "party",
                                                   "members": party},
                                      fallback=ws)
            await ws.send_json({"t": "lexicon",
                                "entries": [{"text": sheet["name"], "kind": "name"}]})
        # Live initiative board for the combat carousel (null clears it).
        try:
            enc = combat.get_active(session_id)
            await _activity_broadcast(session_id, {
                "t": "combat",
                "encounter": combat.state(enc.id) if enc else None,
            }, fallback=ws)
        except Exception as e:
            print(f"[activity] combat state push failed: {e}")
        await send_levelup()

    sheet: Optional[dict] = None
    await send_hello()

    try:
        while True:
            msg = await ws.receive_json()

            # ---- landing: forge a new character (deterministic CC data) ----
            if msg.get("t") == "cc_register":
                p = msg.get("payload") or {}
                try:
                    req = RegisterCharacterRequest(
                        discord_user_id=user_id,
                        name=(p.get("name") or "").strip(),
                        race=p.get("race"), char_class=p.get("char_class"),
                        background=p.get("background"),
                        stats=p.get("stats"), skills=p.get("skills"),
                        feats=p.get("feats"), approve=True, source="guided",
                        gear_mode=p.get("gear_mode") or "kit",
                        bought_items=p.get("bought_items"),
                        wondrous_item=p.get("wondrous_item"),
                    )
                    result = await register_character(req)
                except HTTPException as e:
                    await ws.send_json({"t": "cc_error", "detail": e.detail})
                    continue
                except Exception as e:
                    print(f"[activity] cc_register failed: {e}")
                    await ws.send_json({"t": "cc_error",
                                        "detail": "Character creation failed."})
                    continue
                await ws.send_json({"t": "cc_done",
                                    "name": req.name,
                                    "detail": result if isinstance(result, dict) else None})
                await send_hello()
                continue

            # ---- landing: enter the world (resume or begin) ----
            if msg.get("t") == "enter":
                char_name = (msg.get("character_name") or "").strip()
                await ws.send_json({"t": "busy", "on": True})

                # Channel = table: if another player's session is live in this
                # channel, entering means JOINING it (spatial gate applies) —
                # unless the client explicitly asked for a solo tale.
                live_sid = _ACTIVITY_TABLES.get(channel)
                if live_sid and not _ACTIVITY_SOCKETS.get(live_sid):
                    live_sid = None  # stale registry entry: table went dark
                    _ACTIVITY_TABLES.pop(channel, None)
                if (live_sid and char_name and not msg.get("solo")):
                    _lm = _session_members(
                        _load_session_state(live_sid).get("meta", {}) or {})
                    already = str(user_id) in _lm
                    if _lm and not already:
                        res = _activity_join_table(
                            live_sid, user_id, username, char_name, channel)
                        if not res.get("ok"):
                            await ws.send_json({
                                "t": "join_blocked",
                                "reason": res.get("reason"),
                                "travel_days": res.get("travel_days"),
                                "away_days": res.get("away_days")})
                            await ws.send_json({"t": "busy", "on": False})
                            continue
                        await bind_session(live_sid)
                        await ws.send_json({"t": "entered", "resumed": True})
                        await _activity_broadcast(session_id, {
                            "t": "narration", "text": f"*{res['arrival']}*"})
                        await send_state()
                        await ws.send_json({"t": "busy", "on": False})
                        continue
                    if already:
                        await bind_session(live_sid)
                        await ws.send_json({"t": "entered", "resumed": True})
                        await send_state()
                        await ws.send_json({"t": "busy", "on": False})
                        continue

                resume_sid = None
                if char_name:
                    with Session(engine) as s:
                        resume_sid = _activity_find_session(
                            s, user_id, channel, char_name)
                if resume_sid:
                    await bind_session(resume_sid)
                    _ACTIVITY_TABLES.setdefault(channel, session_id)
                    state = _load_session_state(session_id)
                    recap = [t for t in (state.get("recent_turns") or [])
                             if getattr(t, "role", None) == "dm"
                             or (isinstance(t, dict) and t.get("role") == "dm")]
                    last = recap[-1] if recap else None
                    last_text = (last.get("content") if isinstance(last, dict)
                                 else getattr(last, "content", "")) if last else ""
                    await ws.send_json({"t": "entered", "resumed": True})
                    if last_text:
                        await ws.send_json({
                            "t": "narration",
                            "text": "*The tale resumes where it left off…*\n\n"
                                    + last_text})
                else:
                    try:
                        er = await enter_world(EnterRequest(
                            user_id=user_id, username=username,
                            guild_id=f"activity-{channel}",
                            character_name=char_name or None))
                    except Exception as e:
                        print(f"[activity] enterworld failed: {e}")
                        await ws.send_json({"t": "cc_error",
                                            "detail": "Could not enter the world."})
                        await ws.send_json({"t": "busy", "on": False})
                        continue
                    if er.status != "ok" or not er.session_id:
                        await ws.send_json({"t": "cc_error", "detail": er.message})
                        await ws.send_json({"t": "busy", "on": False})
                        continue
                    await bind_session(er.session_id)
                    # Merge (never replace) so enterworld's meta survives;
                    # seed the members map with the table's founder.
                    _state = _load_session_state(session_id)
                    _meta = dict(_state.get("meta") or {})
                    _meta["activity_channel"] = channel
                    if _meta.get("character_id"):
                        _meta["members"] = {str(user_id): {
                            "character_id": _meta.get("character_id"),
                            "character_name": _meta.get("character_name"),
                            "pc_slug": _meta.get("pc_slug"),
                        }}
                    _set_session_meta(session_id, _meta)
                    _ACTIVITY_TABLES.setdefault(channel, session_id)
                    await ws.send_json({"t": "entered", "resumed": False})
                    # Opening ambient-music cue (the bot polls and switches).
                    _set_activity_music(channel, er.music)
                    if er.intro:
                        await ws.send_json({"t": "narration", "text": er.intro})
                    # Opening establishing scene (guaranteed by enter_world).
                    for img in er.images or []:
                        if img.get("b64"):
                            await ws.send_json({
                                "t": "scene",
                                "url": f"data:{img.get('mime', 'image/webp')};base64,{img['b64']}",
                            })
                    # Merge-invite: another live table at the same place is an
                    # invitation, not a contradiction.
                    try:
                        my_loc = world.location_of(_meta.get("pc_slug"))
                        for other_ch, other_sid in _ACTIVITY_TABLES.items():
                            if other_sid == session_id or not _ACTIVITY_SOCKETS.get(other_sid):
                                continue
                            om = _session_members(_load_session_state(
                                other_sid).get("meta", {}) or {})
                            owner = next(iter(om.values()), None)
                            if not (owner and owner.get("pc_slug") and my_loc):
                                continue
                            other_loc = world.location_of(owner["pc_slug"])
                            if other_loc and other_loc.slug == my_loc.slug:
                                await ws.send_json({
                                    "t": "table_invite",
                                    "place": my_loc.name,
                                    "channel": other_ch})
                                break
                    except Exception as e:
                        print(f"[activity] merge-invite scan failed: {e}")
                await send_state()
                await ws.send_json({"t": "busy", "on": False})
                continue

            # Everything below requires a bound session.
            if session_id is None:
                continue

            if msg.get("t") == "levelup_apply":
                char_id = _activity_char_id(session_id, user_id)
                if not char_id:
                    continue
                try:
                    result = await level_up(LevelUpRequest(
                        character_id=char_id, subclass=msg.get("subclass")))
                except HTTPException as e:
                    await ws.send_json({"t": "narration", "text": f"⚠ {e.detail}"})
                    await send_levelup()
                    continue
                if result.get("applied"):
                    sub = f" — {result['subclass']}" if result.get("subclass") else ""
                    await _activity_broadcast(session_id, {
                        "t": "narration",
                        "text": (f"{result['name']} rises to level "
                                 f"{result['current_level']}{sub}. "
                                 "New strength settles into old scars."),
                    }, fallback=ws)
                    await ws.send_json({"t": "levelup", "data": None})
                    sheet = _activity_sheet(session_id, user_id)
                    if sheet:
                        await ws.send_json({"t": "sheet", "sheet": sheet})
                else:
                    await send_levelup()  # subclass still required
                continue

            # ---- inspect an inventory item (focused window: detail + art) ----
            if msg.get("t") == "inspect_item":
                name = (msg.get("name") or "").strip()
                cid = _activity_char_id(session_id, user_id) if session_id else None
                if not name or not cid:
                    continue
                with Session(engine) as s:
                    ch = s.get(Character, cid)
                    detail = _activity_item_detail(ch, name) if ch else None
                if detail is None:
                    continue
                await ws.send_json({"t": "item_detail", "item": detail})
                # Generate/fetch the item's picture off the event loop and push it
                # in when ready, so the modal opens instantly and the art fills in.
                if get_config().imagery.enabled:
                    async def _gen_item_art(nm=name):
                        try:
                            loop = asyncio.get_event_loop()
                            res = await loop.run_in_executor(
                                None, lambda: image_store.ensure_image(
                                    "item", nm, context="inspect"))
                            if res and not getattr(res, "offline", False):
                                await ws.send_json({"t": "item_image", "name": nm,
                                                    "url": _img_data_url(res)})
                        except Exception as e:
                            print(f"[activity] item art gen: {e}")
                    asyncio.create_task(_gen_item_art())
                continue

            # ---- inscribe a spell into a spellbook (wizard etc.) ----
            if msg.get("t") == "inscribe_spell":
                spell = (msg.get("spell") or "").strip()
                book = (msg.get("book") or "Spellbook").strip()
                cid = _activity_char_id(session_id, user_id) if session_id else None
                if not spell or not cid:
                    continue
                with Session(engine) as s:
                    ch = s.get(Character, cid)
                    if not ch:
                        continue
                    if not _can_inscribe(ch):
                        await ws.send_json({"t": "item_error",
                            "detail": "Only a wizard — or one trained to keep a "
                                      "spellbook — can inscribe spells."})
                        continue
                    valid = None
                    try:
                        valid = rules_lib.get_spell(spell)
                    except Exception:
                        pass
                    if valid is None:
                        await ws.send_json({"t": "item_error",
                            "detail": f"No spell named “{spell}” found in the tomes."})
                        continue
                    spell_name = getattr(valid, "name", spell)
                    spells = list(ch.spells or [])
                    have = {(x if isinstance(x, str) else (x.get("name") or "")).lower()
                            for x in spells}
                    if spell_name.lower() in have:
                        await ws.send_json({"t": "item_error",
                            "detail": f"{spell_name} is already inscribed here."})
                        continue
                    spells.append(spell_name)
                    ch.spells = spells
                    s.add(ch); s.commit(); s.refresh(ch)
                    detail = _activity_item_detail(ch, book)
                await ws.send_json({"t": "item_detail", "item": detail})
                refreshed = _activity_sheet(session_id, user_id)
                if refreshed:
                    await ws.send_json({"t": "sheet", "sheet": refreshed})
                continue

            # ---- act on an item: equip/attune/expend/use (interactive items) ----
            if msg.get("t") == "item_action":
                name = (msg.get("name") or "").strip()
                action = (msg.get("action") or "").strip()
                cid = _activity_char_id(session_id, user_id) if session_id else None
                if not name or not action or not cid:
                    continue
                gone = False
                use_roll = None
                use_narration = None
                with Session(engine) as s:
                    ch = s.get(Character, cid)
                    if not ch:
                        continue
                    inv = list(ch.inventory or [])
                    idx = None
                    for i, raw in enumerate(inv):
                        rn = raw if isinstance(raw, str) else (raw.get("name") or raw.get("item"))
                        if _normalize_item_name(rn) == _normalize_item_name(name):
                            idx = i
                            break
                    if idx is None:
                        await ws.send_json({"t": "item_error",
                                            "detail": "That item isn't in your pack."})
                        continue
                    raw = inv[idx]
                    item = {"name": name, "quantity": 1} if isinstance(raw, str) else dict(raw)
                    try:
                        row = rules_lib.get_item(name)
                    except Exception:
                        row = None
                    desc = getattr(row, "desc", "") if row else ""
                    err = None
                    if action == "equip":
                        item["equipped"] = True
                    elif action == "unequip":
                        item["equipped"] = False
                    elif action == "attune":
                        if not item.get("attuned") and _attuned_count(ch) >= 3:
                            err = "You can be attuned to only three items at once."
                        else:
                            item["attuned"] = True
                    elif action == "unattune":
                        item["attuned"] = False
                    elif action == "expend":
                        cmax = _detect_charges(desc) or 0
                        cur = int(item.get("charges_current", cmax))
                        if cur <= 0:
                            err = "No charges remain."
                        else:
                            item["charges_current"] = cur - 1
                    elif action == "recharge":
                        cmax = _detect_charges(desc)
                        if cmax:
                            item["charges_current"] = cmax
                    elif action == "use":
                        qty = int(item.get("quantity", 1) or 1)
                        eff = _consumable_effect(name)
                        low = name.lower()
                        verb = (f"{ch.name} drinks the {name}"
                                if any(w in low for w in ("potion", "elixir", "antitoxin"))
                                else f"{ch.name} uses the {name}")
                        if eff and eff.get("heal"):
                            from dice.roller import roll as _dice_roll
                            r = _dice_roll(eff["heal"])
                            before = ch.current_hp
                            ch.current_hp = min(ch.max_hp, ch.current_hp + r.total)
                            gained = ch.current_hp - before
                            use_narration = (f"{verb} and regains {gained} "
                                             f"hit point{'s' if gained != 1 else ''}.")
                            use_roll = {"expr": eff["heal"], "label": name,
                                        "total": r.total, "detail": r.detail}
                        elif eff:
                            if eff.get("temp_hp"):
                                # Temp HP doesn't stack — keep the larger pool.
                                ch.temp_hp = max(int(getattr(ch, "temp_hp", 0) or 0), int(eff["temp_hp"]))
                            for op in eff.get("conditions", []):
                                _apply_condition_op(ch, op)
                            if eff.get("remove_conditions"):
                                _apply_condition_op(ch, {"action": "clear"})
                            use_narration = f"{verb} and {eff.get('narration', 'feels its magic take hold')}."
                        else:
                            use_narration = f"{verb}."
                        if qty <= 1:
                            inv.pop(idx)
                            gone = True
                        else:
                            item["quantity"] = qty - 1
                    elif action in ("store", "take_out"):
                        target = (msg.get("target") or "").strip()
                        contents = list(item.get("contents") or [])
                        if not target:
                            err = "Choose an item."
                        elif action == "store":
                            tidx = None
                            for j, r2 in enumerate(inv):
                                if j == idx:
                                    continue
                                rn2 = r2 if isinstance(r2, str) else (r2.get("name") or r2.get("item"))
                                if _normalize_item_name(rn2) == _normalize_item_name(target):
                                    tidx = j
                                    break
                            if tidx is None:
                                err = f"You aren't carrying {target}."
                            else:
                                tr = inv[tidx]
                                titem = {"name": target, "quantity": 1} if isinstance(tr, str) else dict(tr)
                                tq = int(titem.get("quantity", 1) or 1)
                                merged = False
                                for cs in contents:
                                    if isinstance(cs, dict) and _normalize_item_name(cs.get("name")) == _normalize_item_name(target):
                                        cs["qty"] = int(cs.get("qty", 1) or 1) + 1
                                        merged = True
                                        break
                                if not merged:
                                    contents.append({"name": titem.get("name", target), "qty": 1})
                                item["contents"] = contents
                                if tq <= 1:
                                    inv.pop(tidx)
                                    if tidx < idx:
                                        idx -= 1
                                else:
                                    titem["quantity"] = tq - 1
                                    inv[tidx] = titem
                                inv[idx] = item
                        else:  # take_out
                            cidx = None
                            for k, cs in enumerate(contents):
                                csn = cs if isinstance(cs, str) else cs.get("name")
                                if _normalize_item_name(csn) == _normalize_item_name(target):
                                    cidx = k
                                    break
                            if cidx is None:
                                err = f"{target} isn't in this container."
                            else:
                                cs = contents[cidx]
                                cq = 1 if isinstance(cs, str) else int(cs.get("qty", 1) or 1)
                                if cq <= 1:
                                    contents.pop(cidx)
                                else:
                                    cs["qty"] = cq - 1
                                item["contents"] = contents
                                mfound = False
                                for r2 in inv:
                                    if isinstance(r2, dict) and _normalize_item_name(r2.get("name")) == _normalize_item_name(target):
                                        r2["quantity"] = int(r2.get("quantity", 1) or 1) + 1
                                        mfound = True
                                        break
                                if not mfound:
                                    inv.append({"name": target, "quantity": 1})
                                inv[idx] = item
                    else:
                        err = "That item can't do that."
                    if err:
                        await ws.send_json({"t": "item_error", "detail": err})
                        continue
                    if not gone:
                        inv[idx] = item
                    ch.inventory = inv
                    s.add(ch); s.commit(); s.refresh(ch)
                    detail = None if gone else _activity_item_detail(ch, name)
                if use_roll:
                    await _activity_broadcast(session_id, {"t": "roll", "roll": use_roll},
                                              fallback=ws)
                if use_narration:
                    await _activity_broadcast(session_id, {"t": "narration",
                                                           "text": use_narration}, fallback=ws)
                if gone:
                    await ws.send_json({"t": "item_gone", "name": name})
                else:
                    await ws.send_json({"t": "item_detail", "item": detail})
                refreshed = _activity_sheet(session_id, user_id)
                if refreshed:
                    await ws.send_json({"t": "sheet", "sheet": refreshed})
                continue

            if msg.get("t") != "action" or not (msg.get("text") or "").strip():
                continue
            text = msg["text"].strip()

            # Table-only rate limit: shared narration is a commons; one player
            # can't monopolize it. Solo sessions are never limited.
            _members_now = _session_members(
                _load_session_state(session_id).get("meta", {}) or {})
            if len(_members_now) > 1:
                import time as _time
                key = (session_id, str(user_id))
                log = [t for t in _ACTIVITY_ACTION_LOG.get(key, [])
                       if _time.time() - t < ACTIVITY_RATE_WINDOW_S]
                if len(log) >= ACTIVITY_RATE_BURST:
                    wait = int(ACTIVITY_RATE_WINDOW_S - (_time.time() - log[0])) + 1
                    await ws.send_json({"t": "rate_limited", "wait": wait})
                    continue
                log.append(_time.time())
                _ACTIVITY_ACTION_LOG[key] = log

            await _activity_broadcast(session_id, {"t": "busy", "on": True},
                                      fallback=ws)
            await _activity_broadcast(session_id, {
                "t": "player", "text": text, "who": username}, fallback=ws)

            rolls: list[dict] = []
            token = _ACTIVITY_ROLLS.set(rolls)
            try:
                bt = BackgroundTasks()
                req = ChatRequest(session_id=session_id, user_id=user_id,
                                  username=username, message=text)
                # Sync endpoint with blocking I/O: run in a worker thread
                # (contextvars propagate, so the roll collector works).
                resp = await asyncio.to_thread(chat_endpoint, req, bt)
            except HTTPException as e:
                await ws.send_json({"t": "narration", "text": f"⚠ {e.detail}"})
                await ws.send_json({"t": "busy", "on": False})
                _ACTIVITY_ROLLS.reset(token)
                continue
            except Exception as e:
                print(f"[activity] chat failed: {e}")
                await ws.send_json({"t": "narration",
                                    "text": "⚠ The Oracle's vision clouds. Try again."})
                await ws.send_json({"t": "busy", "on": False})
                _ACTIVITY_ROLLS.reset(token)
                continue
            _ACTIVITY_ROLLS.reset(token)

            # Relay the DM's scene music cue to the bot (voice channel = channel).
            _set_activity_music(channel, resp.music)

            pc_name = (sheet or {}).get("name")
            lex = _activity_lexicon(session_id, pc_name, resp.reply)
            if lex:
                await _activity_broadcast(session_id, {"t": "lexicon",
                                                       "entries": lex},
                                          fallback=ws)

            for ev in _activity_segments(resp.reply, rolls):
                await _activity_broadcast(session_id, ev, fallback=ws)

            for img in resp.images or []:
                if img.get("b64"):
                    await _activity_broadcast(session_id, {
                        "t": "scene",
                        "url": f"data:{img.get('mime', 'image/webp')};base64,{img['b64']}",
                    }, fallback=ws)

            await send_state()
            await _activity_broadcast(session_id, {"t": "busy", "on": False},
                                      fallback=ws)

            # Post-reply pipeline work (world extraction, compaction).
            try:
                await bt()
            except Exception as e:
                print(f"[activity] background tasks failed: {e}")
    except WebSocketDisconnect:
        pass
    finally:
        if session_id:
            _ACTIVITY_SOCKETS.get(session_id, set()).discard(ws)
            if not _ACTIVITY_SOCKETS.get(session_id):
                # Table went dark: free the channel for the next gathering.
                for ch, sid in list(_ACTIVITY_TABLES.items()):
                    if sid == session_id:
                        _ACTIVITY_TABLES.pop(ch, None)


class _ActivityTokenReq(BaseModel):
    code: str


@app.post("/api/token")
def activity_token(req: _ActivityTokenReq):
    """Exchange a Discord OAuth2 authorization `code` (from the Embedded App
    SDK's authorize() call) for an access token. The client secret stays on the
    server; the browser never sees it. See docs/ACTIVITY_SETUP.md."""
    client_id = os.getenv("ORACLE_DM_CLIENT_ID", "").strip()
    client_secret = os.getenv("ORACLE_DM_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Activity OAuth not configured (set ORACLE_DM_CLIENT_ID/SECRET in backend-cred.env).")
    resp = requests.post(
        "https://discord.com/api/oauth2/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "code": req.code,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    if resp.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"Discord token exchange failed: {resp.text[:200]}")
    return {"access_token": resp.json().get("access_token")}


_ACTIVITY_DIST = Path(__file__).resolve().parent.parent / "activity-ui" / "dist"
if _ACTIVITY_DIST.is_dir():
    app.mount("/activity", StaticFiles(directory=str(_ACTIVITY_DIST), html=True),
              name="activity")
    # The SPA is also served at "/" (see root()); its bundle uses relative
    # asset URLs (vite base "./"), which resolve to "/assets/..." from root, so
    # mount that too for the embedded-iframe path.
    _ACTIVITY_ASSETS = _ACTIVITY_DIST / "assets"
    if _ACTIVITY_ASSETS.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_ACTIVITY_ASSETS)),
                  name="activity-assets")
    print(f"[activity] serving UI from {_ACTIVITY_DIST} at /activity and /")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
