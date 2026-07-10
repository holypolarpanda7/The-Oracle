"""
Backend Integration Module - Handles all HTTP communication with the FastAPI backend.
"""
import aiohttp
from typing import Dict


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

    Returns a dict: {"reply": str, "music": Optional[str]} where "music" is the
    AI-recommended ambient-music search query for the current scene (or None).
    """
    payload = {
        "session_id": session_id,
        "user_id": user_id,
        "username": username,
        "message": message_text,
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(backend_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {"reply": data.get("reply", "The Oracle is silent..."), "music": data.get("music")}
                else:
                    return {"reply": f"The Oracle is troubled (HTTP {resp.status})...", "music": None}
        except aiohttp.ClientError as e:
            print(f"[call_backend error] {e}")
            return {"reply": "The Oracle's connection falters...", "music": None}
        except Exception as e:
            print(f"[call_backend error] {e}")
            return {"reply": "The Oracle is silent...", "music": None}


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
