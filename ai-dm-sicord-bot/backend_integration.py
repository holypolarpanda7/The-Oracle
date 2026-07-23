"""
Backend Integration Module - Handles all HTTP communication with the FastAPI backend.
"""
import aiohttp
from typing import Dict, List, Optional, Tuple


def _api_base(backend_url: str) -> str:
    """Derive the backend root (drops the trailing endpoint segment, e.g. /chat)."""
    return backend_url.rsplit("/", 1)[0]


async def check_character_in_db(user_id: str, check_url: str):
    """Check if a user has a character registered in the backend.
    Returns: (has_character: bool, characters: list)
    """
    payload = {"discord_user_id": user_id}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(check_url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("has_character", False), data.get("characters", [])
        except Exception as e:
            print(f"[check_character error] {e}")
    return False, []


async def get_activity_music_cue(backend_url: str, channel_id, since: int = 0) -> Optional[Dict]:
    """Poll the backend for the DM's latest scene music cue for a voice channel.

    Returns ``{"query": str|None, "seq": int}`` (query is None when there's
    nothing newer than ``since``), or None on transport error.
    """
    url = f"{_api_base(backend_url)}/activity/music/{channel_id}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, params={"since": since},
                                   timeout=aiohttp.ClientTimeout(total=6)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"[music cue poll error] {e}")
    return None


async def register_character_backend(payload: Dict, register_url: str) -> Dict:
    """POST to backend /register_character and return JSON result or error dict."""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(register_url, json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {"ok": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            print(f"[register_character_backend exception] {e}")
            return {"ok": False, "error": str(e)}


async def call_backend(message_text: str, session_id: str, user_id: str, username: str,
                       backend_url: str, *, private: bool = False) -> Dict:
    """Call the DM backend for a conversational reply.

    Returns a dict: {"reply", "music", "images", "memorial", "whisper", "public"}.
    "music" is the AI-recommended ambient-music query (or None); "images" is a list
    of scene-picture payloads; "whisper" is a list of {"user_id","text"} private
    notes to DM to specific players; "public" is the sanitized table-visible line on
    a secret turn. ``private=True`` marks a covert action (DM-the-bot secret input):
    the reply comes back private to the sender and only "public" is safe to post.
    """
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "username": username,
        "message": message_text,
        "private": private,
    }

    _err = lambda reply: {"reply": reply, "music": None, "images": None,
                          "memorial": None, "whisper": None, "public": None}

    # Generous timeout: the backend may synchronously render a scene image on the
    # local diffusion box, which can take longer than a plain text reply.
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(backend_url, json=payload, timeout=aiohttp.ClientTimeout(total=180)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        "reply": data.get("reply", "The Oracle is silent..."),
                        "music": data.get("music"),
                        "images": data.get("images"),
                        "memorial": data.get("memorial"),
                        "whisper": data.get("whisper"),
                        "public": data.get("public"),
                    }
                return _err(f"The Oracle is troubled (HTTP {resp.status})...")
        except aiohttp.ClientError as e:
            print(f"[call_backend error] {e}")
            return _err("The Oracle's connection falters...")
        except Exception as e:
            print(f"[call_backend error] {e}")
            return _err("The Oracle is silent...")


async def active_session_for_user(user_id: str, backend_url: str) -> Optional[str]:
    """Resolve the user's current live table session id (for the DM-the-bot secret
    path), or None if they aren't seated at a table right now."""
    url = f"{_api_base(backend_url)}/session/active/{user_id}"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return (await resp.json()).get("session_id")
        except Exception as e:
            print(f"[active_session_for_user error] {e}")
    return None


async def reset_backend_session(session_id: str, reset_url: str) -> str:
    """Reset (clear) a conversation session in the backend."""
    payload = {"session_id": session_id}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(reset_url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("message", "Session reset.")
                else:
                    return f"Failed to reset session (HTTP {resp.status})"
        except Exception as e:
            print(f"[reset_backend_session error] {e}")
            return "Error resetting session."


async def enter_world_backend(user_id: str, character_name: str, enter_url: str) -> Dict:
    """Call backend /enterworld endpoint."""
    payload = {
        "discord_user_id": user_id,
        "character_name": character_name
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(enter_url, json=payload) as resp:
                if resp.status == 200:
                    return await resp.json()
                else:
                    return {"ok": False, "error": f"HTTP {resp.status}"}
        except Exception as e:
            print(f"[enter_world_backend error] {e}")
            return {"ok": False, "error": str(e)}


async def enter_world_session(
    user_id: str, username: str, guild_id: str, character_name: str, enter_url: str
) -> Dict:
    """Start a play session for a specific character via the backend /enterworld.

    Sends the fields the backend actually expects (``user_id``/``username``/
    ``guild_id``/``character_name``) and returns the full response, including the
    backend ``session_id`` (used for every subsequent turn so the character stays
    bound), the opening ``intro`` narration, and any opening ``music`` cue.
    """
    payload = {
        "user_id": user_id,
        "username": username,
        "guild_id": guild_id,
        "character_name": character_name,
    }
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                enter_url, json=payload, timeout=aiohttp.ClientTimeout(total=180)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                return {"status": "error", "error": f"HTTP {resp.status}"}
        except Exception as e:
            print(f"[enter_world_session error] {e}")
            return {"status": "error", "error": str(e)}


# ==================== Character sheet / inventory / portrait ====================

async def resolve_character(
    user_id: str, check_url: str, name: Optional[str] = None
) -> Tuple[Optional[dict], List[dict]]:
    """Find the player's character (by name, else the only/first one).

    Returns ``(chosen_or_None, all_characters)``.
    """
    _, characters = await check_character_in_db(user_id, check_url)
    if not characters:
        return None, []
    if name:
        low = name.strip().lower()
        for c in characters:
            if str(c.get("name", "")).lower() == low:
                return c, characters
        for c in characters:
            if low in str(c.get("name", "")).lower():
                return c, characters
        return None, characters
    return characters[0], characters


async def import_ddb_character(user_id: str, url: str, backend_url: str) -> Dict:
    """POST /import_ddb: import + validate a public D&D Beyond sheet.

    Returns the backend payload ({status, character_id, name, report}) or
    {"ok": False, "error": <player-readable reason>}.
    """
    api = f"{_api_base(backend_url)}/import_ddb"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                api, json={"discord_user_id": user_id, "url": url},
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status == 200:
                    return data
                return {"ok": False,
                        "error": data.get("detail", f"HTTP {resp.status}")}
        except Exception as e:
            print(f"[import_ddb_character error] {e}")
            return {"ok": False, "error": "Could not reach the Oracle's backend."}


async def get_character_sheet(character_id: int, backend_url: str) -> Optional[dict]:
    """GET the rendered character sheet from the backend."""
    url = f"{_api_base(backend_url)}/character/{character_id}/sheet"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.json()
                print(f"[get_character_sheet] HTTP {resp.status}")
        except Exception as e:
            print(f"[get_character_sheet error] {e}")
    return None


async def get_inventory(character_id: int, backend_url: str) -> Optional[dict]:
    """GET the character's inventory list from the backend."""
    url = f"{_api_base(backend_url)}/character/{character_id}/inventory"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.json()
                print(f"[get_inventory] HTTP {resp.status}")
        except Exception as e:
            print(f"[get_inventory error] {e}")
    return None


async def get_portrait(character_id: int, backend_url: str) -> Optional[dict]:
    """GET the character's current portrait payload, or None if none stored."""
    url = f"{_api_base(backend_url)}/character/{character_id}/portrait"
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            print(f"[get_portrait error] {e}")
    return None


async def generate_portrait(
    character_id: int, backend_url: str, *, description: str = "", look: str = ""
) -> Dict:
    """Ask the backend to generate/regenerate a portrait. Portrait rendering can be
    slow on the local diffusion box, so use a generous timeout."""
    url = f"{_api_base(backend_url)}/character/{character_id}/portrait/generate"
    payload = {"character_id": character_id, "description": description, "look": look}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=300)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                detail = await _error_detail(resp)
                return {"error": detail, "status": resp.status}
        except Exception as e:
            print(f"[generate_portrait error] {e}")
            return {"error": str(e)}


async def upload_portrait(
    character_id: int, backend_url: str, b64_image: str, *, caption: str = ""
) -> Dict:
    """Upload a player-supplied portrait (base64-encoded image bytes)."""
    url = f"{_api_base(backend_url)}/character/{character_id}/portrait/upload"
    payload = {"character_id": character_id, "b64": b64_image, "caption": caption}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                url, json=payload, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                detail = await _error_detail(resp)
                return {"error": detail, "status": resp.status}
        except Exception as e:
            print(f"[upload_portrait error] {e}")
            return {"error": str(e)}


async def _error_detail(resp) -> str:
    """Best-effort extraction of a FastAPI error detail string."""
    try:
        data = await resp.json()
        if isinstance(data, dict) and data.get("detail"):
            return str(data["detail"])
    except Exception:
        pass
    return f"HTTP {resp.status}"
