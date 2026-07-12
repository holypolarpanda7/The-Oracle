"""
Homebrew / owned-content ingest — YOUR books, typed in by YOU.

The rules DB ships with the open SRD (CC-BY-4.0) only. Content from books you
own (Tasha's, Xanathar's, ...) can be used at your own table — but it has to
enter the database from YOUR copy, not from an AI's memory: reproduced-from-
memory stats would be both legally murky and, worse, silently WRONG.

Drop a ``rules/homebrew.json`` next to this file (see homebrew.sample.json for
the shape) and it loads at backend startup, upserting into the same
``rules_item`` table the SRD uses — so shops, guardrails, crafting, and the
DM's exact-numbers injection all pick your entries up automatically.

Minimal item entry:
    {"name": "...", "rarity": "rare", "desc": "what it does", "cost_gp": 500}
Everything else (category, item_type, requires_attunement, weapon/armor
numbers) is optional and mirrors the Item model's fields.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from sqlmodel import Session, select

from .ingest import get_engine
from .models import Item

HOMEBREW_PATH = Path(__file__).resolve().parent / "homebrew.json"
HOMEBREW_SOURCE = "homebrew"

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name: str) -> str:
    return _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-") or "item"


# JSON keys accepted verbatim onto the Item row.
_ITEM_FIELDS = {
    "name", "category", "item_type", "cost_gp", "weight",
    "damage_dice", "damage_type", "two_handed_damage_dice",
    "range_normal", "range_long", "properties",
    "armor_class_base", "armor_dex_bonus", "armor_max_dex_bonus",
    "str_minimum", "stealth_disadvantage",
    "rarity", "requires_attunement", "desc",
}


def load_homebrew(path: Optional[Path] = None, engine=None) -> dict:
    """Upsert homebrew items from JSON into the rules DB. Returns counts.

    Idempotent: entries are keyed by a ``hb-`` slug, so editing the file and
    restarting updates rows in place. Never raises — a malformed file logs
    and loads nothing rather than breaking startup.
    """
    path = Path(path) if path else HOMEBREW_PATH
    out = {"items_new": 0, "items_updated": 0, "skipped": 0}
    if not path.is_file():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[homebrew] could not parse {path.name}: {e}")
        return out

    engine = engine or get_engine()
    entries = data.get("items") or []
    with Session(engine) as s:
        for raw in entries:
            name = (raw.get("name") or "").strip()
            if not name or not (raw.get("desc") or "").strip():
                out["skipped"] += 1
                continue
            slug = f"hb-{_slug(name)}"
            fields = {k: v for k, v in raw.items() if k in _ITEM_FIELDS}
            existing = s.exec(select(Item).where(Item.index_slug == slug)).first()
            if existing:
                for k, v in fields.items():
                    setattr(existing, k, v)
                existing.source = HOMEBREW_SOURCE
                s.add(existing)
                out["items_updated"] += 1
            else:
                s.add(Item(index_slug=slug, source=HOMEBREW_SOURCE,
                           category=raw.get("category") or "magic-item",
                           **{k: v for k, v in fields.items() if k != "category"}))
                out["items_new"] += 1
        s.commit()
    if out["items_new"] or out["items_updated"]:
        print(f"[homebrew] loaded {path.name}: {out['items_new']} new, "
              f"{out['items_updated']} updated"
              + (f", {out['skipped']} skipped (need name+desc)" if out["skipped"] else ""))
    return out
