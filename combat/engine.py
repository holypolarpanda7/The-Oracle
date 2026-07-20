"""
CombatEngine — deterministic turn resolution over the CombatTracker.

The LLM proposes structured INTENTS; this engine is the referee and the dice:
it validates every intent against turn order, action economy, spacing bands,
and reach, resolves the legal ones with real dice (attack rolls vs effective
AC, contests, saves), applies results to the tracker, and returns a certified
turn report. Illegal intents are NOT applied — they come back as rejections
with player-facing reasons so the narrator can kick the problem back to the
player and leave their turn open.

Turn semantics:
- A creature's per-turn economy (action / bonus action / band-steps of
  movement / reaction) lives on the Combatant row, so a PC's turn can span
  several player messages. The turn ends only when the player declares it
  (an ``end_turn`` intent) or the engine proves the economy exhausted.
- Monster/NPC turns are resolved in one call each; if the proposed intents
  are missing or all illegal, a small default AI acts (attack in reach, else
  close and attack, else dash toward the fight).

Spacing model (gridless bands, no maps):
- position is "far" (rank 2), "near" (rank 1), or "melee with <Name>"
  (rank 0, a pairwise engagement; symmetric — either side's tag counts).
- steps between two creatures: 0 if engaged, else max(1, |rank_a - rank_b|).
- one band-step = a normal move; Dash buys one more step this turn.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session, select

from dice import ability_modifier
from dice.mechanics import ability_check, attack_roll, damage_roll, saving_throw
from rules.models import Monster, Spell

from .models import Combatant
from .tracker import CombatTracker


# --------------------------------------------------------------------------
# Profiles — what a creature can do, built outside the engine.

@dataclass
class PCWeapon:
    name: str
    attack_bonus: int
    damage: str                  # e.g. "1d8+3"
    ranged: bool = False
    finesse: bool = False        # Sneak Attack qualifies on finesse or ranged


@dataclass
class PCProfile:
    """The acting numbers for a player character (built by the backend from
    the Character row + rules items; the engine never touches the char DB)."""
    character_id: int
    name: str
    level: int = 1
    ability_mods: dict[str, int] = field(default_factory=dict)  # str/dex/con/int/wis/cha
    prof: int = 2
    skills: set[str] = field(default_factory=set)               # lowercase skill names
    weapons: list[PCWeapon] = field(default_factory=list)
    spell_attack_bonus: Optional[int] = None
    spell_dc: Optional[int] = None
    spell_mod: Optional[str] = None                             # casting ability key
    # Remaining spell slots {slot level: count}. The engine decrements this
    # in-memory as it resolves; the backend persists spends from the report.
    slots: dict[int, int] = field(default_factory=dict)
    # Action-economy features:
    attacks_per_action: int = 1          # Extra Attack: 2 at fighter 5, etc.
    features: set[str] = field(default_factory=set)
    # recognized: "action surge", "second wind", "rage", "cunning action",
    # "bonus attack" (two-weapon fighting / Martial Arts style off-hand swing),
    # "uncanny dodge" (reaction: halve an attack's damage)
    # Reaction spells the engine may auto-cast when they change the outcome
    # (today: "shield" — +5 AC flips a hit into a miss).
    reaction_spells: set[str] = field(default_factory=set)


# Class features the engine resolves mechanically. "per_encounter": how many
# uses per fight (None = unlimited).
_FEATURES: dict[str, dict] = {
    "action surge": {"cost": "free", "per_encounter": 1},
    "second wind": {"cost": "bonus", "per_encounter": 1, "heal": "1d10+{level}"},
    "rage": {"cost": "bonus", "per_encounter": None, "condition": "raging"},
}

# Spell effects the engine resolves mechanically (keyed by lowercase name).
# save_condition: applied to the target on a FAILED save; repeat_save: the
# target re-saves at the end of each of its turns. ally_condition: applied to
# the (friendly) target, no save. heal: dice per slot level + casting mod.
_SPELL_EFFECTS: dict[str, dict] = {
    # targets: base target cap; upcast_targets: +1 per slot level above base.
    "hold person": {"save_condition": "paralyzed", "repeat_save": True,
                    "targets": 1, "upcast_targets": True},
    "hold monster": {"save_condition": "paralyzed", "repeat_save": True,
                     "targets": 1, "upcast_targets": True},
    "web": {"save_condition": "restrained", "repeat_save": True},
    "entangle": {"save_condition": "restrained", "repeat_save": True},
    "tasha's hideous laughter": {"save_condition": "incapacitated", "repeat_save": True,
                                 "targets": 1},
    "hideous laughter": {"save_condition": "incapacitated", "repeat_save": True,
                         "targets": 1},
    "blindness/deafness": {"save_condition": "blinded", "repeat_save": True,
                           "targets": 1},
    "bane": {"save_condition": "baned", "targets": 3, "upcast_targets": True},
    "faerie fire": {"save_condition": "faerie fire"},
    "bless": {"ally_condition": "blessed", "targets": 3, "upcast_targets": True},
    "magic missile": {"missiles": True},
    "misty step": {"teleport": True},
    "cure wounds": {"heal": "d8"},
    "healing word": {"heal": "d4"},
}

# Common consumables the engine can resolve without the item DB.
_CONSUMABLE_HEALS = {
    "potion of healing": "2d4+2",
    "potion of greater healing": "4d4+4",
    "potion of superior healing": "8d4+8",
    "potion of supreme healing": "10d4+20",
}
_CONSUMABLE_TEMPS = {"potion of heroism": 10}

# Conditions that shape the attack advantage matrix.
_ATTACKER_DISADV = {"poisoned", "prone", "restrained", "blinded", "frightened",
                    "exhaustion"}
_TARGET_GIVES_ADV = {"restrained", "stunned", "paralyzed", "unconscious",
                     "petrified", "blinded", "faerie fire"}
_CANNOT_ACT = {"incapacitated", "stunned", "paralyzed", "unconscious", "petrified"}

_BAND_RANK = {"near": 1, "far": 2}


def _mod_key(name: str) -> str:
    return (name or "")[:3].lower()


class _ReactionPause(Exception):
    """Raised mid-resolution when a player must decide a reaction. The fight
    freezes at this exact point until the owner answers."""

    def __init__(self, payload: dict):
        super().__init__(payload.get("question", "reaction pending"))
        self.payload = payload


@dataclass
class TurnReport:
    """What actually happened (events), what was refused and why (rejections),
    and whether the current creature's turn is now over."""
    events: list[dict] = field(default_factory=list)
    rejections: list[dict] = field(default_factory=list)
    turn_over: bool = False
    turn_over_reason: Optional[str] = None
    remaining: dict = field(default_factory=dict)
    # A reaction prompt froze the fight — nothing advances until it's answered.
    paused: bool = False

    def rolls(self) -> list[dict]:
        """Dice results in the activity UI's RollResult shape."""
        out = []
        for e in self.events:
            for r in e.get("rolls") or []:
                out.append(r)
        return out


class CombatEngine:
    def __init__(self, tracker: CombatTracker, rng: Optional[random.Random] = None):
        self.tracker = tracker
        self.rng = rng or random.Random()

    # ---------------- band / spacing model ----------------

    def _engaged_with(self, a: Combatant, b: Combatant) -> bool:
        pa = (a.position or "").lower()
        pb = (b.position or "").lower()
        return (pa == f"melee with {b.name.lower()}"
                or pb == f"melee with {a.name.lower()}")

    def _rank(self, c: Combatant) -> int:
        p = (c.position or "near").lower()
        if p.startswith("melee"):
            return 0
        return _BAND_RANK.get(p, 1)

    def _steps_between(self, a: Combatant, b: Combatant) -> int:
        if self._engaged_with(a, b):
            return 0
        return max(1, abs(self._rank(a) - self._rank(b)))

    @staticmethod
    def _side(c: Combatant) -> str:
        """PCs are one side; monsters/NPCs the other (v1 — allied NPCs later)."""
        return "party" if c.kind == "pc" else "foe"

    def _engaged_enemies(self, encounter_id: int, c: Combatant) -> list[Combatant]:
        out = []
        for other in self.tracker.order(encounter_id):
            if other.id == c.id or other.defeated:
                continue
            if self._side(other) == self._side(c):
                continue
            if self._engaged_with(c, other):
                out.append(other)
        return out

    def _ally_engaged_with(self, encounter_id: int, attacker: Combatant,
                           target: Combatant) -> bool:
        """An able ally of the attacker is within 5 ft of the target (the
        Sneak Attack ally condition)."""
        for other in self.tracker.order(encounter_id):
            if other.id in (attacker.id, target.id) or other.defeated:
                continue
            if self._side(other) != self._side(attacker):
                continue
            if self._conds(other) & _CANNOT_ACT:
                continue
            if self._engaged_with(other, target):
                return True
        return False

    # ---------------- creature capability lookup ----------------

    def _monster(self, c: Combatant) -> Optional[Monster]:
        if not c.monster_slug:
            return None
        with Session(self.tracker.engine) as s:
            return s.exec(select(Monster).where(
                Monster.index_slug == c.monster_slug)).first()

    def _monster_attacks(self, c: Combatant) -> list[dict]:
        m = self._monster(c)
        out: list[dict] = []
        for a in (m.actions if m else []) or []:
            if a.get("attack_bonus") is None:
                continue
            dmg = ""
            for d in a.get("damage") or []:
                if d.get("damage_dice"):
                    dmg = d["damage_dice"]
                    break
            if not dmg:
                continue
            desc = (a.get("desc") or "").lower()
            out.append({"name": a.get("name") or "attack",
                        "attack_bonus": int(a["attack_bonus"]),
                        "damage": dmg,
                        "ranged": desc.startswith("ranged")
                                  or "ranged weapon attack" in desc})
        return out

    def _multiattack_count(self, c: Combatant) -> int:
        m = self._monster(c)
        for a in (m.actions if m else []) or []:
            if (a.get("name") or "").lower() == "multiattack":
                d = (a.get("desc") or "").lower()
                for word, n in (("two", 2), ("three", 3), ("four", 4), ("five", 5)):
                    if f"makes {word}" in d or f"{word} attacks" in d:
                        return n
        return 1

    def _ability_mod(self, c: Combatant, ability: str,
                     profiles: dict[int, PCProfile]) -> int:
        key = _mod_key(ability)
        if c.character_id and c.character_id in profiles:
            return profiles[c.character_id].ability_mods.get(key, 0)
        m = self._monster(c)
        if m:
            score = {"str": m.strength, "dex": m.dexterity, "con": m.constitution,
                     "int": m.intelligence, "wis": m.wisdom,
                     "cha": m.charisma}.get(key)
            return ability_modifier(score) if score is not None else 0
        return c.dex_mod if key == "dex" else 0

    def _attack_profile(self, c: Combatant, weapon: str,
                        profiles: dict[int, PCProfile]) -> Optional[dict]:
        """Resolve what this creature swings: named weapon, else its best."""
        w = (weapon or "").strip().lower()
        if c.character_id and c.character_id in profiles:
            p = profiles[c.character_id]
            pool = p.weapons or []

            def as_dict(cand: PCWeapon) -> dict:
                return {"name": cand.name, "attack_bonus": cand.attack_bonus,
                        "damage": cand.damage, "ranged": cand.ranged,
                        "finesse": cand.finesse}

            for cand in pool:
                if w and w in cand.name.lower():
                    return as_dict(cand)
            if pool and w not in ("unarmed", "fist", "punch"):
                return as_dict(pool[0])
            stray = p.ability_mods.get("str", 0)
            return {"name": "Unarmed strike", "attack_bonus": p.prof + stray,
                    "damage": f"1+{stray}" if stray > 0 else "1",
                    "ranged": False, "finesse": False}
        pool = self._monster_attacks(c)
        if not pool:
            return None
        for cand in pool:
            if w and w in cand["name"].lower():
                return cand
        return pool[0]

    def _melee_profile(self, c: Combatant,
                       profiles: dict[int, PCProfile]) -> Optional[dict]:
        if c.character_id and c.character_id in profiles:
            for cand in profiles[c.character_id].weapons:
                if not cand.ranged:
                    return {"name": cand.name, "attack_bonus": cand.attack_bonus,
                            "damage": cand.damage, "ranged": False,
                            "finesse": cand.finesse}
            return self._attack_profile(c, "unarmed", profiles)
        for cand in self._monster_attacks(c):
            if not cand["ranged"]:
                return cand
        return None

    # ---------------- helpers ----------------

    def _resolve_targets(self, encounter_id: int, actor: Combatant,
                         raw: str) -> list[Combatant]:
        """Resolve a cast intent's target field: one name, a comma/'and' list,
        or the keywords 'all enemies' / 'all allies'."""
        raw = (raw or "").strip()
        if not raw:
            return []
        low = raw.lower()
        order = [c for c in self.tracker.order(encounter_id) if not c.defeated]
        if low in ("all enemies", "all foes", "the enemies", "every enemy",
                   "everyone in the area"):
            return [c for c in order if self._side(c) != self._side(actor)]
        if low in ("all allies", "the party", "every ally"):
            return [c for c in order if self._side(c) == self._side(actor)]
        out: list[Combatant] = []
        for part in re.split(r",|\s+and\s+", raw):
            c = self._find(encounter_id, part.strip())
            if c is not None and not c.defeated \
                    and all(c.id != x.id for x in out):
                out.append(c)
        return out

    def _eff_ac(self, c: Combatant) -> Optional[int]:
        """Effective AC including cover and an active Shield reaction."""
        base = self.tracker.effective_ac(c.id)
        if base is not None and "shielded" in self._conds(c):
            base += 5
        return base

    def _find(self, encounter_id: int, ref: str) -> Optional[Combatant]:
        ref_l = (ref or "").strip().lower()
        if not ref_l:
            return None
        order = self.tracker.order(encounter_id)
        for c in order:
            if c.name.lower() == ref_l:
                return c
        for c in order:
            if c.name.lower().startswith(ref_l):
                return c
        for c in order:
            if ref_l in c.name.lower():
                return c
        return None

    def _conds(self, c: Combatant) -> set[str]:
        return {x.lower() for x in (c.conditions or [])}

    def _remaining(self, c: Combatant,
                   prof: Optional[PCProfile] = None) -> dict:
        c = self.tracker.get_combatant(c.id) or c
        return {"action": not c.action_used, "bonus": not c.bonus_used,
                "move_steps": max(0, c.move_left),
                "reaction": not c.reaction_used,
                "options": self._leftover_options(c, prof)}

    @staticmethod
    def _roll_dict(label: str, detail: str, total: int,
                   dc: Optional[int] = None, success: Optional[bool] = None,
                   expr: str = "") -> dict:
        out = {"expr": expr or "d20", "label": label, "total": total,
               "detail": detail}
        if dc is not None:
            out["dc"] = dc
        if success is not None:
            out["success"] = success
        return out

    def _attack_advantage(self, atk: Combatant, tgt: Combatant,
                          ranged: bool, encounter_id: int) -> tuple[bool, bool, list[str]]:
        adv, dis, notes = False, False, []
        ac_conds, tc_conds = self._conds(atk), self._conds(tgt)
        if ranged and self._engaged_enemies(encounter_id, atk):
            dis = True
            notes.append("ranged attack while in melee: disadvantage")
        if ac_conds & _ATTACKER_DISADV:
            dis = True
            notes.append(f"attacker {', '.join(sorted(ac_conds & _ATTACKER_DISADV))}: disadvantage")
        if "invisible" in ac_conds or "hidden" in ac_conds:
            adv = True
            notes.append("unseen attacker: advantage")
        if "helped" in ac_conds:
            adv = True
            notes.append("helped: advantage")
        if tgt.dodging:
            dis = True
            notes.append(f"{tgt.name} is Dodging: disadvantage")
        if tc_conds & _TARGET_GIVES_ADV:
            adv = True
            notes.append(f"target {', '.join(sorted(tc_conds & _TARGET_GIVES_ADV))}: advantage")
        if "prone" in tc_conds:
            if ranged:
                dis = True
                notes.append("target prone vs ranged: disadvantage")
            else:
                adv = True
                notes.append("target prone in melee: advantage")
        if adv and dis:
            notes.append("advantage and disadvantage cancel")
            adv = dis = False
        return adv, dis, notes

    # ------------- reactions: the PLAYER decides, the fight freezes -------------

    def _reaction_ready(self, target: Combatant) -> bool:
        fresh = self.tracker.get_combatant(target.id)
        return not fresh.reaction_used and not (self._conds(fresh) & _CANNOT_ACT)

    def _maybe_prompt_shield(self, attacker: Combatant, target: Combatant,
                             atk, eff_ac: Optional[int], profiles: dict,
                             weapon: dict, notes: list[str],
                             after: Optional[dict]) -> None:
        """Freeze the fight and ask when Shield would flip this hit into a
        miss. Never asks when Shield couldn't help (crit, big hit, no slot)."""
        if not atk.hit or atk.is_crit:
            return
        p = profiles.get(target.character_id) if target.character_id else None
        if p is None or "shield" not in p.reaction_spells:
            return
        if not self._reaction_ready(target):
            return
        if eff_ac is None or atk.total >= eff_ac + 5:
            return
        avail = {lv: n for lv, n in (p.slots or {}).items() if n > 0}
        if not avail:
            return
        lv = min(avail)
        raise _ReactionPause({
            "type": "shield",
            "attacker_id": attacker.id, "attacker": attacker.name,
            "target_id": target.id, "target": target.name,
            "target_char_id": target.character_id,
            "weapon": dict(weapon), "atk_total": atk.total,
            "crit": bool(atk.is_crit), "eff_ac": eff_ac, "slot": lv,
            "notes": list(notes), "after": after,
            "question": (f"{attacker.name}'s {weapon.get('name', 'attack')} is about "
                         f"to hit {target.name} ({atk.total} vs AC {eff_ac}). "
                         f"Shield would turn it aside (+5 AC until their next "
                         f"turn, level-{lv} slot, reaction)."),
            "options": ["cast Shield", "take the hit"],
        })

    def _maybe_prompt_uncanny(self, attacker_name: str, target: Combatant,
                              total: int, profiles: dict, ev_ctx: dict,
                              after: Optional[dict]) -> int:
        """Freeze and ask when Uncanny Dodge could halve this damage."""
        p = profiles.get(target.character_id) if target.character_id else None
        if p is None or "uncanny dodge" not in p.features or total <= 1:
            return total
        if not self._reaction_ready(target):
            return total
        raise _ReactionPause({
            "type": "uncanny",
            "attacker": attacker_name,
            "target_id": target.id, "target": target.name,
            "target_char_id": target.character_id,
            "damage_total": total, "ev_ctx": dict(ev_ctx), "after": after,
            "question": (f"{attacker_name}'s blow lands on {target.name} for "
                         f"{total} damage. Uncanny Dodge would halve it "
                         f"(reaction)."),
            "options": ["Uncanny Dodge", "take it"],
        })

    def _maybe_offer_oa(self, enemy: Combatant, mover: Combatant,
                        weapon: dict, after: Optional[dict]) -> None:
        """A PC's opportunity attack is a choice, not a reflex — freeze and
        ask before rolling anything."""
        raise _ReactionPause({
            "type": "oa_offer",
            "reactor_id": enemy.id,
            "mover_id": mover.id,
            "target": enemy.name, "target_id": enemy.id,
            "target_char_id": enemy.character_id,
            "after": after,
            "question": (f"{mover.name} slips out of {enemy.name}'s reach — "
                         f"an opportunity attack with "
                         f"{weapon.get('name', 'their weapon')} is there for "
                         f"the taking (reaction)."),
            "options": ["take the swing", "hold the reaction"],
        })

    @staticmethod
    def _prompt_event(payload: dict) -> dict:
        return {"kind": "reaction_prompt", "actor": payload.get("target"),
                "rolls": [], "options": list(payload.get("options") or []),
                "notes": [payload.get("question", "")]}

    def resume_reaction(self, encounter_id: int, use: bool,
                        profiles: Optional[dict[int, PCProfile]] = None) -> TurnReport:
        """Answer the frozen reaction and finish the interrupted attack.
        May pause again (a declined Shield can chain into an Uncanny ask)."""
        profiles = profiles or {}
        rep = TurnReport()
        payload = self.tracker.get_pending_reaction(encounter_id)
        if not payload:
            rep.rejections.append({"reason": "No reaction is pending."})
            return rep
        self.tracker.set_pending_reaction(encounter_id, None)
        try:
            if payload.get("type") == "shield":
                self._resume_shield(encounter_id, payload, use, profiles, rep)
            elif payload.get("type") == "oa_offer":
                self._resume_oa(encounter_id, payload, use, profiles, rep)
            else:
                self._resume_uncanny(encounter_id, payload, use, profiles, rep)
        except _ReactionPause as p:
            self.tracker.set_pending_reaction(encounter_id, p.payload)
            rep.events.append(self._prompt_event(p.payload))
            rep.paused = True
        return rep

    def _resume_shield(self, encounter_id: int, payload: dict, use: bool,
                       profiles: dict, rep: TurnReport) -> None:
        target = self.tracker.get_combatant(payload["target_id"])
        if target is None:
            return
        weapon = payload.get("weapon") or {}
        ev = {"kind": "attack", "actor": payload.get("attacker"),
              "target": target.name, "weapon": weapon.get("name"),
              "crit": bool(payload.get("crit")),
              "notes": list(payload.get("notes") or []), "rolls": []}
        if use and self._reaction_ready(target):
            p = profiles.get(target.character_id) if target.character_id else None
            lv = int(payload.get("slot") or 1)
            if p is not None:
                p.slots[lv] = max(0, p.slots.get(lv, 0) - 1)
            self.tracker.update_economy(target.id, reaction_used=True)
            self.tracker.add_condition(target.id, "shielded")
            rep.events.append({
                "kind": "reaction", "actor": target.name, "spell": "Shield",
                "slot_spent": lv, "slot_char_id": target.character_id,
                "rolls": [], "notes": [
                    f"+5 AC until their next turn — "
                    f"{payload.get('attacker')}'s attack misses"]})
            ev["hit"] = False
            ev["notes"].append("turned aside by Shield")
            rep.events.append(ev)
        else:
            ev["hit"] = True
            dmg = damage_roll(weapon.get("damage", "1"),
                              crit=bool(payload.get("crit")), rng=self.rng)
            ev["rolls"].append(self._roll_dict(
                f"{weapon.get('name', 'attack')} damage", dmg.detail,
                dmg.total, expr=weapon.get("damage", "")))
            total = self._maybe_prompt_uncanny(
                payload.get("attacker", "the attacker"), target, dmg.total,
                profiles, ev_ctx=ev, after=payload.get("after"))
            out = self.tracker.apply_damage(target.id, total)
            ev["damage"] = total
            ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
            if out.get("defeated"):
                ev["defeated"] = True
            rep.events.append(ev)
        self._finish_after(encounter_id, payload.get("after"), profiles, rep)

    def _resume_uncanny(self, encounter_id: int, payload: dict, use: bool,
                        profiles: dict, rep: TurnReport) -> None:
        target = self.tracker.get_combatant(payload["target_id"])
        if target is None:
            return
        total = int(payload.get("damage_total") or 0)
        ev = dict(payload.get("ev_ctx") or {})
        ev.setdefault("kind", "attack")
        ev.setdefault("rolls", [])
        ev.setdefault("notes", [])
        ev["hit"] = True
        if use and self._reaction_ready(target):
            self.tracker.update_economy(target.id, reaction_used=True)
            halved = total // 2
            rep.events.append({"kind": "reaction", "actor": target.name,
                               "spell": "Uncanny Dodge", "rolls": [],
                               "notes": [f"halves the blow ({total} → {halved})"]})
            total = halved
        out = self.tracker.apply_damage(target.id, total)
        ev["damage"] = total
        ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
        if out.get("defeated"):
            ev["defeated"] = True
        rep.events.append(ev)
        self._finish_after(encounter_id, payload.get("after"), profiles, rep)

    def _resume_oa(self, encounter_id: int, payload: dict, use: bool,
                   profiles: dict, rep: TurnReport) -> None:
        """The player answered an opportunity-attack offer: swing or hold,
        then let the interrupted move finish."""
        enemy = self.tracker.get_combatant(payload["reactor_id"])
        mover = self.tracker.get_combatant(payload["mover_id"])
        if enemy is not None and mover is not None and not mover.defeated:
            if use and self._reaction_ready(enemy):
                self._roll_opportunity_attack(
                    encounter_id, enemy, mover, profiles, rep,
                    after=payload.get("after"), offered=True)
            else:
                rep.events.append({
                    "kind": "note", "actor": enemy.name, "rolls": [],
                    "notes": [f"{enemy.name} holds their reaction as "
                              f"{mover.name} slips away"]})
        self._finish_after(encounter_id, payload.get("after"), profiles, rep)

    def _finish_after(self, encounter_id: int, after: Optional[dict],
                      profiles: dict, rep: TurnReport) -> None:
        """Complete a move that was interrupted by a reaction mid-OA: run the
        remaining opportunity attacks, then land the mover on its new band."""
        if not after or after.get("kind") != "move":
            return
        mover = self.tracker.get_combatant(after["actor_id"])
        if mover is None or mover.defeated:
            return
        for eid in after.get("enemy_ids") or []:
            enemy = self.tracker.get_combatant(eid)
            if enemy is None or enemy.defeated or enemy.reaction_used \
                    or (self._conds(enemy) & _CANNOT_ACT):
                continue
            remaining = [x for x in (after.get("enemy_ids") or []) if x != eid]
            self._roll_opportunity_attack(
                encounter_id, enemy, mover, profiles, rep,
                after={**after, "enemy_ids": remaining})
            mover = self.tracker.get_combatant(after["actor_id"])
            if mover is None or mover.defeated:
                return
        self.tracker.set_position(mover.id, after["new_pos"])
        fresh = self.tracker.get_combatant(mover.id)
        self.tracker.update_economy(
            mover.id, move_left=max(0, fresh.move_left - int(after.get("cost") or 1)))
        rep.events.append({"kind": "move", "actor": mover.name,
                           "to": after["new_pos"], "steps": after.get("cost"),
                           "rolls": [], "notes": ["completes the move"]})

    def _roll_opportunity_attack(self, encounter_id: int, enemy: Combatant,
                                 mover: Combatant, profiles: dict,
                                 rep: TurnReport, after: Optional[dict],
                                 offered: bool = False) -> None:
        """One opportunity attack against a mover (may pause for a reaction).
        A PC's OA is offered to the player first unless ``offered`` (the
        resume path, where the player already said yes)."""
        mprof = self._melee_profile(enemy, profiles)
        if not mprof:
            return
        if enemy.kind == "pc" and not offered:
            self._maybe_offer_oa(enemy, mover, mprof, after)  # raises
        self.tracker.update_economy(enemy.id, reaction_used=True)
        adv, dis, notes = self._attack_advantage(enemy, mover, False, encounter_id)
        eff_ac = self._eff_ac(mover)
        atk = attack_roll(mprof["attack_bonus"], eff_ac, advantage=adv,
                          disadvantage=dis,
                          label=f"Opportunity attack ({enemy.name})",
                          rng=self.rng)
        self._maybe_prompt_shield(enemy, mover, atk, eff_ac, profiles,
                                  mprof, notes, after)
        oa_rolls = [self._roll_dict(
            f"Opportunity attack — {enemy.name}", atk.detail, atk.total,
            dc=eff_ac, success=bool(atk.hit))]
        oa = {"kind": "opportunity_attack", "actor": enemy.name,
              "target": mover.name, "weapon": mprof["name"],
              "hit": bool(atk.hit), "rolls": oa_rolls, "notes": notes}
        if atk.hit:
            dmg = damage_roll(mprof["damage"], crit=atk.is_crit, rng=self.rng)
            total = self._maybe_prompt_uncanny(enemy.name, mover, dmg.total,
                                               profiles, ev_ctx=oa, after=after)
            out = self.tracker.apply_damage(mover.id, total)
            oa_rolls.append(self._roll_dict(
                f"{mprof['name']} damage", dmg.detail, total,
                expr=mprof["damage"]))
            oa["damage"] = total
            oa["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
            if out.get("defeated"):
                oa["defeated"] = True
        rep.events.append(oa)

    # ---------------- intent resolution ----------------

    def resolve(self, encounter_id: int, intents: list[dict],
                profiles: Optional[dict[int, PCProfile]] = None) -> TurnReport:
        """Resolve intents for the CURRENT creature's turn. Illegal intents
        are rejected with reasons; nothing about them is applied."""
        profiles = profiles or {}
        rep = TurnReport()
        if self.tracker.get_pending_reaction(encounter_id):
            rep.rejections.append({
                "reason": "A reaction decision is pending — it must be "
                          "answered (or declined) before anything else happens."})
            rep.paused = True
            return rep
        cur = self.tracker.current_combatant(encounter_id)
        if cur is None:
            rep.rejections.append({"reason": "No one is in the fight."})
            return rep

        for intent in intents:
            cur = self.tracker.current_combatant(encounter_id)
            if cur is None or rep.turn_over:
                rep.rejections.append({
                    "intent": intent,
                    "reason": "The turn already ended — further acts wait for the next turn."})
                continue
            verb = (intent.get("verb") or "").lower()
            actor_ref = intent.get("actor") or ""
            actor = self._find(encounter_id, actor_ref) if actor_ref else cur
            if actor is None:
                rep.rejections.append({"intent": intent,
                                       "reason": f"No combatant named '{actor_ref}'."})
                continue
            if actor.id != cur.id:
                rep.rejections.append({
                    "intent": intent,
                    "reason": f"It is {cur.name}'s turn, not {actor.name}'s."})
                continue
            if self._conds(actor) & _CANNOT_ACT:
                rep.rejections.append({
                    "intent": intent,
                    "reason": f"{actor.name} is {', '.join(sorted(self._conds(actor) & _CANNOT_ACT))} and cannot act."})
                continue

            handler = getattr(self, f"_do_{verb}", None)
            if handler is None:
                rep.rejections.append({"intent": intent,
                                       "reason": f"Unknown act '{verb}'."})
                continue
            try:
                handler(encounter_id, actor, intent, profiles, rep)
            except _ReactionPause as p:
                self.tracker.set_pending_reaction(encounter_id, p.payload)
                rep.events.append(self._prompt_event(p.payload))
                rep.paused = True
                break

        # Auto-end only when the economy is PROVABLY exhausted — an unspent
        # Action Surge or bonus-action feature keeps the turn open for the
        # player to claim it. A paused fight never advances.
        if rep.paused:
            return rep
        cur = self.tracker.current_combatant(encounter_id)
        if cur is not None and not rep.turn_over:
            fresh = self.tracker.get_combatant(cur.id)
            prof = profiles.get(fresh.character_id) if fresh.character_id else None
            if fresh and fresh.action_used and fresh.move_left <= 0 \
                    and not self._leftover_options(fresh, prof):
                rep.turn_over = True
                rep.turn_over_reason = "action and movement spent"
        if cur is not None:
            fresh = self.tracker.get_combatant(cur.id)
            prof = profiles.get(fresh.character_id) if fresh.character_id else None
            rep.remaining = self._remaining(fresh, prof)
        if rep.turn_over:
            if cur is not None:
                self._end_of_turn_saves(encounter_id, cur, profiles, rep)
            self.tracker.next_turn(encounter_id)
        return rep

    def _end_of_turn_saves(self, encounter_id: int, c: Combatant,
                           profiles: dict[int, PCProfile],
                           rep: TurnReport) -> None:
        """Roll the repeat saves owed at the end of this creature's turn
        (Hold Person, Web, ...). Success ends the condition."""
        fresh = self.tracker.get_combatant(c.id)
        saves = list((fresh.pending_saves if fresh else None) or [])
        if not saves:
            return
        keep: list[dict] = []
        for sv in saves:
            mod = self._ability_mod(fresh, sv.get("ability") or "con", profiles)
            res = saving_throw(mod, dc=int(sv.get("dc") or 10),
                               label=f"{(sv.get('ability') or '?').upper()} save "
                                     f"({fresh.name})", rng=self.rng)
            ev = {"kind": "save", "actor": fresh.name,
                  "condition": sv.get("condition"),
                  "success": bool(res.success),
                  "rolls": [self._roll_dict(
                      f"{(sv.get('ability') or '?').upper()} save — {fresh.name} "
                      f"vs {sv.get('condition')}",
                      res.detail, res.total, dc=int(sv.get("dc") or 10),
                      success=bool(res.success))],
                  "notes": []}
            if res.success:
                self.tracker.remove_condition(fresh.id, sv.get("condition") or "")
                ev["notes"].append(f"shakes off {sv.get('condition')}")
            else:
                keep.append(sv)
            rep.events.append(ev)
        self.tracker.set_pending_saves(fresh.id, keep)

    def _leftover_options(self, c: Combatant,
                          prof: Optional[PCProfile]) -> list[str]:
        """Engine-modeled options this creature could still take this turn."""
        opts: list[str] = []
        if prof is None:
            return opts
        used = [u.lower() for u in (c.used_features or [])]
        if "action surge" in prof.features and "action surge" not in used:
            opts.append("Action Surge")
        if not c.bonus_used:
            for f in ("second wind", "rage"):
                spec = _FEATURES.get(f)
                if f in prof.features and spec and (
                        spec["per_encounter"] is None
                        or used.count(f) < spec["per_encounter"]):
                    opts.append(f.title())
            if "bonus attack" in prof.features and c.action_used:
                opts.append("bonus-action attack")
            if "cunning action" in prof.features:
                opts.append("Cunning Action (Dash/Disengage/Hide)")
        return opts

    # ----- verbs -----

    def _spend_action(self, actor: Combatant, rep: TurnReport,
                      intent: dict, what: str) -> bool:
        fresh = self.tracker.get_combatant(actor.id)
        if fresh.action_used:
            rep.rejections.append({
                "intent": intent,
                "reason": f"{actor.name} has already used their action this turn "
                          f"(wanted: {what}). Movement or a bonus action may remain — "
                          "or end the turn."})
            return False
        self.tracker.update_economy(actor.id, action_used=True)
        return True

    def _do_attack(self, encounter_id, actor, intent, profiles, rep):
        target = self._find(encounter_id, intent.get("target") or "")
        if target is None or target.defeated:
            rep.rejections.append({"intent": intent,
                                   "reason": "No living target by that name."})
            return
        prof = self._attack_profile(actor, intent.get("arg") or "", profiles)
        if prof is None:
            rep.rejections.append({"intent": intent,
                                   "reason": f"{actor.name} has no attack to make."})
            return
        steps = self._steps_between(actor, target)
        if not prof["ranged"] and steps > 0:
            hint = ("move into melee first — a move can close the gap"
                    if steps <= max(0, self.tracker.get_combatant(actor.id).move_left)
                    else "they are too far to reach this turn (move, Dash, or use a ranged attack)")
            rep.rejections.append({
                "intent": intent,
                "reason": f"{target.name} is not in melee reach for "
                          f"{prof['name']} — {hint}."})
            return
        if (target.cover or "none") == "total":
            rep.rejections.append({
                "intent": intent,
                "reason": f"{target.name} has total cover — no line of attack."})
            return
        # Attack budget: monsters get their Multiattack routine per action; PCs
        # get attacks_per_action (Extra Attack). A "bonus attack" feature
        # (two-weapon fighting / Martial Arts) buys one more with the bonus.
        fresh = self.tracker.get_combatant(actor.id)
        pc_prof = profiles.get(actor.character_id) if actor.character_id else None
        allowed = (self._multiattack_count(actor) if actor.monster_slug
                   else (pc_prof.attacks_per_action if pc_prof else 1))
        bonus_note = None
        if not fresh.action_used:
            self.tracker.update_economy(actor.id, action_used=True,
                                        attacks_made=1)
        elif fresh.attacks_made < allowed:
            self.tracker.update_economy(actor.id,
                                        attacks_made=fresh.attacks_made + 1)
        elif (pc_prof and "bonus attack" in pc_prof.features
              and not fresh.bonus_used):
            self.tracker.update_economy(actor.id, bonus_used=True)
            bonus_note = "off-hand / bonus-action attack"
        else:
            left = []
            if not fresh.bonus_used:
                left.append("a bonus action")
            if fresh.move_left > 0:
                left.append("movement")
            rep.rejections.append({
                "intent": intent,
                "reason": f"{actor.name} has no attacks left this turn"
                          + (f" — still available: {', '.join(left)}" if left
                             else " — declare the end of the turn")
                          + "."})
            return

        adv, dis, notes = self._attack_advantage(actor, target, prof["ranged"], encounter_id)
        if bonus_note:
            notes = [bonus_note, *notes]
        # Bless / Bane ride the attack roll as a d4 swing.
        atk_bonus = prof["attack_bonus"]
        a_conds = self._conds(actor)
        if "blessed" in a_conds:
            d4 = damage_roll("1d4", rng=self.rng).total
            atk_bonus += d4
            notes.append(f"Bless +{d4}")
        if "baned" in a_conds:
            d4 = damage_roll("1d4", rng=self.rng).total
            atk_bonus -= d4
            notes.append(f"Bane -{d4}")
        eff_ac = self._eff_ac(target)
        atk = attack_roll(atk_bonus, eff_ac, advantage=adv,
                          disadvantage=dis, label=f"{prof['name']} ({actor.name})",
                          rng=self.rng)
        # May freeze the fight to ask the target's player (Shield).
        self._maybe_prompt_shield(actor, target, atk, eff_ac, profiles,
                                  prof, notes, after=None)
        hit = bool(atk.hit)
        rolls = [self._roll_dict(f"{prof['name']} — {actor.name}", atk.detail,
                                 atk.total, dc=eff_ac, success=hit)]
        ev = {"kind": "attack", "actor": actor.name, "target": target.name,
              "weapon": prof["name"], "hit": hit, "crit": atk.is_crit,
              "notes": notes, "rolls": rolls}
        if "hidden" in self._conds(actor):
            self.tracker.remove_condition(actor.id, "hidden")
        if "helped" in self._conds(actor):
            self.tracker.remove_condition(actor.id, "helped")
        if hit:
            dmg = damage_roll(prof["damage"], crit=atk.is_crit, rng=self.rng)
            total = dmg.total
            rolls.append(self._roll_dict(f"{prof['name']} damage", dmg.detail,
                                         dmg.total, expr=prof["damage"]))

            # Sneak Attack — auto-applied once per turn when the rogue
            # qualifies: finesse/ranged weapon, and either advantage or an
            # able ally engaged with the target (and no disadvantage).
            fresh2 = self.tracker.get_combatant(actor.id)
            if (pc_prof and "sneak attack" in pc_prof.features
                    and not fresh2.sneak_used
                    and (prof.get("finesse") or prof.get("ranged"))
                    and not dis
                    and (adv or self._ally_engaged_with(encounter_id, actor, target))):
                ndice = (pc_prof.level + 1) // 2
                sneak = damage_roll(f"{ndice}d6", crit=atk.is_crit, rng=self.rng)
                total += sneak.total
                rolls.append(self._roll_dict("Sneak Attack", sneak.detail,
                                             sneak.total, expr=f"{ndice}d6"))
                notes.append(f"Sneak Attack +{sneak.total}")
                self.tracker.update_economy(actor.id, sneak_used=True)

            # Divine Smite — a declared rider on a melee hit, fueled by a slot.
            rider = (intent.get("rider") or "").lower()
            if (pc_prof and "divine smite" in pc_prof.features
                    and "smite" in rider and not prof.get("ranged")):
                avail = {lv: n for lv, n in (pc_prof.slots or {}).items() if n > 0}
                m = re.search(r"\d+", rider)
                want = int(m.group()) if m else None
                lv = want if (want and avail.get(want)) else \
                    (min(avail) if avail else None)
                if lv is None:
                    notes.append("wanted to Smite but has no spell slot left")
                else:
                    pc_prof.slots[lv] = max(0, pc_prof.slots[lv] - 1)
                    ndice = min(5, 1 + lv)  # 2d8 at 1st, +1d8/slot level, cap 5d8
                    sm = damage_roll(f"{ndice}d8", crit=atk.is_crit, rng=self.rng)
                    total += sm.total
                    ev["slot_spent"] = lv
                    rolls.append(self._roll_dict(f"Divine Smite (L{lv})",
                                                 sm.detail, sm.total,
                                                 expr=f"{ndice}d8"))
                    notes.append(f"Divine Smite +{sm.total} (level-{lv} slot)")

            # Rage — flat bonus on melee damage while raging.
            if (pc_prof and "rage" in pc_prof.features
                    and not prof.get("ranged")
                    and "raging" in self._conds(actor)):
                rb = 2 if pc_prof.level < 9 else 3 if pc_prof.level < 16 else 4
                total += rb
                notes.append(f"Rage +{rb}")

            total = self._maybe_prompt_uncanny(actor.name, target, total,
                                               profiles, ev_ctx=ev, after=None)
            out = self.tracker.apply_damage(target.id, total)
            ev["damage"] = total
            ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
            if out.get("defeated"):
                ev["defeated"] = True
            if out.get("concentration_check"):
                ev["concentration_dc"] = out.get("concentration_dc")
        rep.events.append(ev)

    def _do_move(self, encounter_id, actor, intent, profiles, rep):
        band_raw = (intent.get("arg") or intent.get("target") or "").strip()
        band = band_raw.lower()
        fresh = self.tracker.get_combatant(actor.id)
        target_c: Optional[Combatant] = None
        if band.startswith("melee"):
            tname = re.sub(r"^melee( with)?", "", band).strip(" |")
            target_c = self._find(encounter_id, tname) if tname else None
            if target_c is None:
                rep.rejections.append({"intent": intent,
                                       "reason": "Move into melee with whom?"})
                return
            cost = self._steps_between(fresh, target_c)
            if cost == 0:
                rep.rejections.append({"intent": intent,
                                       "reason": f"{actor.name} is already in melee with {target_c.name}."})
                return
        elif band in ("near", "far"):
            cost = abs(self._rank(fresh) - _BAND_RANK[band])
            if cost == 0:
                rep.rejections.append({"intent": intent,
                                       "reason": f"{actor.name} is already {band}."})
                return
        else:
            rep.rejections.append({
                "intent": intent,
                "reason": f"Unknown position '{band_raw}' — use 'melee with <name>', 'near', or 'far'."})
            return
        if cost > fresh.move_left:
            need_dash = (not fresh.action_used
                         and cost <= fresh.move_left + 1)
            hint = ("Dash (using the action) would get them there"
                    if need_dash else "not reachable this turn")
            rep.rejections.append({
                "intent": intent,
                "reason": f"{actor.name} has {fresh.move_left} move left but needs "
                          f"{cost} — {hint}."})
            return

        ev = {"kind": "move", "actor": actor.name, "to": band_raw, "rolls": [],
              "notes": []}
        new_pos = f"melee with {target_c.name}" if target_c else band
        # Leaving melee provokes opportunity attacks unless Disengaging. Each
        # OA can freeze the fight for a reaction decision; the frozen payload
        # carries the rest of the move so it completes on resume.
        leaving = self._engaged_enemies(encounter_id, fresh)
        if leaving and not fresh.disengaging:
            for i, enemy in enumerate(leaving):
                if enemy.reaction_used or (self._conds(enemy) & _CANNOT_ACT):
                    continue
                after = {"kind": "move", "actor_id": actor.id,
                         "new_pos": new_pos, "cost": cost,
                         "enemy_ids": [e.id for e in leaving[i + 1:]]}
                self._roll_opportunity_attack(encounter_id, enemy, fresh,
                                              profiles, rep, after=after)
                if (self.tracker.get_combatant(fresh.id) or fresh).defeated:
                    rep.turn_over = True
                    rep.turn_over_reason = f"{actor.name} went down mid-move"
                    return
        elif leaving and fresh.disengaging:
            ev["notes"].append("Disengaged — no opportunity attacks")

        self.tracker.set_position(actor.id, new_pos)
        self.tracker.update_economy(actor.id, move_left=fresh.move_left - cost)
        ev["steps"] = cost
        rep.events.append(ev)

    def _spend_action_or_cunning(self, actor: Combatant, rep: TurnReport,
                                 intent: dict, profiles: dict, what: str) -> Optional[str]:
        """Spend the action; a rogue's Cunning Action can pay with the bonus
        action instead. Returns 'action', 'bonus', or None (rejected)."""
        fresh = self.tracker.get_combatant(actor.id)
        if not fresh.action_used:
            self.tracker.update_economy(actor.id, action_used=True)
            return "action"
        p = profiles.get(actor.character_id) if actor.character_id else None
        if p and "cunning action" in p.features and not fresh.bonus_used \
                and what in ("Dash", "Disengage", "Hide"):
            self.tracker.update_economy(actor.id, bonus_used=True)
            return "bonus"
        rep.rejections.append({
            "intent": intent,
            "reason": f"{actor.name} has already used their action this turn "
                      f"(wanted: {what})."})
        return None

    def _do_dash(self, encounter_id, actor, intent, profiles, rep):
        paid = self._spend_action_or_cunning(actor, rep, intent, profiles, "Dash")
        if not paid:
            return
        fresh = self.tracker.get_combatant(actor.id)
        self.tracker.update_economy(actor.id, move_left=fresh.move_left + 1)
        rep.events.append({"kind": "dash", "actor": actor.name, "rolls": [],
                           "notes": ["Cunning Action"] if paid == "bonus" else []})

    def _do_disengage(self, encounter_id, actor, intent, profiles, rep):
        paid = self._spend_action_or_cunning(actor, rep, intent, profiles, "Disengage")
        if not paid:
            return
        self.tracker.update_economy(actor.id, disengaging=True)
        rep.events.append({"kind": "disengage", "actor": actor.name, "rolls": [],
                           "notes": ["Cunning Action"] if paid == "bonus" else []})

    def _do_dodge(self, encounter_id, actor, intent, profiles, rep):
        if not self._spend_action(actor, rep, intent, "Dodge"):
            return
        self.tracker.update_economy(actor.id, dodging=True)
        rep.events.append({"kind": "dodge", "actor": actor.name, "rolls": []})

    def _do_feature(self, encounter_id, actor, intent, profiles, rep):
        """Activate a class feature the engine models mechanically:
        Action Surge (regain the action), Second Wind (bonus, 1d10+level HP),
        Rage (bonus, 'raging' condition)."""
        name = (intent.get("arg") or intent.get("target") or "").strip().lower()
        spec = _FEATURES.get(name)
        p = profiles.get(actor.character_id) if actor.character_id else None
        if spec is None:
            rep.rejections.append({
                "intent": intent,
                "reason": f"'{name or 'that feature'}' isn't a feature the engine "
                          "resolves — describe it as an improvised act instead."})
            return
        if p is None or name not in p.features:
            rep.rejections.append({
                "intent": intent,
                "reason": f"{actor.name} doesn't have {name.title()}."})
            return
        fresh = self.tracker.get_combatant(actor.id)
        used = [u.lower() for u in (fresh.used_features or [])]
        if spec["per_encounter"] is not None \
                and used.count(name) >= spec["per_encounter"]:
            rep.rejections.append({
                "intent": intent,
                "reason": f"{actor.name} has already used {name.title()} this fight."})
            return
        if spec["cost"] == "bonus":
            if fresh.bonus_used:
                rep.rejections.append({
                    "intent": intent,
                    "reason": f"{actor.name}'s bonus action is already spent."})
                return
            self.tracker.update_economy(actor.id, bonus_used=True)
        ev = {"kind": "feature", "actor": actor.name, "feature": name.title(),
              "rolls": [], "notes": []}
        if name == "action surge":
            self.tracker.update_economy(actor.id, action_used=False,
                                        attacks_made=0)
            ev["notes"].append("regains their action")
        if spec.get("heal"):
            expr = spec["heal"].format(level=p.level)
            r = damage_roll(expr, rng=self.rng)
            out = self.tracker.heal(actor.id, r.total)
            ev["rolls"].append(self._roll_dict(name.title(), r.detail, r.total,
                                               expr=expr))
            ev["notes"].append(f"regains {r.total} HP "
                               f"({out['current_hp']}/{out['max_hp']})")
        if spec.get("condition"):
            self.tracker.add_condition(actor.id, spec["condition"])
            ev["notes"].append(spec["condition"])
        self.tracker.update_economy(actor.id, used_features=[*used, name])
        rep.events.append(ev)

    def _do_help(self, encounter_id, actor, intent, profiles, rep):
        ally = self._find(encounter_id, intent.get("target") or "")
        if ally is None or ally.defeated:
            rep.rejections.append({"intent": intent, "reason": "Help whom?"})
            return
        if not self._spend_action(actor, rep, intent, "Help"):
            return
        self.tracker.add_condition(ally.id, "helped")
        rep.events.append({"kind": "help", "actor": actor.name,
                           "target": ally.name, "rolls": [],
                           "notes": [f"{ally.name}'s next attack has advantage"]})

    def _do_hide(self, encounter_id, actor, intent, profiles, rep):
        if not self._spend_action(actor, rep, intent, "Hide"):
            return
        mod = self._ability_mod(actor, "dex", profiles)
        if actor.character_id and actor.character_id in profiles \
                and "stealth" in profiles[actor.character_id].skills:
            mod += profiles[actor.character_id].prof
        # Contested by the sharpest enemy's passive Perception.
        best_pp = 10
        for other in self.tracker.order(encounter_id):
            if other.defeated or other.id == actor.id \
                    or self._side(other) == self._side(actor):
                continue
            best_pp = max(best_pp, 10 + self._ability_mod(other, "wis", profiles))
        chk = ability_check(mod, dc=best_pp, label=f"Stealth ({actor.name})",
                            rng=self.rng)
        rolls = [self._roll_dict(f"Stealth — {actor.name}", chk.detail,
                                 chk.total, dc=best_pp, success=bool(chk.success))]
        ev = {"kind": "hide", "actor": actor.name, "success": bool(chk.success),
              "rolls": rolls, "notes": []}
        if chk.success:
            self.tracker.add_condition(actor.id, "hidden")
            ev["notes"].append("hidden — next attack has advantage")
        rep.events.append(ev)

    def _contest(self, encounter_id, actor, target, profiles,
                 label) -> tuple[bool, list[dict]]:
        a_mod = self._ability_mod(actor, "str", profiles)
        if actor.character_id and actor.character_id in profiles \
                and "athletics" in profiles[actor.character_id].skills:
            a_mod += profiles[actor.character_id].prof
        t_mod = max(self._ability_mod(target, "str", profiles),
                    self._ability_mod(target, "dex", profiles))
        a = ability_check(a_mod, label=f"{label} ({actor.name})", rng=self.rng)
        t = ability_check(t_mod, label=f"contest ({target.name})", rng=self.rng)
        rolls = [self._roll_dict(f"{label} — {actor.name}", a.detail, a.total),
                 self._roll_dict(f"Contest — {target.name}", t.detail, t.total)]
        return a.total > t.total, rolls

    def _do_grapple(self, encounter_id, actor, intent, profiles, rep):
        target = self._find(encounter_id, intent.get("target") or "")
        if target is None or target.defeated:
            rep.rejections.append({"intent": intent, "reason": "Grapple whom?"})
            return
        if self._steps_between(actor, target) > 0:
            rep.rejections.append({
                "intent": intent,
                "reason": f"{target.name} is out of reach — move into melee first."})
            return
        if not self._spend_action(actor, rep, intent, "Grapple"):
            return
        won, rolls = self._contest(encounter_id, actor, target, profiles, "Grapple")
        ev = {"kind": "grapple", "actor": actor.name, "target": target.name,
              "success": won, "rolls": rolls, "notes": []}
        if won:
            self.tracker.add_condition(target.id, "grappled")
        rep.events.append(ev)

    def _do_shove(self, encounter_id, actor, intent, profiles, rep):
        target = self._find(encounter_id, intent.get("target") or "")
        if target is None or target.defeated:
            rep.rejections.append({"intent": intent, "reason": "Shove whom?"})
            return
        if self._steps_between(actor, target) > 0:
            rep.rejections.append({
                "intent": intent,
                "reason": f"{target.name} is out of reach — move into melee first."})
            return
        if not self._spend_action(actor, rep, intent, "Shove"):
            return
        won, rolls = self._contest(encounter_id, actor, target, profiles, "Shove")
        ev = {"kind": "shove", "actor": actor.name, "target": target.name,
              "success": won, "rolls": rolls, "notes": []}
        if won:
            mode = (intent.get("arg") or "prone").lower()
            if "push" in mode or "back" in mode:
                self.tracker.set_position(target.id, "near")
                ev["notes"].append(f"{target.name} shoved back out of melee")
            else:
                self.tracker.add_condition(target.id, "prone")
                ev["notes"].append(f"{target.name} knocked prone")
        rep.events.append(ev)

    def _do_use(self, encounter_id, actor, intent, profiles, rep):
        item = (intent.get("arg") or intent.get("target") or "").strip()
        low = item.lower()
        if not self._spend_action(actor, rep, intent, f"use {item or 'an item'}"):
            return
        ev = {"kind": "use", "actor": actor.name, "item": item, "rolls": [],
              "notes": []}
        healed = next((expr for k, expr in _CONSUMABLE_HEALS.items() if k in low), None)
        temp = next((n for k, n in _CONSUMABLE_TEMPS.items() if k in low), None)
        if healed:
            r = damage_roll(healed, rng=self.rng)
            out = self.tracker.heal(actor.id, r.total)
            ev["rolls"].append(self._roll_dict(item, r.detail, r.total, expr=healed))
            ev["notes"].append(f"regains {r.total} HP "
                               f"({out['current_hp']}/{out['max_hp']})")
        elif temp:
            self.tracker.set_temp_hp(actor.id, temp)
            ev["notes"].append(f"gains {temp} temporary hit points")
        else:
            ev["notes"].append("effect adjudicated in narration")
        rep.events.append(ev)

    def _do_cast(self, encounter_id, actor, intent, profiles, rep):
        spell_name = (intent.get("arg") or "").strip()
        targets = self._resolve_targets(encounter_id, actor,
                                        intent.get("target") or "")
        target = targets[0] if targets else None
        with Session(self.tracker.engine) as s:
            sp = s.exec(select(Spell).where(
                Spell.name.ilike(spell_name))).first() if spell_name else None
        prof = profiles.get(actor.character_id) if actor.character_id else None

        # Leveled spells consume a real slot; cantrips are free. Rejection
        # happens BEFORE any economy is spent so the turn stays intact.
        slot_spent: Optional[int] = None
        if sp is not None and (sp.level or 0) >= 1 and prof is not None:
            want = None
            m = re.search(r"\d+", intent.get("slot") or "")
            if m:
                want = int(m.group())
            avail = {lv: n for lv, n in (prof.slots or {}).items()
                     if n > 0 and lv >= sp.level}
            if not avail:
                have = ", ".join(f"L{lv}×{n}" for lv, n in
                                 sorted((prof.slots or {}).items()) if n > 0)
                rep.rejections.append({
                    "intent": intent,
                    "reason": f"{actor.name} has no spell slot for {sp.name} "
                              f"(needs level {sp.level}+; remaining: "
                              f"{have or 'none'}). A cantrip is always free."})
                return
            if want is not None:
                if avail.get(want):
                    slot_spent = want
                else:
                    rep.rejections.append({
                        "intent": intent,
                        "reason": f"{actor.name} has no level-{want} slot left "
                                  f"for {sp.name} — available: "
                                  + ", ".join(f"L{lv}×{n}" for lv, n in sorted(avail.items()))
                                  + "."})
                    return
            else:
                slot_spent = min(avail)

        bonus_cast = bool(sp and "bonus" in (sp.casting_time or "").lower())
        fresh = self.tracker.get_combatant(actor.id)
        if bonus_cast:
            if fresh.bonus_used:
                rep.rejections.append({
                    "intent": intent,
                    "reason": f"{actor.name} has already used their bonus action."})
                return
            self.tracker.update_economy(actor.id, bonus_used=True)
        else:
            if not self._spend_action(actor, rep, intent,
                                      f"cast {spell_name or 'a spell'}"):
                return
        ev = {"kind": "cast", "actor": actor.name, "spell": spell_name or "a spell",
              "target": target.name if target else None, "rolls": [], "notes": []}
        if slot_spent is not None:
            prof.slots[slot_spent] = max(0, prof.slots.get(slot_spent, 0) - 1)
            ev["slot_spent"] = slot_spent
            up = f" (upcast at level {slot_spent})" if slot_spent > sp.level else ""
            ev["notes"].append(f"level-{slot_spent} slot spent{up}; "
                               f"{prof.slots[slot_spent]} left")
        dmg_expr = self._spell_damage(sp, prof, slot=slot_spent)
        name_l = (sp.name if sp else spell_name).strip().lower()
        eff = _SPELL_EFFECTS.get(name_l)
        base_lv = (sp.level if sp else 1) or 1

        if eff and eff.get("missiles") and target is not None:
            # Magic Missile: auto-hit darts, +1 per slot level above 1st.
            darts = 3 + max(0, (slot_spent or base_lv) - base_lv)
            expr = f"{darts}d4+{darts}"
            dmg = damage_roll(expr, rng=self.rng)
            out = self.tracker.apply_damage(target.id, dmg.total)
            ev["rolls"].append(self._roll_dict(
                f"{sp.name} ({darts} darts)", dmg.detail, dmg.total, expr=expr))
            ev["damage"] = dmg.total
            ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
            ev["notes"].append("auto-hit")
            if out.get("defeated"):
                ev["defeated"] = True
        elif eff and eff.get("heal"):
            tgt = target or actor
            n = 1 + max(0, (slot_spent or base_lv) - base_lv)
            mod = (prof.ability_mods.get(prof.spell_mod, 0)
                   if prof and prof.spell_mod
                   else self._ability_mod(actor, "wis", profiles))
            expr = f"{n}{eff['heal']}" + (f"{mod:+d}" if mod else "")
            r = damage_roll(expr, rng=self.rng)
            out = self.tracker.heal(tgt.id, r.total)
            ev["rolls"].append(self._roll_dict(
                f"{sp.name if sp else spell_name} — healing", r.detail,
                r.total, expr=expr))
            ev["notes"].append(f"{tgt.name} regains {r.total} HP "
                               f"({out['current_hp']}/{out['max_hp']})")
        elif sp and sp.attack_type and target is not None:
            bonus = (prof.spell_attack_bonus if prof and
                     prof.spell_attack_bonus is not None
                     else 2 + self._ability_mod(actor, "cha", profiles))
            adv, dis, notes = self._attack_advantage(
                actor, target, sp.attack_type != "melee", encounter_id)
            eff_ac = self._eff_ac(target)
            atk = attack_roll(bonus, eff_ac, advantage=adv, disadvantage=dis,
                              label=f"{sp.name} ({actor.name})", rng=self.rng)
            self._maybe_prompt_shield(actor, target, atk, eff_ac, profiles,
                                      {"name": sp.name, "damage": dmg_expr or "1"},
                                      notes, after=None)
            s_hit = bool(atk.hit)
            ev["rolls"].append(self._roll_dict(
                f"{sp.name} — {actor.name}", atk.detail, atk.total,
                dc=eff_ac, success=s_hit))
            ev["notes"].extend(notes)
            ev["hit"] = s_hit
            if s_hit and dmg_expr:
                dmg = damage_roll(dmg_expr, crit=atk.is_crit, rng=self.rng)
                out = self.tracker.apply_damage(target.id, dmg.total)
                ev["rolls"].append(self._roll_dict(
                    f"{sp.name} damage", dmg.detail, dmg.total, expr=dmg_expr))
                ev["damage"] = dmg.total
                ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
                if out.get("defeated"):
                    ev["defeated"] = True
        elif sp and sp.dc_type and targets:
            # Registry target cap (upcasting may widen it); AoE spells carry
            # no cap — the narration decides who stands in the area, the
            # engine rolls every save.
            if eff and eff.get("targets"):
                cap = eff["targets"] + (max(0, (slot_spent or base_lv) - base_lv)
                                        if eff.get("upcast_targets") else 0)
                if len(targets) > cap:
                    ev["notes"].append(f"only {cap} target"
                                       f"{'s' if cap != 1 else ''} — extras dropped")
                    targets = targets[:cap]
            dc = (prof.spell_dc if prof and prof.spell_dc is not None
                  else 10 + self._ability_mod(actor, "cha", profiles))
            # One damage roll shared by every creature in the effect (RAW).
            shared = damage_roll(dmg_expr, rng=self.rng) if dmg_expr else None
            if shared is not None:
                ev["rolls"].append(self._roll_dict(
                    f"{sp.name} damage", shared.detail, shared.total,
                    expr=dmg_expr))
            results: list[dict] = []
            for tgt in targets:
                t_mod = self._ability_mod(tgt, sp.dc_type, profiles)
                save = saving_throw(t_mod, dc=dc,
                                    label=f"{sp.dc_type.upper()} save ({tgt.name})",
                                    rng=self.rng)
                ev["rolls"].append(self._roll_dict(
                    f"{sp.dc_type.upper()} save — {tgt.name}", save.detail,
                    save.total, dc=dc, success=bool(save.success)))
                res: dict = {"target": tgt.name, "saved": bool(save.success)}
                if not save.success and eff and eff.get("save_condition"):
                    cond = eff["save_condition"]
                    self.tracker.add_condition(tgt.id, cond)
                    res["condition"] = cond
                    if eff.get("repeat_save"):
                        fresh_t = self.tracker.get_combatant(tgt.id)
                        saves = list(fresh_t.pending_saves or [])
                        saves.append({"condition": cond,
                                      "ability": sp.dc_type, "dc": dc})
                        self.tracker.set_pending_saves(tgt.id, saves)
                if shared is not None:
                    total = shared.total
                    if save.success and (sp.dc_success or "").lower() == "half":
                        total //= 2
                    elif save.success:
                        total = 0
                    if total > 0:
                        out = self.tracker.apply_damage(tgt.id, total)
                        res["damage"] = total
                        res["hp"] = f"{out['current_hp']}/{out['max_hp']}"
                        if out.get("defeated"):
                            res["defeated"] = True
                results.append(res)
            ev["results"] = results
            if len(results) == 1:
                # legacy single-target shape for the renderer/narration
                r0 = results[0]
                ev["saved"] = r0["saved"]
                if r0.get("damage") is not None:
                    ev["damage"] = r0["damage"]
                    ev["target_hp"] = r0.get("hp")
                    if r0["saved"]:
                        ev["notes"].append("save: half damage")
                elif r0["saved"] and shared is not None:
                    ev["notes"].append("save: no effect")
                if r0.get("defeated"):
                    ev["defeated"] = True
                if r0.get("condition"):
                    ev["notes"].append(f"{r0['target']} is {r0['condition']}")
                    if eff and eff.get("repeat_save"):
                        ev["notes"].append("repeat save at the end of its turns")
        elif eff and eff.get("teleport"):
            self.tracker.set_position(actor.id, "near")
            ev["notes"].append("teleports to safety — no opportunity attacks")
        elif eff and eff.get("ally_condition") and targets:
            cap = (eff.get("targets") or len(targets)) + \
                (max(0, (slot_spent or base_lv) - base_lv)
                 if eff.get("upcast_targets") else 0)
            if len(targets) > cap:
                ev["notes"].append(f"only {cap} targets — extras dropped")
                targets = targets[:cap]
            for tgt in targets:
                self.tracker.add_condition(tgt.id, eff["ally_condition"])
            names = ", ".join(t.name for t in targets)
            verb = "are" if len(targets) > 1 else "is"
            ev["notes"].append(f"{names} {verb} {eff['ally_condition']}")
        else:
            ev["notes"].append("effect adjudicated in narration")
            # An unregistered concentration spell on a target still leaves a
            # visible mark on the board so it isn't forgotten.
            if sp and sp.concentration and target is not None:
                self.tracker.add_condition(target.id, name_l)
                ev["notes"].append(f"{target.name} tagged: {name_l}")
        if sp and sp.concentration:
            self.tracker.set_concentration(actor.id, sp.name)
            ev["notes"].append(f"concentrating on {sp.name}")
        rep.events.append(ev)

    def _spell_damage(self, sp: Optional[Spell], prof: Optional[PCProfile],
                      slot: Optional[int] = None) -> Optional[str]:
        if sp is None or not isinstance(sp.damage, dict):
            return None
        lvl = prof.level if prof else 1
        slots = sp.damage.get("damage_at_slot_level") or {}
        chars = sp.damage.get("damage_at_character_level") or {}
        if slots:
            # Upcasting: use the spent slot's row (best row at or below it);
            # no known slot -> the base row.
            if slot is None:
                key = min(slots.keys(), key=lambda k: int(k))
            else:
                eligible = [int(k) for k in slots.keys() if int(k) <= slot]
                key = str(max(eligible)) if eligible \
                    else min(slots.keys(), key=lambda k: int(k))
            return slots[key]
        if chars:
            eligible = [int(k) for k in chars.keys() if int(k) <= max(1, lvl)]
            key = str(max(eligible)) if eligible else min(chars.keys(), key=lambda k: int(k))
            return chars[key]
        return None

    def _do_improvise(self, encounter_id, actor, intent, profiles, rep):
        desc = (intent.get("arg") or "").strip()
        ev = {"kind": "improvise", "actor": actor.name, "desc": desc,
              "rolls": [], "notes": ["adjudicated in narration"]}
        m = re.search(r"(str|dex|con|int|wis|cha)[a-z]*\s+(?:check\s+)?"
                      r"(?:vs|dc)\s*(\d+)", desc.lower())
        if m:
            mod = self._ability_mod(actor, m.group(1), profiles)
            chk = ability_check(mod, dc=int(m.group(2)),
                                label=f"{m.group(1).upper()} check ({actor.name})",
                                rng=self.rng)
            ev["rolls"].append(self._roll_dict(
                f"{m.group(1).upper()} check — {actor.name}", chk.detail,
                chk.total, dc=int(m.group(2)), success=bool(chk.success)))
            ev["success"] = bool(chk.success)
        rep.events.append(ev)

    def _do_end_turn(self, encounter_id, actor, intent, profiles, rep):
        rep.turn_over = True
        rep.turn_over_reason = f"{actor.name} ends their turn"

    # ---------------- monster autopilot ----------------

    def run_monster_turn(self, encounter_id: int,
                         intents: Optional[list[dict]] = None,
                         profiles: Optional[dict[int, PCProfile]] = None) -> TurnReport:
        """Resolve the current (non-PC) creature's whole turn: proposed intents
        first; if none land, a default AI acts. Always advances the turn."""
        profiles = profiles or {}
        cur = self.tracker.current_combatant(encounter_id)
        rep = TurnReport()
        if cur is None or cur.kind == "pc":
            rep.rejections.append({"reason": "Not a monster's turn."})
            return rep
        if cur.defeated or (self._conds(cur) & _CANNOT_ACT):
            rep.events.append({"kind": "skip", "actor": cur.name, "rolls": [],
                               "notes": ["cannot act"]})
            # A held/stunned creature still gets its end-of-turn repeat saves.
            self._end_of_turn_saves(encounter_id, cur, profiles, rep)
            self.tracker.next_turn(encounter_id)
            rep.turn_over = True
            return rep

        if intents:
            rep = self.resolve(encounter_id, intents, profiles)
            if rep.paused:
                return rep
        if not rep.events and not rep.turn_over:
            # Default AI: hit whoever is in reach, else close and swing.
            pcs = [c for c in self.tracker.order(encounter_id)
                   if c.kind == "pc" and not c.defeated]
            if pcs:
                tgt = min(pcs, key=lambda p: (self._steps_between(cur, p),
                                              p.current_hp))
                steps = self._steps_between(cur, tgt)
                seq: list[dict] = []
                has_ranged = any(a["ranged"] for a in self._monster_attacks(cur))
                if steps == 0:
                    seq = [{"verb": "attack", "actor": cur.name, "target": tgt.name}]
                elif steps <= 1:
                    seq = [{"verb": "move", "actor": cur.name,
                            "arg": f"melee with {tgt.name}"},
                           {"verb": "attack", "actor": cur.name, "target": tgt.name}]
                elif has_ranged:
                    seq = [{"verb": "attack", "actor": cur.name, "target": tgt.name,
                            "arg": "ranged"}]
                else:
                    seq = [{"verb": "dash", "actor": cur.name},
                           {"verb": "move", "actor": cur.name, "arg": "near"}]
                rep = self.resolve(encounter_id, seq, profiles)
        if rep.paused:
            return rep
        if not rep.turn_over:
            cur2 = self.tracker.current_combatant(encounter_id)
            if cur2 is not None:
                self._end_of_turn_saves(encounter_id, cur2, profiles, rep)
            self.tracker.next_turn(encounter_id)
            rep.turn_over = True
        return rep

    # ---------------- report rendering ----------------

    @staticmethod
    def render_report(rep: TurnReport) -> str:
        """Certified-results text for the narration prompt."""
        lines: list[str] = []
        for e in rep.events:
            k = e["kind"]
            if k in ("attack", "opportunity_attack", "cast"):
                what = e.get("weapon") or e.get("spell") or "attack"
                head = ("OPPORTUNITY ATTACK" if k == "opportunity_attack"
                        else "CAST" if k == "cast" else "ATTACK")
                if "hit" in e:
                    res = "HIT" if e["hit"] else "MISS"
                    if e.get("crit"):
                        res = "CRITICAL HIT"
                    line = (f"{head}: {e['actor']} — {what} vs "
                            f"{e.get('target') or '—'}: {res}")
                elif "saved" in e:
                    line = (f"{head}: {e['actor']} — {what} vs {e.get('target')}: "
                            f"{'SAVED' if e['saved'] else 'FAILED SAVE'}")
                else:
                    line = f"{head}: {e['actor']} — {what}"
                if e.get("damage") is not None:
                    line += f", {e['damage']} damage ({e.get('target_hp', '?')} HP)"
                if e.get("defeated"):
                    line += f" — {e['target']} goes DOWN"
                if e.get("concentration_dc"):
                    line += (f" [concentration check DC "
                             f"{e['concentration_dc']} pending]")
                if e.get("notes"):
                    line += f" [{'; '.join(e['notes'])}]"
                if e.get("results") and len(e["results"]) > 1:
                    for r in e["results"]:
                        sub = (f"  - {r['target']}: "
                               f"{'SAVED' if r['saved'] else 'FAILED SAVE'}")
                        if r.get("damage") is not None:
                            sub += f", {r['damage']} damage ({r.get('hp', '?')} HP)"
                        if r.get("condition"):
                            sub += f", now {r['condition']}"
                        if r.get("defeated"):
                            sub += " — DOWN"
                        line += "\n" + sub
                lines.append(line)
            elif k == "reaction":
                lines.append(f"REACTION: {e['actor']} — {e.get('spell')}"
                             f" ({'; '.join(e.get('notes') or [])})")
            elif k == "reaction_prompt":
                q = "; ".join(e.get("notes") or [])
                opts = " / ".join(e.get("options") or [])
                lines.append(f"REACTION? {q} Options: {opts}.")
            elif k == "note":
                lines.append(f"NOTE: {'; '.join(e.get('notes') or [])}")
            elif k == "move":
                n = f" ({'; '.join(e['notes'])})" if e.get("notes") else ""
                lines.append(f"MOVE: {e['actor']} -> {e['to']}{n}")
            elif k in ("dash", "dodge", "disengage"):
                lines.append(f"{k.upper()}: {e['actor']}")
            elif k in ("grapple", "shove", "hide"):
                res = "succeeds" if e.get("success") else "fails"
                n = f" ({'; '.join(e['notes'])})" if e.get("notes") else ""
                lines.append(f"{k.upper()}: {e['actor']}"
                             + (f" vs {e['target']}" if e.get("target") else "")
                             + f" — {res}{n}")
            elif k == "save":
                res = (f"shakes off {e.get('condition')}" if e.get("success")
                       else f"still {e.get('condition')}")
                lines.append(f"SAVE: {e['actor']} — {res}")
            elif k == "feature":
                n = f" — {'; '.join(e['notes'])}" if e.get("notes") else ""
                lines.append(f"FEATURE: {e['actor']} uses {e['feature']}{n}")
            elif k == "help":
                lines.append(f"HELP: {e['actor']} aids {e['target']} "
                             f"({'; '.join(e.get('notes') or [])})")
            elif k == "use":
                n = f" — {'; '.join(e['notes'])}" if e.get("notes") else ""
                lines.append(f"USE: {e['actor']} uses {e.get('item')}{n}")
            elif k == "improvise":
                res = ("" if e.get("success") is None
                       else f" — {'succeeds' if e['success'] else 'fails'}")
                lines.append(f"IMPROVISED: {e['actor']}: {e.get('desc')}{res}")
            elif k == "skip":
                lines.append(f"SKIP: {e['actor']} ({'; '.join(e.get('notes') or [])})")
        for r in rep.rejections:
            lines.append(f"REFUSED: {r['reason']}")
        if rep.paused:
            lines.append("FIGHT PAUSED — nothing else resolves until the "
                         "reaction question above is answered (or declined).")
            return "\n".join(lines)
        if rep.turn_over:
            lines.append(f"TURN OVER ({rep.turn_over_reason or 'ended'})")
        elif rep.remaining:
            rem = rep.remaining
            bits = []
            if rem.get("action"):
                bits.append("action")
            if rem.get("bonus"):
                bits.append("bonus action")
            if rem.get("move_steps"):
                bits.append(f"movement ({rem['move_steps']} step"
                            f"{'s' if rem['move_steps'] != 1 else ''})")
            for opt in rem.get("options") or []:
                bits.append(opt)
            lines.append("TURN STILL OPEN — remaining: "
                         + (", ".join(bits) if bits else "nothing (declare end of turn)"))
        return "\n".join(lines)
