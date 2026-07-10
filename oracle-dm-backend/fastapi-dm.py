import os
import re
from pathlib import Path
from typing import Dict, List, Literal, TypedDict, Optional
import uuid
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from dotenv import load_dotenv

# Database (SQLModel)
from sqlmodel import SQLModel, Field, create_engine, Session, select
from sqlalchemy import Column, JSON, String
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
from rules import RulesLibrary, format_monster_brief, format_spell_brief, ingest_srd, seed_classes_and_subclasses, level_up_report
from rules.models import Subclass, DndClass
from combat import CombatTracker, Condition
from dice import ability_check, ability_modifier, roll as dice_roll

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

    # Seed classes + subclasses (offline; includes owned non-SRD like Bladesinger).
    try:
        with Session(engine) as _s:
            has_classes = _s.exec(select(Subclass)).first() is not None
        if not has_classes:
            cls_counts = seed_classes_and_subclasses(engine=engine)
            print(f"[Startup] Seeded classes/subclasses: {cls_counts}")
    except Exception as e:
        print(f"[Startup] Class seed skipped: {e}")

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


SESSIONS: Dict[str, List[Turn]] = {}


# Database engine (default: SQLite in project dir). Change DATABASE_URL to a Postgres URL for production.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'oracle.db'}")
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)

# Shared subsystems on the same DB: persistent world graph + SRD rules reference.
world = WorldGraph(engine=engine)
rules_lib = RulesLibrary(engine=engine)
# Initiative-ordered combat state tracker (PCs, NPCs, monsters).
combat = CombatTracker(engine=engine)

# Per-session metadata (which PC is playing) alongside the in-memory history.
SESSION_META: Dict[str, Dict] = {}


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


# ----- OpenRouter LLM call -----

def call_openrouter_dm(messages: List[Dict[str, str]]) -> str:
    """
    Call OpenRouter's chat completion endpoint with the given messages.
    Returns the assistant's reply text.
    """
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

    resp = requests.post(OPENROUTER_BASE_URL, headers=headers, json=payload, timeout=60)
    if resp.status_code != 200:
        print(f"[OpenRouter error] HTTP {resp.status_code}: {resp.text}")
        raise RuntimeError("LLM call failed")

    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"[OpenRouter parse error] {e} | data={data}")
        raise RuntimeError("Failed to parse LLM response")


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

    history = SESSIONS.get(session_id, [])

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
        "  totals; narrate the fight around them and don't contradict the numbers.\n\n"
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

    messages.append({"role": "system", "content": system_prompt})

    # Grounding context (world slice, rules briefs).
    for block in (extra_context or []):
        if block and block.strip():
            messages.append({"role": "system", "content": block})

    # Previous turns
    for turn in history[-12:]:
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


def assemble_context(session_id: str, message: str):
    """Build grounding context for a turn: the local world slice + referenced rules.

    Returns ``(world_context_or_None, [text_blocks])``.
    """
    ctx_obj = None
    texts: List[str] = []

    meta = SESSION_META.get(session_id)
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
        hist = SESSIONS.get(session_id, [])
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

    return ctx_obj, texts


# ----- Routes -----

@app.get("/")
async def root():
    return {"status": "ok", "message": "Oracle DM backend with OpenRouter + Avrae hooks running"}


@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    """
    Main entry point from the Discord bot.
    Tracks per-session history and asks the DM brain for a reply.
    """

    history = SESSIONS.setdefault(req.session_id, [])

    # Player turn
    history.append(Turn(role="player", user=req.username, content=req.message))

    # DM reply
    try:
        # Ground the turn in the local world slice, referenced rules, and — if a
        # fight is underway — the live initiative/HP board.
        _, ctx_texts = assemble_context(req.session_id, req.message)
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

    # Store DM turn
    history.append(Turn(role="dm", user="Oracle DM", content=dm_text))
    SESSIONS[req.session_id] = history

    return ChatResponse(reply=dm_text, music=music_query)

@app.post("/reset")
async def reset_endpoint(req: ResetRequest):
    """
    Reset the story/session for a given session_id (guild:channel).
    Called by the Discord bot when !resetdm is used.
    """
    if req.session_id in SESSIONS:
        del SESSIONS[req.session_id]
        print(f"[SESSION RESET] {req.session_id}")
        return {"status": "ok", "message": f"Session {req.session_id} reset."}
    else:
        return {"status": "ok", "message": f"Session {req.session_id} did not exist (nothing to reset)."}


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

    # Seed session history with an initial player 'enter' turn
    SESSIONS[session_id] = [Turn(role="player", user=req.username, content="ENTER_WORLD")]

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
    SESSION_META[session_id] = {
        "pc_slug": pc_slug,
        "user_id": req.user_id,
        "character_name": chosen.name,
    }

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
    SESSIONS[session_id].append(Turn(role="dm", user="Oracle DM", content=intro))

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

        char = Character(
            discord_user_id=req.discord_user_id,
            name=req.name,
            race=req.race,
            char_class=req.char_class,
            subclass=req.subclass,
            level=req.level,
            stats=req.stats,
            ddb_url=req.ddb_url,
            avrae_import_text=req.avrae_import_text,
            approved=approved_flag,
            home_region=req.home_region,
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8000")))
