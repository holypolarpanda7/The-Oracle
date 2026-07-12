"""
D&D Beyond character import — no Avrae in the loop.

DDB has no official API, but PUBLIC character sheets are served as JSON from
``character-service.dndbeyond.com``. We fetch, parse the v5 payload down to
The Oracle's own Character shape, and run a validation pass:

  * expected features present (name, race, class, ability scores, HP basis) —
    anything absent lands in ``report["missing"]`` for the AI DM to follow up;
  * world rules enforced — characters enter at LEVEL 1 (higher DDB levels are
    normalized and reported), ability scores capped at the level-1 legal max,
    magic/homebrew/high-rarity gear dropped and reported;
  * extras dropped are itemized in ``report["dropped"]`` so the DM can
    mention them instead of silently eating them.

The character must be set to PUBLIC on D&D Beyond; private sheets 403.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import requests

_DDB_SERVICE = "https://character-service.dndbeyond.com/character/v5/character/{cid}"
_URL_RE = re.compile(
    r"(?:dndbeyond\.com/characters?|ddb\.ac/characters?)/(\d+)", re.IGNORECASE)

# DDB stat ids, in order.
_ABILITIES = ["strength", "dexterity", "constitution",
              "intelligence", "wisdom", "charisma"]
_SUBTYPE_TO_ABILITY = {f"{a}-score": a for a in _ABILITIES}

# Level-1 legality: 15 base + 2 racial is the standard ceiling.
MAX_STAT_AT_LEVEL_1 = 17
# Item rarities a level-1 character may keep. Everything else is dropped.
ALLOWED_RARITIES = {None, "", "common"}

SRD_CLASSES = {"barbarian", "bard", "cleric", "druid", "fighter", "monk",
               "paladin", "ranger", "rogue", "sorcerer", "warlock", "wizard"}


class DDBImportError(RuntimeError):
    """User-presentable import failure (bad URL, private sheet, ...)."""


def extract_character_id(text: str) -> Optional[str]:
    m = _URL_RE.search(text or "")
    return m.group(1) if m else None


def fetch_ddb_json(character_id: str, *, timeout: int = 20) -> dict:
    url = _DDB_SERVICE.format(cid=character_id)
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/125.0 Safari/537.36"),
            "Accept": "application/json",
        })
    except requests.RequestException as e:
        raise DDBImportError(f"Could not reach D&D Beyond: {e}") from e
    if resp.status_code == 403:
        raise DDBImportError(
            "D&D Beyond refused access — the character sheet must be set to "
            "PUBLIC (Character Privacy on its DDB settings page).")
    if resp.status_code == 404:
        raise DDBImportError("No D&D Beyond character with that id was found.")
    if resp.status_code != 200:
        raise DDBImportError(f"D&D Beyond returned HTTP {resp.status_code}.")
    body = resp.json()
    data = body.get("data", body)
    if not isinstance(data, dict) or not data.get("name"):
        raise DDBImportError("Unrecognized D&D Beyond response shape.")
    return data


# Standard 27-point buy costs (base scores 8-15).
_POINT_BUY_COST = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
POINT_BUY_BUDGET = 27
MAX_BASE_STAT = 15  # highest base score point-buy or the standard array allows


def _compute_stats(data: dict) -> tuple[dict[str, int], dict[str, int], bool]:
    """Return (final_scores, base_scores_pre_bonus, override_used)."""
    base = {i + 1: 10 for i in range(6)}
    for row in data.get("stats") or []:
        if row.get("id") in base and row.get("value") is not None:
            base[row["id"]] = int(row["value"])
    bonus = {i + 1: 0 for i in range(6)}
    for row in data.get("bonusStats") or []:
        if row.get("id") in bonus and row.get("value"):
            bonus[row["id"]] += int(row["value"])
    # Racial/feat/background ability bonuses live in the modifiers block.
    for source in (data.get("modifiers") or {}).values():
        for mod in source or []:
            if mod.get("type") != "bonus":
                continue
            ability = _SUBTYPE_TO_ABILITY.get(str(mod.get("subType") or ""))
            if ability and mod.get("value"):
                bonus[_ABILITIES.index(ability) + 1] += int(mod["value"])
    override = {}
    for row in data.get("overrideStats") or []:
        if row.get("id") and row.get("value"):
            override[row["id"]] = int(row["value"])
    final: dict[str, int] = {}
    base_named: dict[str, int] = {}
    for i, name in enumerate(_ABILITIES, start=1):
        base_named[name] = base[i]
        final[name] = override.get(i, base[i] + bonus[i])
    return final, base_named, bool(override)


def parse_ddb(data: dict) -> dict:
    """DDB v5 JSON -> a neutral character dict (not yet validated)."""
    classes = data.get("classes") or []
    primary = classes[0] if classes else {}
    cdef = primary.get("definition") or {}
    sdef = primary.get("subclassDefinition") or {}
    total_level = sum(int(c.get("level") or 0) for c in classes) or 1

    items = []
    for it in data.get("inventory") or []:
        idef = it.get("definition") or {}
        items.append({
            "name": idef.get("name") or "unknown item",
            "quantity": int(it.get("quantity") or 1),
            "rarity": (idef.get("rarity") or "").strip().lower() or None,
            "magic": bool(idef.get("magic")),
            "homebrew": bool(idef.get("isHomebrew")),
            "attunement": bool(idef.get("canAttune") or idef.get("requiresAttunement")),
        })

    spells: list[str] = []
    for cs in data.get("classSpells") or []:
        for sp in cs.get("spells") or []:
            n = ((sp.get("definition") or {}).get("name"))
            if n:
                spells.append(n)
    for group in (data.get("spells") or {}).values():
        for sp in group or []:
            n = ((sp.get("definition") or {}).get("name"))
            if n:
                spells.append(n)

    race = data.get("race") or {}
    background = ((data.get("background") or {}).get("definition") or {})
    final_stats, base_stats, override_used = _compute_stats(data)
    return {
        "name": (data.get("name") or "").strip(),
        "race": race.get("fullName") or race.get("baseName"),
        "char_class": cdef.get("name"),
        "subclass": sdef.get("name"),
        "ddb_level": total_level,
        "multiclass": [c.get("definition", {}).get("name") for c in classes[1:]],
        "stats": final_stats,
        "base_stats": base_stats,
        "stats_overridden": override_used,
        "background": background.get("name"),
        "items": items,
        "spells": sorted(set(spells)),
        "avatar_url": data.get("decorations", {}).get("avatarUrl")
        or data.get("avatarUrl"),
    }


def validate_for_world(parsed: dict) -> tuple[dict, dict]:
    """Enforce world rules on a parsed DDB character.

    Returns ``(normalized, report)`` where report = {missing, dropped,
    warnings} in player-readable strings. The AI DM follows up on ``missing``
    and mentions ``dropped``.
    """
    missing: list[str] = []
    dropped: list[str] = []
    warnings: list[str] = []
    out = dict(parsed)

    if not out.get("name"):
        missing.append("a character name")
    if not out.get("race"):
        missing.append("a race")
    cls = (out.get("char_class") or "").strip()
    if not cls:
        missing.append("a class")
    elif cls.lower() not in SRD_CLASSES:
        warnings.append(f"class '{cls}' is not an SRD class — the DM may adapt it")
    stats = out.get("stats") or {}
    if not stats or all(v == 10 for v in stats.values()):
        missing.append("ability scores (the sheet shows none, or all 10s)")

    # World rule: everyone starts at level 1 and advances in-system.
    if int(out.get("ddb_level") or 1) > 1:
        dropped.append(
            f"levels above 1 (sheet was level {out['ddb_level']}; this world "
            "starts every tale at level 1 — earn it back in play)")
    out["level"] = 1
    if out.get("multiclass"):
        extra = ", ".join(c for c in out["multiclass"] if c)
        if extra:
            dropped.append(f"multiclass levels in {extra} (single class at level 1)")
    if out.get("subclass"):
        dropped.append(f"subclass '{out['subclass']}' (chosen in-system at the "
                       "class's subclass level)")
        out["subclass"] = None

    # Point-buy legality on the BASE scores (before racial/feat bonuses).
    base = dict(out.get("base_stats") or {})
    if out.get("stats_overridden"):
        warnings.append("the sheet uses manual stat overrides — point-buy "
                        "legality can't be verified; the DM may ask about it")
    elif base:
        base_capped = {}
        for k, v in base.items():
            if int(v) > MAX_BASE_STAT:
                base_capped[k] = int(v)
                # Pull the final score down by the same amount the base loses.
                stats[k] = int(stats.get(k, v)) - (int(v) - MAX_BASE_STAT)
                base[k] = MAX_BASE_STAT
        if base_capped:
            pretty = ", ".join(f"{k} base {v}->{MAX_BASE_STAT}"
                               for k, v in base_capped.items())
            dropped.append(f"base scores above the point-buy/array max ({pretty})")
        cost = sum(_POINT_BUY_COST.get(max(8, int(v)), 0) for v in base.values())
        if cost > POINT_BUY_BUDGET:
            warnings.append(
                f"ability scores cost {cost} points (budget {POINT_BUY_BUDGET}) "
                "— rolled stats? The DM may ask how they were determined")
    out["base_stats"] = base

    # Level-1 legal ability ceiling (base + racial).
    capped = {}
    for k, v in stats.items():
        v = int(v)
        if v > MAX_STAT_AT_LEVEL_1:
            capped[k] = v
            v = MAX_STAT_AT_LEVEL_1
        if v < 1:
            v = 1
        stats[k] = v
    if capped:
        pretty = ", ".join(f"{k} {v}->{MAX_STAT_AT_LEVEL_1}" for k, v in capped.items())
        dropped.append(f"over-cap ability scores ({pretty}; level-1 max is "
                       f"{MAX_STAT_AT_LEVEL_1})")
    out["stats"] = stats

    # Gear: no magic/homebrew/high-rarity items at level 1.
    kept_items: list[dict] = []
    for it in out.get("items") or []:
        reason = None
        if it.get("homebrew"):
            reason = "homebrew"
        elif it.get("rarity") not in ALLOWED_RARITIES:
            reason = f"rarity: {it['rarity']}"
        elif it.get("magic"):
            reason = "magic item"
        if reason:
            dropped.append(f"{it['name']} ({reason})")
        else:
            kept_items.append(it)
    out["items"] = kept_items
    if not kept_items:
        warnings.append("no mundane equipment survived import — the standard "
                        "class starting kit will be issued")

    # Spells beyond level-1 slots are the class tables' problem; just flag bulk.
    if len(out.get("spells") or []) > 12:
        warnings.append(f"{len(out['spells'])} spells on the sheet — only "
                        "level-1-legal choices will matter until leveled")

    report = {"missing": missing, "dropped": dropped, "warnings": warnings}
    return out, report


def import_from_url(url_or_id: str) -> tuple[dict, dict]:
    """One-call convenience: URL/id -> (normalized character, report)."""
    cid = extract_character_id(url_or_id) or (
        url_or_id if url_or_id.strip().isdigit() else None)
    if not cid:
        raise DDBImportError(
            "That doesn't look like a D&D Beyond character link — expected "
            "something like https://www.dndbeyond.com/characters/12345678.")
    data = fetch_ddb_json(cid)
    return validate_for_world(parse_ddb(data))
