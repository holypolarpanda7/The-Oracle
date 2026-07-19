"""
CombatTracker — manage an initiative-ordered encounter and its combatants.

Every creature in the fight (PCs, NPCs, monsters) is a row in ``combat_combatant``.
Monster combatants are hydrated straight from the SRD ``rules_monster`` table so
their HP/AC/DEX are exact. The tracker rolls initiative, advances turns/rounds,
applies damage & healing, and renders a compact board the DM brain can read.

    from combat import CombatTracker
    ct = CombatTracker(database_url="sqlite:///./oracle.db")
    ct.create_tables()
    enc = ct.start_encounter("guild:chan", "Ambush on the road")
    ct.add_pc(enc.id, character_id=1, name="Lyra", max_hp=11, armor_class=13, dex_mod=3)
    ct.add_from_monster(enc.id, "goblin", count=2)
    ct.roll_initiative(enc.id)
    print(ct.render(enc.id))
"""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from dice import roll as dice_roll, ability_modifier
from rules.models import Monster

from .models import Encounter, Combatant, CombatantKind


def _default_engine(database_url: Optional[str] = None) -> Engine:
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        database_url = f"sqlite:///{backend_db}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)


class CombatTracker:
    def __init__(self, engine: Optional[Engine] = None, database_url: Optional[str] = None):
        self.engine = engine or _default_engine(database_url)

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine)

    # ----- encounters -----

    def start_encounter(self, session_id: str, name: str = "Encounter") -> Encounter:
        """Begin a new encounter, ending any still-active one for this session."""
        with Session(self.engine) as s:
            for enc in s.exec(
                select(Encounter).where(
                    Encounter.session_id == session_id, Encounter.active == True  # noqa: E712
                )
            ).all():
                enc.active = False
                s.add(enc)
            enc = Encounter(session_id=session_id, name=name)
            s.add(enc)
            s.commit()
            s.refresh(enc)
            return enc

    def get_active(self, session_id: str) -> Optional[Encounter]:
        with Session(self.engine) as s:
            return s.exec(
                select(Encounter).where(
                    Encounter.session_id == session_id, Encounter.active == True  # noqa: E712
                )
            ).first()

    def get_encounter(self, encounter_id: int) -> Optional[Encounter]:
        with Session(self.engine) as s:
            return s.get(Encounter, encounter_id)

    def end_encounter(self, encounter_id: int) -> Optional[Encounter]:
        with Session(self.engine) as s:
            enc = s.get(Encounter, encounter_id)
            if enc:
                enc.active = False
                s.add(enc)
                s.commit()
                s.refresh(enc)
            return enc

    # ----- combatants -----

    def add_combatant(
        self,
        encounter_id: int,
        name: str,
        *,
        kind: str = CombatantKind.MONSTER,
        max_hp: int = 1,
        armor_class: Optional[int] = None,
        dex_mod: int = 0,
        initiative: int = 0,
        character_id: Optional[int] = None,
        monster_slug: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Combatant:
        with Session(self.engine) as s:
            c = Combatant(
                encounter_id=encounter_id, name=name, kind=kind,
                max_hp=max(1, max_hp), current_hp=max(1, max_hp), temp_hp=0,
                armor_class=armor_class, dex_mod=dex_mod, initiative=initiative,
                character_id=character_id, monster_slug=monster_slug,
                conditions=[], notes=notes,
            )
            s.add(c)
            s.commit()
            s.refresh(c)
            return c

    def add_pc(
        self,
        encounter_id: int,
        *,
        name: str,
        max_hp: int,
        armor_class: Optional[int] = None,
        dex_mod: int = 0,
        character_id: Optional[int] = None,
        initiative: int = 0,
    ) -> Combatant:
        return self.add_combatant(
            encounter_id, name, kind=CombatantKind.PC, max_hp=max_hp,
            armor_class=armor_class, dex_mod=dex_mod, initiative=initiative,
            character_id=character_id,
        )

    def add_from_monster(
        self,
        encounter_id: int,
        slug: str,
        *,
        count: int = 1,
        roll_hp: bool = False,
        rng: Optional[random.Random] = None,
    ) -> list[Combatant]:
        """Add ``count`` copies of an SRD monster, hydrated from ``rules_monster``."""
        rng = rng or random
        with Session(self.engine) as s:
            mon = s.exec(select(Monster).where(Monster.index_slug == slug)).first()
            if mon is None:
                raise ValueError(f"Unknown monster slug: {slug!r}")
            dex_mod = ability_modifier(mon.dexterity)
            created: list[Combatant] = []
            for i in range(max(1, count)):
                if roll_hp and mon.hit_points_roll:
                    try:
                        hp = max(1, dice_roll(mon.hit_points_roll).total)
                    except Exception:
                        hp = mon.hit_points or 1
                else:
                    hp = mon.hit_points or 1
                label = mon.name if count == 1 else f"{mon.name} {i + 1}"
                c = Combatant(
                    encounter_id=encounter_id, name=label, kind=CombatantKind.MONSTER,
                    max_hp=hp, current_hp=hp, temp_hp=0,
                    armor_class=mon.armor_class, dex_mod=dex_mod,
                    monster_slug=mon.index_slug, conditions=[],
                )
                s.add(c)
                created.append(c)
            s.commit()
            for c in created:
                s.refresh(c)
            return created

    def get_combatant(self, combatant_id: int) -> Optional[Combatant]:
        with Session(self.engine) as s:
            return s.get(Combatant, combatant_id)

    def remove_combatant(self, combatant_id: int) -> bool:
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                return False
            s.delete(c)
            s.commit()
            return True

    def _combatants(self, s: Session, encounter_id: int) -> list[Combatant]:
        return list(
            s.exec(select(Combatant).where(Combatant.encounter_id == encounter_id)).all()
        )

    def order(self, encounter_id: int) -> list[Combatant]:
        """Initiative order: initiative desc, then DEX mod desc, then id."""
        with Session(self.engine) as s:
            combatants = self._combatants(s, encounter_id)
        return sorted(
            combatants,
            key=lambda c: (-c.initiative, -c.dex_mod, c.id or 0),
        )

    # ----- initiative & turns -----

    def roll_initiative(
        self,
        encounter_id: int,
        *,
        reroll: bool = False,
        rng: Optional[random.Random] = None,
        reset_turn: bool = True,
    ) -> list[Combatant]:
        """Roll d20 + DEX mod for combatants (only those unset unless ``reroll``).

        ``reset_turn=False`` keeps the current round/turn — for rolling in
        mid-fight reinforcements without restarting the fight."""
        rng = rng or random
        with Session(self.engine) as s:
            combatants = self._combatants(s, encounter_id)
            for c in combatants:
                if reroll or not c.initiative:
                    c.initiative = rng.randint(1, 20) + c.dex_mod
                    s.add(c)
            enc = s.get(Encounter, encounter_id)
            if enc and reset_turn:
                enc.turn_index = 0
                enc.round = 1
                s.add(enc)
            s.commit()
        order = self.order(encounter_id)
        if reset_turn and order and order[0].id is not None:
            self.begin_turn(order[0].id)
            order = self.order(encounter_id)
        return order

    def begin_turn(self, combatant_id: int) -> Optional[Combatant]:
        """Reset a creature's per-turn economy at the start of its turn."""
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                return None
            c.action_used = False
            c.bonus_used = False
            c.reaction_used = False
            c.move_left = 1
            c.dodging = False
            c.disengaging = False
            c.attacks_made = 0
            c.sneak_used = False
            s.add(c)
            s.commit()
            s.refresh(c)
            return c

    def update_economy(self, combatant_id: int, **fields) -> Combatant:
        """Set economy fields (action_used, bonus_used, reaction_used,
        move_left, dodging, disengaging) on a combatant."""
        allowed = {"action_used", "bonus_used", "reaction_used",
                   "move_left", "dodging", "disengaging", "attacks_made",
                   "sneak_used", "used_features"}
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            for k, v in fields.items():
                if k in allowed:
                    setattr(c, k, v)
            s.add(c)
            s.commit()
            s.refresh(c)
            return c

    def current_combatant(self, encounter_id: int) -> Optional[Combatant]:
        enc = self.get_encounter(encounter_id)
        if not enc:
            return None
        order = self.order(encounter_id)
        if not order:
            return None
        idx = min(enc.turn_index, len(order) - 1)
        return order[idx]

    def next_turn(self, encounter_id: int) -> tuple[Optional[Encounter], Optional[Combatant]]:
        """Advance to the next living combatant, incrementing the round on wrap."""
        with Session(self.engine) as s:
            enc = s.get(Encounter, encounter_id)
            if not enc:
                return None, None
            order = self.order(encounter_id)
            n = len(order)
            if n == 0:
                return enc, None
            idx = enc.turn_index
            steps = 0
            while steps < n:
                idx += 1
                if idx >= n:
                    idx = 0
                    enc.round += 1
                steps += 1
                if not order[idx].defeated:
                    break
            enc.turn_index = idx
            s.add(enc)
            s.commit()
            s.refresh(enc)
        cur = self.current_combatant(encounter_id)
        if cur and cur.id is not None:
            self.begin_turn(cur.id)
            cur = self.get_combatant(cur.id)
        return enc, cur

    # ----- damage / healing / status -----

    def apply_damage(self, combatant_id: int, amount: int) -> dict:
        """Deal ``amount`` damage (temp HP absorbs first). Returns the new state."""
        amount = max(0, amount)
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            absorbed = min(c.temp_hp, amount)
            c.temp_hp -= absorbed
            remaining = amount - absorbed
            c.current_hp = max(0, c.current_hp - remaining)
            broke_conc = bool(c.concentration) and remaining > 0
            if c.current_hp == 0:
                c.defeated = True
                c.concentration = None
            s.add(c)
            s.commit()
            s.refresh(c)
            out = _combatant_dict(c)
            out["damage_taken"] = amount
            out["concentration_check"] = broke_conc
            if broke_conc:
                out["concentration_dc"] = max(10, remaining // 2)
            return out

    def heal(self, combatant_id: int, amount: int) -> dict:
        amount = max(0, amount)
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            if c.current_hp == 0 and amount > 0:
                c.defeated = False
            c.current_hp = min(c.max_hp, c.current_hp + amount)
            s.add(c)
            s.commit()
            s.refresh(c)
            return _combatant_dict(c)

    def set_temp_hp(self, combatant_id: int, amount: int) -> dict:
        """Temp HP does not stack — take the higher value (SRD)."""
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            c.temp_hp = max(c.temp_hp, max(0, amount))
            s.add(c)
            s.commit()
            s.refresh(c)
            return _combatant_dict(c)

    def add_condition(self, combatant_id: int, condition: str) -> dict:
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            conds = list(c.conditions or [])
            if condition not in conds:
                conds.append(condition)
            c.conditions = conds
            s.add(c)
            s.commit()
            s.refresh(c)
            return _combatant_dict(c)

    def remove_condition(self, combatant_id: int, condition: str) -> dict:
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            c.conditions = [x for x in (c.conditions or []) if x != condition]
            s.add(c)
            s.commit()
            s.refresh(c)
            return _combatant_dict(c)

    def set_concentration(self, combatant_id: int, spell: Optional[str]) -> dict:
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            c.concentration = spell or None
            s.add(c)
            s.commit()
            s.refresh(c)
            return _combatant_dict(c)

    def set_position(self, combatant_id: int, position: Optional[str]) -> dict:
        """Record a spacing band: 'melee with <name>' | 'near' | 'far' (None clears)."""
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            c.position = (position or "").strip() or None
            s.add(c)
            s.commit()
            s.refresh(c)
            return _combatant_dict(c)

    _COVER_AC_BONUS = {"none": 0, "half": 2, "three-quarters": 5, "total": 0}

    def set_cover(self, combatant_id: int, cover: str) -> dict:
        cover = (cover or "none").lower()
        if cover not in self._COVER_AC_BONUS:
            raise ValueError(
                "cover must be one of: none, half, three-quarters, total")
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            c.cover = cover
            s.add(c)
            s.commit()
            s.refresh(c)
            return _combatant_dict(c)

    def effective_ac(self, combatant_id: int) -> Optional[int]:
        """AC including any cover bonus (None if the combatant has no AC set)."""
        c = self.get_combatant(combatant_id)
        if not c or c.armor_class is None:
            return None
        return c.armor_class + self._COVER_AC_BONUS.get(c.cover or "none", 0)

    # ----- views -----

    def state(self, encounter_id: int) -> dict:
        enc = self.get_encounter(encounter_id)
        if not enc:
            return {}
        order = self.order(encounter_id)
        current = order[min(enc.turn_index, len(order) - 1)] if order else None
        return {
            "id": enc.id,
            "session_id": enc.session_id,
            "name": enc.name,
            "round": enc.round,
            "active": enc.active,
            "turn_index": enc.turn_index,
            "current_combatant_id": current.id if current else None,
            "combatants": [_combatant_dict(c) for c in order],
        }

    def render(self, encounter_id: int) -> str:
        """Compact, DM-prompt-friendly board of the current fight."""
        enc = self.get_encounter(encounter_id)
        if not enc:
            return ""
        order = self.order(encounter_id)
        current = order[min(enc.turn_index, len(order) - 1)] if order else None
        lines = [f"# Combat: {enc.name} — round {enc.round}"]
        if not order:
            lines.append("(no combatants)")
            return "\n".join(lines)
        for c in order:
            marker = "\u27a4 " if current and c.id == current.id else "  "
            hp = f"{c.current_hp}/{c.max_hp} HP"
            if c.temp_hp:
                hp += f" (+{c.temp_hp} temp)"
            ac = f", AC {c.armor_class}" if c.armor_class is not None else ""
            status = ""
            extras = list(c.conditions or [])
            if c.position:
                extras.append(f"@ {c.position}")
            if c.concentration:
                extras.append(f"concentrating: {c.concentration}")
            if c.cover and c.cover != "none":
                extras.append(f"{c.cover} cover")
            if c.defeated:
                extras.append("DOWN")
            if extras:
                status = f" [{', '.join(extras)}]"
            lines.append(f"{marker}{c.initiative:>2} · {c.name}: {hp}{ac}{status}")
        return "\n".join(lines)


def _combatant_dict(c: Combatant) -> dict:
    return {
        "id": c.id,
        "encounter_id": c.encounter_id,
        "name": c.name,
        "kind": c.kind,
        "character_id": c.character_id,
        "monster_slug": c.monster_slug,
        "initiative": c.initiative,
        "dex_mod": c.dex_mod,
        "max_hp": c.max_hp,
        "current_hp": c.current_hp,
        "temp_hp": c.temp_hp,
        "armor_class": c.armor_class,
        "cover": c.cover,
        "position": c.position,
        "action_used": c.action_used,
        "bonus_used": c.bonus_used,
        "reaction_used": c.reaction_used,
        "move_left": c.move_left,
        "dodging": c.dodging,
        "disengaging": c.disengaging,
        "attacks_made": c.attacks_made,
        "used_features": list(c.used_features or []),
        "conditions": list(c.conditions or []),
        "concentration": c.concentration,
        "defeated": c.defeated,
        "notes": c.notes,
    }
