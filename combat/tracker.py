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
from rules import damage as dmg
from rules.models import Monster

from .models import Awareness, Encounter, Combatant, CombatantKind


def _default_engine(database_url: Optional[str] = None) -> Engine:
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        database_url = f"sqlite:///{backend_db}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)


class CombatTracker:
    def __init__(self, engine: Optional[Engine] = None,
                 database_url: Optional[str] = None,
                 con_save_mod_for=None, defenses_for=None):
        self.engine = engine or _default_engine(database_url)
        # ``con_save_mod_for(combatant) -> int`` — the one thing the tracker
        # cannot know about a creature it holds a row for. With it, damage
        # ROLLS the concentration save here, which is the only place all eight
        # of the engine's damage paths and the DM's own damage hook meet;
        # without it the check is reported as pending exactly as before, so a
        # caller that never installs one loses nothing. The DC has always been
        # computed here, so the save belongs beside it.
        self.con_save_mod_for = con_save_mod_for
        # ``defenses_for(combatant) -> rules.damage.Defenses | None`` — the
        # same shape and the same reason. A MONSTER's resistances are in its
        # stat block and read here; a PC's come from species traits, Rage and
        # spells, none of which the tracker can see. Without it, monsters still
        # resist correctly and PCs simply don't, which is where the game was.
        self.defenses_for = defenses_for

    def defenses(self, c: Combatant):
        """What this creature resists — its stat block, or the callback."""
        if c.character_id and self.defenses_for is not None:
            try:
                got = self.defenses_for(c)
                if got is not None:
                    return got
            except Exception as e:
                print(f"[defenses] {e}")
        if not c.monster_slug:
            return None
        with Session(self.engine) as s:
            row = s.exec(select(Monster).where(
                Monster.index_slug == c.monster_slug)).first()
        return dmg.defenses_of(row) if row is not None else None

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine)
        # `create_all` makes missing TABLES and nothing else, so a new column on
        # an existing database is a column that is simply not there and every
        # query naming it fails. Same ad-hoc migration the board and the world
        # graph use.
        try:
            with self.engine.begin() as conn:
                have = {r[1] for r in conn.exec_driver_sql(
                    'PRAGMA table_info("combat_combatant")')}
                if have and "awareness" not in have:
                    conn.exec_driver_sql(
                        'ALTER TABLE "combat_combatant" ADD COLUMN '
                        "awareness VARCHAR DEFAULT 'alert'")
        except Exception as e:      # never block a fight on a migration
            print(f"[combat] awareness column check failed: {e}")

    def set_awareness(self, combatant_id: int, awareness: str) -> dict:
        """Set what one creature knows. Returns what changed.

        Escalation only ever goes UP through :attr:`Awareness.RANK` unless the
        DM says otherwise outright — a creature that has seen you does not go
        back to wondering because the next round is quiet, and a fight where
        alertness could silently decay would be one nobody could reason about.
        """
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if c is None:
                return {"ok": False, "reason": "no such combatant"}
            want = awareness if awareness in Awareness.ALL else Awareness.ALERT
            was = c.awareness or Awareness.ALERT
            c.awareness = want
            s.add(c)
            s.commit()
            return {"ok": True, "name": c.name, "was": was, "now": want}

    def raise_awareness(self, encounter_id: int, *, names: Optional[list[str]] = None,
                        to: str = Awareness.ALERT) -> list[dict]:
        """Wake creatures up. Everything on the NPC side unless names are given.

        This is what a failed Stealth check, a shout, a slammed door or a
        thrown fireball actually does to a room, and it is one call so every
        one of those paths reaches the same rule.
        """
        want = to if to in Awareness.ALL else Awareness.ALERT
        out: list[dict] = []
        wanted = {n.strip().lower() for n in (names or []) if n.strip()}
        for c in self.order(encounter_id):
            if c.kind == CombatantKind.PC:
                continue
            if wanted and c.name.lower() not in wanted:
                continue
            if Awareness.RANK.get(c.awareness or Awareness.ALERT, 2) >= \
                    Awareness.RANK.get(want, 2):
                continue
            got = self.set_awareness(c.id, want)
            if got.get("ok"):
                out.append(got)
        return out

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

    def set_pending_reaction(self, encounter_id: int,
                             payload: Optional[dict]) -> None:
        """Store (or clear, with None) the frozen-attack reaction prompt."""
        with Session(self.engine) as s:
            enc = s.get(Encounter, encounter_id)
            if enc:
                enc.pending_reaction = payload
                s.add(enc)
                s.commit()

    def get_pending_reaction(self, encounter_id: int) -> Optional[dict]:
        enc = self.get_encounter(encounter_id)
        return dict(enc.pending_reaction) if enc and enc.pending_reaction else None

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
        side: Optional[str] = None,
        awareness: str = Awareness.ALERT,
    ) -> Combatant:
        with Session(self.engine) as s:
            c = Combatant(
                encounter_id=encounter_id, name=name, kind=kind,
                max_hp=max(1, max_hp), current_hp=max(1, max_hp), temp_hp=0,
                armor_class=armor_class, dex_mod=dex_mod, initiative=initiative,
                character_id=character_id, monster_slug=monster_slug,
                conditions=[], notes=notes, side=side,
                awareness=(awareness if awareness in Awareness.ALL
                           else Awareness.ALERT),
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
        side: Optional[str] = None,
        initiative: int = 0,
        dex_mod: Optional[int] = None,
        summoned_by: Optional[int] = None,
        summon_spell: Optional[str] = None,
    ) -> list[Combatant]:
        """Add ``count`` copies of an SRD monster, hydrated from ``rules_monster``.

        ``side`` marks who it fights for (a conjured spirit is a monster on the
        party's side). ``initiative``/``dex_mod`` override the roll, which is
        how "shares your initiative count, but takes its turn immediately after
        yours" is expressed: copying the summoner's initiative AND its tiebreak
        leaves ``order()``'s final key — the row id — to place the newer row
        just after. The creature has no initiative of its own; that is the rule,
        not a hack around it.
        """
        rng = rng or random
        with Session(self.engine) as s:
            mon = s.exec(select(Monster).where(Monster.index_slug == slug)).first()
            if mon is None:
                raise ValueError(f"Unknown monster slug: {slug!r}")
            dex = ability_modifier(mon.dexterity) if dex_mod is None else int(dex_mod)
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
                    armor_class=mon.armor_class, dex_mod=dex,
                    initiative=int(initiative or 0), side=side,
                    summoned_by=summoned_by, summon_spell=summon_spell,
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
        bonus_dice_for=None,
    ) -> list[Combatant]:
        """Roll d20 + DEX mod for combatants (only those unset unless ``reroll``).

        ``reset_turn=False`` keeps the current round/turn — for rolling in
        mid-fight reinforcements without restarting the fight.

        ``bonus_dice_for(name) -> str`` adds extra dice for a given combatant
        ("1d4"), which is how a feature that steadies a whole party's nerves
        reaches the roll. A callback rather than a column: the reason for the
        bonus lives with whatever granted it, and the tracker stays ignorant of
        every such feature."""
        rng = rng or random
        with Session(self.engine) as s:
            combatants = self._combatants(s, encounter_id)
            for c in combatants:
                if reroll or not c.initiative:
                    total = rng.randint(1, 20) + c.dex_mod
                    if bonus_dice_for is not None:
                        try:
                            expr = bonus_dice_for(c.name) or ""
                            if expr:
                                n, _, faces = str(expr).lower().partition("d")
                                for _i in range(max(1, int(n or 1))):
                                    total += rng.randint(1, max(2, int(faces)))
                        except Exception as e:  # noqa: BLE001
                            print(f"[combat] initiative bonus skipped for "
                                  f"{c.name}: {e}")
                    c.initiative = total
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
            c.interactions_used = 0
            c.last_weapon = None
            # Shield's +5 AC lasts until the start of the caster's next turn.
            if c.conditions:
                c.conditions = [x for x in c.conditions
                                if x.lower() != "shielded"]
            s.add(c)
            s.commit()
            s.refresh(c)
            return c

    def update_economy(self, combatant_id: int, **fields) -> Combatant:
        """Set economy fields (action_used, bonus_used, reaction_used,
        move_left, dodging, disengaging) on a combatant."""
        allowed = {"action_used", "bonus_used", "reaction_used",
                   "move_left", "dodging", "disengaging", "attacks_made",
                   "sneak_used", "used_features", "interactions_used",
                   "last_weapon"}
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

    def apply_damage(self, combatant_id: int, amount: int = 0, *,
                     rolled: Optional[list] = None) -> dict:
        """Deal damage — TYPED, if the caller says what kind. New state back.

        ``rolled`` is a list of ``(rules.damage.Packet, amount)``: one entry
        per damage type in the blow, because a flame tongue's slashing and its
        fire meet a creature's defences separately and a fire elemental takes
        one and not the other. Summing them first and reducing once is the
        mistake the signature exists to prevent.

        Resistance is applied HERE, before temp HP and before the concentration
        DC, for the same reason the save is rolled here: this is the one place
        all of the engine's damage paths and the DM's own damage hook meet, so
        it is the only place a rule about damage can be written once. An
        untyped ``amount`` is passed through untouched, which is exactly what
        every caller got before types existed.
        """
        reduce_notes: list[str] = []
        raw_total = int(amount or 0)
        if rolled:
            raw_total = sum(int(n) for _, n in rolled)
            live = self.get_combatant(combatant_id)
            applied = dmg.apply(self.defenses(live) if live else None, rolled)
            amount = applied.total
            reduce_notes = applied.notes
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
            dismissed: list[str] = []
            if c.current_hp == 0:
                c.defeated = True
                # Going down ends concentration outright — no save — so the
                # spirits it was holding up go with it. Same helper as every
                # other way concentration ends.
                dismissed = self._dismiss_summons(s, combatant_id, c.concentration)
                c.concentration = None
                broke_conc = False
            dc = max(10, remaining // 2)
            roll = None
            if broke_conc and self.con_save_mod_for is not None:
                try:
                    mod = int(self.con_save_mod_for(c))
                except Exception:
                    mod = None
                if mod is not None:
                    res = dice_roll(f"1d20{mod:+d}")
                    held = res.total >= dc
                    roll = {"total": res.total, "dc": dc, "detail": res.detail,
                            "success": held, "spell": c.concentration}
                    if not held:
                        dismissed = self._dismiss_summons(s, combatant_id,
                                                          c.concentration)
                        c.concentration = None
                    broke_conc = not held
            s.add(c)
            s.commit()
            s.refresh(c)
            out = _combatant_dict(c)
            out["damage_taken"] = amount
            if reduce_notes or raw_total != amount:
                # What the defences did, so the table sees WHY a blow landed
                # soft. A halving nobody is told about reads as a bad roll.
                out["damage_rolled"] = raw_total
                out["damage_notes"] = reduce_notes
            # True only when concentration was actually LOST (or, with no
            # modifier callback installed, when a save is owed and unrolled).
            out["concentration_check"] = broke_conc
            out["dismissed"] = dismissed
            if roll is not None:
                out["concentration_roll"] = roll
            if broke_conc:
                out["concentration_dc"] = dc
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

    def _dismiss_summons(self, s: Session, summoner_id: int,
                         spell: Optional[str]) -> list[str]:
        """"The creature disappears when the spell ends" — make that true.

        A conjured spirit is held up by its summoner's concentration and by
        nothing else, so ending that concentration has to reach the roster or
        the spirit fights on after the magic that made it is gone. Marked
        defeated rather than deleted: that is already the state a spirit at 0
        HP is in (the rules give both endings the same outcome), and it is
        already mirrored onto the board and skipped by the engine, so nothing
        else needs telling.
        """
        if not spell:
            return []
        rows = s.exec(select(Combatant).where(
            Combatant.summoned_by == summoner_id)).all()
        gone: list[str] = []
        key = str(spell).strip().lower()
        for r in rows:
            if r.defeated or str(r.summon_spell or "").strip().lower() != key:
                continue
            r.defeated = True
            s.add(r)
            gone.append(r.name)
        return gone

    def set_concentration(self, combatant_id: int, spell: Optional[str]) -> dict:
        """Start, change or drop concentration — and the ONE place a summon dies with it.

        Every way concentration ends routes through here (a failed save, the
        caster dropping it, moving it to another spell, going down), so a
        conjured creature can be dismissed in one place instead of at each of
        them. Returns the usual combatant dict plus ``dismissed``: the names of
        the spirits that went with the spell.
        """
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            was = c.concentration
            c.concentration = spell or None
            # Dismissed even when the new spell is the SAME one: that is a
            # re-cast, and the first casting's spirits end with the first
            # casting. Every caller here is a real event (a cast, a drop, a
            # failed save), so there is no redundant set to protect against.
            dismissed = self._dismiss_summons(s, combatant_id, was)
            # ...and the ONE place a per-attack rider ends, for the same
            # reason: every way concentration drops routes through here, so a
            # Spirit Shroud that stopped being concentrated on stops adding
            # damage without each caller having to remember it.
            if was:
                keep = [x for x in (c.conditions or [])
                        if not str(x).lower().startswith(
                            f"rider:{str(was).strip().lower()}:")]
                if len(keep) != len(c.conditions or []):
                    c.conditions = keep
            s.add(c)
            s.commit()
            s.refresh(c)
            out = _combatant_dict(c)
            out["dismissed"] = dismissed
            return out

    def set_pending_saves(self, combatant_id: int, saves: list) -> dict:
        """Replace the repeat-save list ({condition, ability, dc} rows)."""
        with Session(self.engine) as s:
            c = s.get(Combatant, combatant_id)
            if not c:
                raise ValueError("Unknown combatant")
            c.pending_saves = list(saves or [])
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
        "interactions_used": c.interactions_used,
        "last_weapon": c.last_weapon,
        "used_features": list(c.used_features or []),
        "pending_saves": list(c.pending_saves or []),
        "conditions": list(c.conditions or []),
        "concentration": c.concentration,
        "defeated": c.defeated,
        "notes": c.notes,
    }
