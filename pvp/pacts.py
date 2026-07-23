"""Sanctioned-duel pacts — the authorization layer for player-vs-player conflict.

A pact is an explicit, server-recorded agreement between two PCs to fight under
agreed terms. While an active pact covers a pair, their clash is a FAIR DUEL and
the divine/curse consequences of murder never fire. Everything here is pure over a
JSON-serializable ``pvp_state`` dict that lives in the session meta (``meta["pvp"]``)
— the same no-DB pattern as games/puzzles. The authoritative way a pact opens is the
Accept/Decline handshake; the DM may also open one when it reads clear mutual assent.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Duel terms, from least to most lethal.
TERMS = ("first-blood", "to-yield", "to-the-death")
_TERM_ALIASES = {
    "first blood": "first-blood", "firstblood": "first-blood", "blood": "first-blood",
    "yield": "to-yield", "to yield": "to-yield", "submission": "to-yield",
    "death": "to-the-death", "to the death": "to-the-death", "death-match": "to-the-death",
    "to-death": "to-the-death",
}


def new_state() -> Dict[str, Any]:
    return {"pacts": [], "offenses": {}}


def normalize_terms(raw: str) -> str:
    r = (raw or "").strip().lower()
    if r in TERMS:
        return r
    return _TERM_ALIASES.get(r, "to-yield")


def _key(name: str) -> str:
    return (name or "").strip().lower()


def _same_pair(p: dict, a: str, b: str) -> bool:
    pa, pb, a, b = _key(p.get("a")), _key(p.get("b")), _key(a), _key(b)
    return {pa, pb} == {a, b}


def open_pact(state: Dict[str, Any], a: str, b: str, terms: str, turn: int,
              ttl: Optional[int] = None) -> dict:
    """Open (or refresh) an active pact between two PCs. Returns the pact."""
    pacts: List[dict] = state.setdefault("pacts", [])
    for p in pacts:
        if _same_pair(p, a, b) and p.get("status") == "active":
            p["terms"] = normalize_terms(terms)
            return p
    pact = {
        "a": (a or "").strip(), "b": (b or "").strip(),
        "terms": normalize_terms(terms), "opened_turn": int(turn),
        "expires_turn": (int(turn) + int(ttl)) if ttl else None,
        "status": "active",
    }
    pacts.append(pact)
    return pact


def authorized(state: Dict[str, Any], a: str, b: str,
               turn: Optional[int] = None) -> Optional[dict]:
    """The active pact covering this pair (order-insensitive), or None.

    A pair whose pact has lapsed past ``expires_turn`` is treated as unauthorized."""
    for p in state.get("pacts", []):
        if p.get("status") != "active" or not _same_pair(p, a, b):
            continue
        exp = p.get("expires_turn")
        if turn is not None and exp is not None and int(turn) > int(exp):
            continue
        return p
    return None


def close_pact(state: Dict[str, Any], a: str, b: str,
               status: str = "resolved") -> Optional[dict]:
    for p in state.get("pacts", []):
        if _same_pair(p, a, b) and p.get("status") == "active":
            p["status"] = status
            return p
    return None


def active_pacts(state: Dict[str, Any]) -> List[dict]:
    return [p for p in state.get("pacts", []) if p.get("status") == "active"]


def record_offense(state: Dict[str, Any], killer: str) -> int:
    """Tally an unsanctioned kill by ``killer``; returns the running count (incl. this)."""
    off = state.setdefault("offenses", {})
    off[_key(killer)] = int(off.get(_key(killer), 0)) + 1
    return off[_key(killer)]


def offense_count(state: Dict[str, Any], killer: str) -> int:
    return int(state.get("offenses", {}).get(_key(killer), 0))


def describe_pacts(state: Dict[str, Any]) -> str:
    """One-line-per-pact summary for the DM context block."""
    act = active_pacts(state)
    if not act:
        return ""
    return "\n".join(f"- **{p['a']}** vs **{p['b']}** — sanctioned duel ({p['terms']})"
                     for p in act)
