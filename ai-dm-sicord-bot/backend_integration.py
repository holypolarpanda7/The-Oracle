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


async def call_backend(message_text: str, session_id: str, user_id: str, username: str, backend_url: str) -> Dict:
    """Call the DM backend for a conversational reply.

    Returns a dict: {"reply": str, "music": Optional[str], "images": Optional[list]}
    where "music" is the AI-recommended ambient-music search query for the current
    scene (or None) and "images" is a list of scene-picture payloads (base64 WebP
    + metadata) for the bot to attach (or None).
    """
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "username": username,
        "message": message_text,
    }

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
                    }
                else:
                    return {"reply": f"The Oracle is troubled (HTTP {resp.status})...", "music": None, "images": None}
        except aiohttp.ClientError as e:
            print(f"[call_backend error] {e}")
            return {"reply": "The Oracle's connection falters...", "music": None, "images": None}
        except Exception as e:
            print(f"[call_backend error] {e}")
            return {"reply": "The Oracle is silent...", "music": None, "images": None}


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
