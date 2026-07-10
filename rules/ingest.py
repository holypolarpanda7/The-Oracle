"""
Seed the SRD rules tables from the open 5e-bits/5e-database dataset.

The dataset is Creative-Commons SRD content (CC-BY-4.0), so it's safe to store and
even redistribute with attribution — unlike the copyrighted rulebook PDFs. We pull
two bulk JSON files (monsters, spells), map them onto our structured tables, and
upsert by their stable ``index`` slug so re-running is idempotent.

    from rules.ingest import ingest_srd
    ingest_srd()                       # into the backend's oracle.db
    ingest_srd(database_url="sqlite:///./rules.db")
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import requests
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from .models import Monster, Spell

RAW_BASE = "https://raw.githubusercontent.com/5e-bits/5e-database/main/src/2014/en/"
MONSTERS_URL = RAW_BASE + "5e-SRD-Monsters.json"
SPELLS_URL = RAW_BASE + "5e-SRD-Spells.json"


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Default to the backend's ``oracle.db`` so rules live beside characters/world."""
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        database_url = f"sqlite:///{backend_db}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)


# ----- mapping helpers -----

def _fetch(url: str) -> list[dict]:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _join(value: Any) -> Optional[str]:
    """SRD descriptions come as list[str]; join into one string."""
    if value is None:
        return None
    if isinstance(value, list):
        return "\n\n".join(str(v) for v in value)
    return str(value)


def _parse_ac(armor_class: Any) -> tuple[Optional[int], Optional[str]]:
    """AC is a list like [{'type':'armor','value':15,'armor':[...]}] or an int."""
    if isinstance(armor_class, int):
        return armor_class, None
    if isinstance(armor_class, list) and armor_class:
        first = armor_class[0]
        if isinstance(first, dict):
            value = first.get("value")
            desc_bits = [str(a.get("type", "")) for a in armor_class if isinstance(a, dict)]
            desc = ", ".join(b for b in desc_bits if b) or None
            return value, desc
    return None, None


def _map_monster(m: dict) -> Monster:
    ac, ac_desc = _parse_ac(m.get("armor_class"))
    return Monster(
        index_slug=m["index"],
        name=m["name"],
        size=m.get("size"),
        type=m.get("type"),
        subtype=m.get("subtype"),
        alignment=m.get("alignment"),
        armor_class=ac,
        ac_desc=ac_desc,
        hit_points=m.get("hit_points"),
        hit_dice=m.get("hit_dice"),
        hit_points_roll=m.get("hit_points_roll"),
        strength=m.get("strength"),
        dexterity=m.get("dexterity"),
        constitution=m.get("constitution"),
        intelligence=m.get("intelligence"),
        wisdom=m.get("wisdom"),
        charisma=m.get("charisma"),
        challenge_rating=m.get("challenge_rating"),
        proficiency_bonus=m.get("proficiency_bonus"),
        xp=m.get("xp"),
        languages=m.get("languages"),
        speed=m.get("speed"),
        proficiencies=m.get("proficiencies"),
        senses=m.get("senses"),
        damage_vulnerabilities=m.get("damage_vulnerabilities"),
        damage_resistances=m.get("damage_resistances"),
        damage_immunities=m.get("damage_immunities"),
        condition_immunities=m.get("condition_immunities"),
        special_abilities=m.get("special_abilities"),
        actions=m.get("actions"),
        legendary_actions=m.get("legendary_actions"),
        raw=m,
    )


def _map_spell(sp: dict) -> Spell:
    school = sp.get("school") or {}
    dc = sp.get("dc") or {}
    dc_type = (dc.get("dc_type") or {}).get("name") if isinstance(dc, dict) else None
    classes = [c.get("name") for c in (sp.get("classes") or []) if isinstance(c, dict)]
    return Spell(
        index_slug=sp["index"],
        name=sp["name"],
        level=sp.get("level", 0),
        school=school.get("name") if isinstance(school, dict) else None,
        casting_time=sp.get("casting_time"),
        range=sp.get("range"),
        duration=sp.get("duration"),
        material=sp.get("material"),
        concentration=bool(sp.get("concentration", False)),
        ritual=bool(sp.get("ritual", False)),
        attack_type=sp.get("attack_type"),
        dc_type=dc_type,
        dc_success=dc.get("dc_success") if isinstance(dc, dict) else None,
        components=sp.get("components"),
        classes=classes,
        damage=sp.get("damage"),
        desc=_join(sp.get("desc")),
        higher_level=_join(sp.get("higher_level")),
        raw=sp,
    )


# ----- ingest -----

def _upsert(session: Session, model, index_slug: str, mapped) -> bool:
    """Insert or update by index_slug. Returns True if newly created."""
    existing = session.exec(select(model).where(model.index_slug == index_slug)).first()
    if existing:
        data = mapped.model_dump(exclude={"id", "created_at"})
        for k, v in data.items():
            setattr(existing, k, v)
        session.add(existing)
        return False
    session.add(mapped)
    return True


def ingest_srd(
    engine: Optional[Engine] = None,
    database_url: Optional[str] = None,
    *,
    monsters: bool = True,
    spells: bool = True,
) -> dict:
    """Download and upsert SRD monsters/spells. Returns counts."""
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)

    result = {"monsters_new": 0, "monsters_total": 0, "spells_new": 0, "spells_total": 0}

    if monsters:
        data = _fetch(MONSTERS_URL)
        with Session(engine) as s:
            for m in data:
                if _upsert(s, Monster, m["index"], _map_monster(m)):
                    result["monsters_new"] += 1
            s.commit()
        result["monsters_total"] = len(data)

    if spells:
        data = _fetch(SPELLS_URL)
        with Session(engine) as s:
            for sp in data:
                if _upsert(s, Spell, sp["index"], _map_spell(sp)):
                    result["spells_new"] += 1
            s.commit()
        result["spells_total"] = len(data)

    return result


if __name__ == "__main__":
    print(ingest_srd())
