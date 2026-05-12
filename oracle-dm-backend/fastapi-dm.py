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


class RegisterCharacterRequest(BaseModel):
    discord_user_id: str
    name: str
    race: Optional[str] = None
    char_class: Optional[str] = None
    level: int = 1
    stats: Optional[Dict[str, int]] = None
    ddb_url: Optional[str] = None
    avrae_import_text: Optional[str] = None
    approve: Optional[bool] = False
    home_region: Optional[str] = None
    source: Optional[str] = "manual"  # one of: 'avrae', 'guided', 'manual'


class CheckCharacterRequest(BaseModel):
    discord_user_id: str



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


class Character(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)

    # Identity
    discord_user_id: str = Field(sa_column=Column(String, nullable=False, index=True))
    avrae_id: Optional[str] = Field(default=None, sa_column=Column(String, index=True))

    # Basic character info
    name: str = Field(sa_column=Column(String, nullable=False))
    race: Optional[str] = Field(default=None, sa_column=Column(String))
    char_class: Optional[str] = Field(default=None, sa_column=Column(String))
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

def generate_dm_reply(session_id: str, username: str, message: str) -> str:
    """
    Main DM brain using OpenRouter.
    Builds a chat history and sends it to the model.
    """

    history = SESSIONS.get(session_id, [])

    messages: List[Dict[str, str]] = []

    # System prompt: describe DM behavior AND Avrae hooks
    system_prompt = (
        "You are an imaginative, fair, descriptive Dungeon Master for a Dungeons & Dragons-like "
        "tabletop roleplaying game. You narrate the world, describe outcomes of actions, and play NPCs.\n\n"
        "Tone & style:\n"
        "- Gritty, grounded fantasy, but still fun and playable.\n"
        "- 2–5 paragraphs per response, not a novel.\n"
        "- End most responses by asking what the players do next.\n\n"
        "Mechanics:\n"
        "- Do NOT roll player dice yourself.\n"
        "- When a PLAYER CHARACTER must roll (attack, skill check, saving throw, etc.), "
        "you suggest an Avrae command using this exact hook format:\n"
        "  [[AVRAE:!check dex save]]\n"
        "  [[AVRAE:!attack longsword]]\n"
        "- Only put the command inside the hook, no extra words.\n"
        "- Prefer to put the Avrae hook at the END of your message, on its own line.\n"
        "- For now, do NOT use any other hook types like [[ROLL:...]].\n"
        "- Assume players are using Avrae in Discord and will type the command you suggest.\n"
    )

    messages.append({"role": "system", "content": system_prompt})

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

    # Process Avrae hooks: turn [[AVRAE:...]] into human-readable instructions
    dm_final = render_avrae_hooks(dm_raw)

    return dm_final


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
        dm_text = generate_dm_reply(
            session_id=req.session_id,
            username=req.username,
            message=req.message,
        )
    except Exception as e:
        print(f"[DM error] {e}")
        dm_text = (
            "⚠ The Oracle strains to speak, but something interrupts its vision. "
            "Try again in a moment."
        )

    # Store DM turn
    history.append(Turn(role="dm", user="Oracle DM", content=dm_text))
    SESSIONS[req.session_id] = history

    return ChatResponse(reply=dm_text)

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

    # Ask the DM brain to produce an opening narration
    try:
        intro = generate_dm_reply(session_id=session_id, username=req.username, message="I enter the world.")
    except Exception as e:
        print(f"[enterworld DM error] {e}")
        intro = "The Oracle is silent for a moment. (failed to generate intro)"

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

    # Level validation: general rule 1-20; if guided, require level == 1
    if req.level < 1 or req.level > 20:
        raise HTTPException(status_code=400, detail="Invalid level (allowed: 1-20).")
    if req.source == "guided" and req.level != 1:
        raise HTTPException(status_code=400, detail="Guided creation must produce a level 1 character.")

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
    
    char_list = [{"id": c.id, "name": c.name, "level": c.level, "char_class": c.char_class, "race": c.race} for c in characters]
    return {"has_character": len(characters) > 0, "characters": char_list}
