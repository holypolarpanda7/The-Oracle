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
from rules import damage as dmgtypes
from rules.models import Monster, Spell

from .models import Awareness, Combatant
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
    # Normal / long range in feet (a longbow is 150/600). Only meaningful with a
    # board out — without exact distance the engine can't police them, so they
    # are simply ignored.
    range_normal: Optional[int] = None
    range_long: Optional[int] = None
    # ---- which hand this is in (rules/equipment.py) ----------------------
    #: "main" / "off" / "both", or None when it is not in a hand at all. The
    #: engine never derives this: the backend reads it off the loadout, which
    #: is the only thing that knows a sword in the pack from one in a fist.
    grip: Optional[str] = None
    #: True when this PC is holding SOMETHING and this weapon isn't it. A PC
    #: holding nothing has every weapon available exactly as before, the same
    #: "unknown, not empty-handed" rule the material-component check uses.
    stowed: bool = False
    light: bool = False
    two_handed: bool = False
    #: A thrown weapon LEAVES the hand: it can be attacked with at range, and
    #: afterwards it is on the floor or in the target rather than in a fist.
    thrown: bool = False
    throw_normal: Optional[int] = None
    throw_long: Optional[int] = None
    #: The 2024 Weapon Mastery this weapon carries AND this character has
    #: chosen — resolved by the backend through ``rules/mastery.py``, because
    #: the assignment is book data the engine must not have to read. None when
    #: mastery is off, the weapon has none, or it wasn't chosen.
    mastery: Optional[str] = None
    #: The ability modifier THIS weapon's attack roll uses, already decided by
    #: the backend (finesse picks the better of Str/Dex, ranged takes Dex).
    #: Graze and Topple both say "the ability modifier used to make the attack
    #: roll", and guessing it from the profile got a finesse weapon wrong.
    ability_mod: int = 0
    #: What the weapon deals. Graze's damage "is the same type dealt by the
    #: weapon", which is the only place this engine has ever needed a type.
    damage_type: Optional[str] = None
    #: The damage dice with NO ability modifier — what Cleave's second target
    #: takes, and the same expression the off-hand swing uses.
    damage_flat: Optional[str] = None
    #: Damage for the two-weapon bonus swing — the ability modifier is dropped
    #: unless the Two-Weapon Fighting style is taken (or the modifier is
    #: negative, which you never get to ignore). Set only on the off hand.
    offhand_damage: Optional[str] = None


def _mark_last_weapon(prof: dict) -> str:
    """``Combatant.last_weapon`` for a resolved attack: ``"<grip>:<name>"``.

    The grip is what matters and the name is what a reader wants, so both are
    stored. Two identical shortswords are two weapons with one name, and the
    two-weapon rule is about the OTHER HAND — matching on the name alone would
    decide that a duellist had already used both of them.
    """
    return f"{prof.get('grip') or ''}:{prof.get('name') or ''}"


def _split_last_weapon(raw: Optional[str]) -> tuple[str, str]:
    grip, _, name = (raw or "").partition(":")
    return grip.strip().lower(), name.strip().lower()


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
    # Abilities this PC is PROFICIENT in the saving throws of ("str"/"con"/…):
    # the class's two, plus anything that granted another (Resilient). Empty
    # means no save proficiencies, which costs the PC their bonus on every save
    # — so a backend that stops filling this in silently weakens every PC.
    save_profs: set[str] = field(default_factory=set)
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
    # 2024 Exhaustion level (0-6): applies -2 x level to this PC's D20 Tests
    # (attack rolls, saving throws, ability checks) — never to DCs or damage.
    exhaustion: int = 0
    # Creature type (targeting: Hold/Charm Person only affect Humanoids) and the
    # set of normalized condition immunities from species traits.
    creature_type: str = "Humanoid"
    immunities: set[str] = field(default_factory=set)
    # Curse-enforced numbers: a flat penalty to this PC's D20 Tests, and whether a
    # curse blocks HP regain (both derived from active curses by the backend).
    curse_pen: int = 0
    no_heal: bool = False


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
# 2024: Exhaustion is no longer a disadvantage tag — it's a -2 x level penalty to
# every D20 Test, applied numerically via CombatEngine._exh_pen.
_ATTACKER_DISADV = {"poisoned", "prone", "restrained", "blinded", "frightened"}

# Spells that only affect Humanoids — a Construct/Undead/etc. target is simply not
# a legal target (resisting by type, not by a save).
_HUMANOID_ONLY_SPELLS = {"hold person", "charm person", "dominate person"}
_TARGET_GIVES_ADV = {"restrained", "stunned", "paralyzed", "unconscious",
                     "petrified", "blinded", "faerie fire"}
_CANNOT_ACT = {"incapacitated", "stunned", "paralyzed", "unconscious", "petrified"}

# --- underwater combat (PHB) ---------------------------------------------
# Water fights the swing, so a weapon has to be one you THRUST rather than one
# you swing. A creature with a swimming speed is at home and exempt from the
# melee rule entirely; nothing exempts you from the ranged one, because the
# problem there is the water slowing the missile, not your footing.
_UNDERWATER_MELEE_OK = ("dagger", "javelin", "shortsword", "short sword",
                        "spear", "trident")
# Crossbows are wound, not drawn, and a net and a thrown spear both work wet.
_UNDERWATER_RANGED_OK = ("crossbow", "net", "javelin", "spear", "trident", "dart")


def _weapon_matches(name: str, allowed: tuple[str, ...]) -> bool:
    """Is this weapon on an underwater allowlist?

    Substring rather than exact match, deliberately: the list is of weapon
    KINDS and the table is full of "Trident of Warning" and "+1 Shortsword".
    A magic shortsword is still a shortsword, and refusing to notice that
    would punish exactly the players who found the good weapon.
    """
    n = (name or "").strip().lower()
    return any(w in n for w in allowed)

_BAND_RANK = {"near": 1, "far": 2}


def _mod_key(name: str) -> str:
    return (name or "")[:3].lower()


def monster_save_mod(mon: Any, ability: str) -> Optional[int]:
    """The saving-throw modifier a stat block PRINTS for this ability, or None.

    The SRD stores it in ``proficiencies`` as
    ``{"value": 10, "proficiency": {"name": "Saving Throw: CON"}}`` and that
    value is the TOTAL — ability modifier and proficiency already added — so it
    is used as-is and proficiency must never be added on top. None means the
    stat block lists no save for this ability, which is not a gap: a creature
    without the proficiency really does roll the bare modifier.

    Book-parsed monsters store proficiencies in a different shape that carries
    skills only, so they come back None here and roll unproficient. That is a
    parse gap in ``rules/owned_ingest.py``, not a rule.
    """
    key = _mod_key(ability)
    for p in (getattr(mon, "proficiencies", None) or []):
        if not isinstance(p, dict):
            continue
        name = str((p.get("proficiency") or {}).get("name") or "")
        if "saving throw" not in name.lower():
            continue
        if _mod_key(name.split(":")[-1].strip()) == key:
            try:
                return int(p.get("value"))
            except (TypeError, ValueError):
                return None
    return None


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
        # Environmental combat aura for the current turn (set per top-level call from
        # the location's arcane sites): {d20, spell_d20, spell_dc, hazards:[...]}.
        self._env: dict = {}
        # Optional exact-position provider (a tactical board — see vtt/bridge.py).
        # When one is attached AND it can answer for both creatures, spacing is
        # measured in real feet; otherwise the gridless bands below still rule,
        # so a table with no board plays exactly as it always has.
        #   distance_ft(a, b) -> Optional[int]
        #   reach_ft(c)       -> int
        self.spatial = None

    # ---------------- band / spacing model ----------------

    def _spatial_gap(self, a: Combatant, b: Combatant) -> Optional[tuple[int, int]]:
        """(distance in feet, the larger of the two reaches) — or None."""
        if self.spatial is None:
            return None
        try:
            d = self.spatial.distance_ft(a, b)
            if d is None:
                return None
            reach = max(self.spatial.reach_ft(a), self.spatial.reach_ft(b))
            return int(d), int(reach)
        except Exception:
            return None

    def _engaged_with(self, a: Combatant, b: Combatant) -> bool:
        gap = self._spatial_gap(a, b)
        if gap is not None:
            return gap[0] <= gap[1]
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
        """Band-steps between two creatures — 0 means "in reach".

        With a board out this is derived from the real distance (a step is one
        normal move, ~30 ft), so "can I reach them this turn?" answers honestly
        instead of rounding everything to near/far.
        """
        gap = self._spatial_gap(a, b)
        if gap is not None:
            dist, reach = gap
            if dist <= reach:
                return 0
            return max(1, -(-(dist - reach) // 30))    # ceil to whole moves
        if self._engaged_with(a, b):
            return 0
        return max(1, abs(self._rank(a) - self._rank(b)))

    @staticmethod
    def _side(c: Combatant) -> str:
        """Which side a creature fights for.

        ``Combatant.side`` when it is set — a conjured spirit is a monster row
        that fights for the party, and nothing else in the schema could say so.
        Unset falls back to the original rule (PCs are one side, everything else
        the other), which is right for every fight that has no allies in it.
        """
        return getattr(c, "side", None) or ("party" if c.kind == "pc" else "foe")

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
            # EVERY damage entry, not the first: a dragon's bite is piercing
            # PLUS acid, and reading one of them dropped the rider damage on
            # the floor along with both types. The extras ride as their own
            # typed lumps so a creature immune to the acid still takes the bite.
            dmg, dtype, extra = "", None, []
            for d in a.get("damage") or []:
                if not d.get("damage_dice"):
                    continue
                dt = ((d.get("damage_type") or {}).get("name")
                      if isinstance(d.get("damage_type"), dict)
                      else d.get("damage_type"))
                if not dmg:
                    dmg, dtype = d["damage_dice"], dt
                else:
                    extra.append({"dice": d["damage_dice"], "type": dt})
            if not dmg:
                continue
            desc = (a.get("desc") or "").lower()
            # Stat blocks spell the bands out: "range 80/320 ft." (or "reach 10 ft.").
            rng = re.search(r"range\s+(\d+)\s*/\s*(\d+)", desc)
            reach = re.search(r"reach\s+(\d+)\s*(?:ft|feet)", desc)
            out.append({"name": a.get("name") or "attack",
                        "attack_bonus": int(a["attack_bonus"]),
                        "damage": dmg, "damage_type": dtype,
                        "damage_extra": extra,
                        # A monster's natural weapons and its spell-like
                        # attacks are magical for the purposes of a resistance
                        # to nonmagical damage only when the block says so;
                        # "magic weapon attacks" is how it says it.
                        "magical": "magic" in desc,
                        "ranged": desc.startswith("ranged")
                                  or "ranged weapon attack" in desc,
                        "range_normal": int(rng.group(1)) if rng else None,
                        "range_long": int(rng.group(2)) if rng else None,
                        "reach_ft": int(reach.group(1)) if reach else None})
        return out

    @staticmethod
    def _note_concentration(out: dict, ev: dict) -> None:
        """Say on the event what the damage did to the target's concentration.

        The tracker ROLLS the save — it owns the DC, and it is the one place
        all eight of the paths that deal damage here meet. This only reports.
        With no ``con_save_mod_for`` installed there is no roll and the check
        is still reported as pending, which is what always happened.
        """
        roll = out.get("concentration_roll")
        if roll is not None:
            spell = roll.get("spell") or "the spell"
            ev.setdefault("notes", []).append(
                f"concentration on {spell} "
                f"{'holds' if roll['success'] else 'BREAKS'} "
                f"(CON {roll['total']} vs DC {roll['dc']})")
        elif out.get("concentration_check"):
            ev["concentration_dc"] = out.get("concentration_dc")
        gone = out.get("dismissed") or []
        if gone:
            ev.setdefault("notes", []).append(
                f"{', '.join(gone)} {'vanish' if len(gone) > 1 else 'vanishes'} "
                f"with the spell")

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

    def _save_mod(self, c: Combatant, ability: str,
                  profiles: dict[int, PCProfile]) -> int:
        """A creature's SAVING THROW modifier — ability mod plus proficiency.

        The one place it is decided, because every save site used
        ``_ability_mod`` and so silently left proficiency out: a level-11
        sorcerer's Constitution save was rolled at +2 instead of +6, which is
        four points off on every concentration check, every Hold Person and
        every hazard. Proficiency comes from the class's two saves plus
        anything that granted another (Resilient writes a ``save:`` tag, which
        until now nothing read).

        A monster's is the value its stat block PRINTS, used as-is — that
        number already includes the creature's proficiency, so adding it again
        would double it. A stat block with no listed save for this ability
        falls back to the bare ability modifier, which is the rule.
        """
        key = _mod_key(ability)
        if c.character_id and c.character_id in profiles:
            p = profiles[c.character_id]
            mod = p.ability_mods.get(key, 0)
            if key in {str(a).strip().lower()[:3] for a in (p.save_profs or ())}:
                mod += p.prof
            return mod
        m = self._monster(c)
        if m is not None:
            listed = monster_save_mod(m, key)
            if listed is not None:
                return listed
        return self._ability_mod(c, ability, profiles)

    def _exh_pen(self, c: Combatant, profiles: dict[int, PCProfile]) -> int:
        """2024 Exhaustion penalty on a creature's D20 Test: -2 x level. Applied at
        every roll site (attacks, saves, checks) but NEVER to DCs, passive scores, or
        damage. Monster exhaustion isn't modeled numerically, so it returns 0."""
        if c.character_id and profiles and c.character_id in profiles:
            lvl = max(0, min(6, int(getattr(profiles[c.character_id], "exhaustion", 0) or 0)))
            return -2 * lvl
        return 0

    def _combat_roll_mod(self, c: Combatant, profiles: dict[int, PCProfile],
                         *, spell: bool = False) -> int:
        """Total flat modifier on a combatant's D20 Test: the per-character penalties
        (exhaustion + curse) PLUS the environmental site aura (which applies to everyone
        in the fight). ``spell`` adds the aura's spell-attack term. Never touches DCs,
        passive scores, or damage."""
        m = self._exh_pen(c, profiles)
        if c.character_id and profiles and c.character_id in profiles:
            m += int(getattr(profiles[c.character_id], "curse_pen", 0) or 0)
        env = self._env or {}
        m += int(env.get("d20", 0) or 0)
        if spell:
            m += int(env.get("spell_d20", 0) or 0)
        return m

    def _can_heal(self, c: Combatant, profiles: dict[int, PCProfile]) -> bool:
        """False if a curse blocks this PC from regaining HP (monsters always heal)."""
        if c.character_id and profiles and c.character_id in profiles:
            return not bool(getattr(profiles[c.character_id], "no_heal", False))
        return True

    def _creature_type(self, c: Combatant, profiles: dict[int, PCProfile]) -> str:
        """Lowercased creature type of a combatant (PC from profile, monster from its
        statblock, default 'humanoid')."""
        if c.character_id and profiles and c.character_id in profiles:
            return (getattr(profiles[c.character_id], "creature_type", None)
                    or "humanoid").lower()
        m = self._monster(c)
        if m and getattr(m, "type", None):
            return str(m.type).lower()
        return "humanoid"

    def _immune_to(self, c: Combatant, cond: str,
                   profiles: dict[int, PCProfile]) -> bool:
        """True if a combatant is immune to a condition (PC species immunities, or a
        monster's condition_immunities)."""
        cond = (cond or "").lower()
        if c.character_id and profiles and c.character_id in profiles:
            return cond in (getattr(profiles[c.character_id], "immunities", None) or set())
        m = self._monster(c)
        if m:
            return cond in {str(x).lower()
                            for x in (getattr(m, "condition_immunities", None) or [])}
        return False

    @staticmethod
    def _weapon_dict(cand: PCWeapon, *, offhand: bool = False) -> dict:
        """One weapon as the attack resolver wants it.

        ``offhand`` swaps in the bonus swing's damage, which is the same dice
        without the ability modifier unless the Two-Weapon Fighting style
        restored it — the backend has already decided that, because the style
        is a feat and the engine knows nothing about feats.
        """
        return {"name": cand.name, "attack_bonus": cand.attack_bonus,
                "damage": (cand.offhand_damage or cand.damage) if offhand
                          else cand.damage,
                "ranged": cand.ranged, "finesse": cand.finesse,
                "range_normal": cand.range_normal,
                "range_long": cand.range_long,
                "grip": cand.grip, "stowed": cand.stowed,
                "light": cand.light, "two_handed": cand.two_handed,
                "thrown": cand.thrown, "throw_normal": cand.throw_normal,
                "throw_long": cand.throw_long, "mastery": cand.mastery,
                "ability_mod": cand.ability_mod,
                "damage_type": cand.damage_type,
                # The dice WITHOUT the ability modifier — Cleave's second
                # target takes the weapon's damage and no modifier with it.
                "damage_flat": cand.damage_flat}

    def _bonus_swing_earned(self, c: Combatant, prof: PCProfile) -> bool:
        """Did the Attack action actually earn the two-weapon bonus attack?

        The Light property hangs the extra attack off attacking WITH A LIGHT
        WEAPON, not off merely holding one — a Dual Wielder who opened with the
        non-Light blade in their off hand has not met the condition. A monk's
        Martial Arts swing is a different feature with no such requirement, so
        a PC who has it is never held to this.
        """
        if "martial arts" in prof.features:
            return True
        held = [w for w in prof.weapons if w.grip in ("main", "off")]
        if not any(w.light for w in held):
            return True                # not a two-weapon build; some other
                                       # feature granted the bonus attack
        used_grip, used_name = _split_last_weapon(c.last_weapon)
        if not used_grip and not used_name:
            return True                # nothing recorded — don't invent a bar
        for w in held:
            if (used_grip and w.grip == used_grip) or \
                    (not used_grip and w.name.strip().lower() == used_name):
                return w.light
        return True

    def _attack_profile(self, c: Combatant, weapon: str,
                        profiles: dict[int, PCProfile],
                        *, offhand: bool = False) -> Optional[dict]:
        """Resolve what this creature swings: named weapon, else its best.

        A PC's pool arrives ordered by the loadout — main hand, then both
        hands, then off hand, then everything still in the pack — so "its best"
        is now "what is actually in its hand", which is the only reading that
        was ever right. A named weapon still wins, including a stowed one: it
        comes back flagged, and the caller refuses it and names the draw. That
        beats silently swinging something else, which is what happened before.
        """
        w = (weapon or "").strip().lower()
        if c.character_id and c.character_id in profiles:
            p = profiles[c.character_id]
            pool = p.weapons or []
            # The bonus swing is made with a DIFFERENT weapon in the other
            # hand, whatever the DM named — that is the whole rule, so it
            # overrides the name. "The other hand" is read off what was already
            # swung this turn rather than assumed to be the off hand: a
            # character who took the Attack action with their off-hand blade
            # makes the extra attack with the main-hand one.
            if offhand:
                used_grip, used_name = _split_last_weapon(c.last_weapon)
                held = [x for x in pool if x.grip in ("main", "off")]
                # By HAND first, because two identical shortswords are two
                # weapons with one name and the rule is about the other hand.
                other = next((x for x in held if used_grip
                              and x.grip != used_grip), None)
                if other is None:
                    other = next((x for x in held if x.name.strip().lower()
                                  != used_name), None)
                if other is not None:
                    return self._weapon_dict(other, offhand=True)
            for cand in pool:
                if w and w in cand.name.lower():
                    return self._weapon_dict(cand)
            if pool and w not in ("unarmed", "fist", "punch"):
                return self._weapon_dict(pool[0])
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
        """The weapon a reaction swings — an opportunity attack, a riposte.

        Nobody draws a blade to take an opportunity attack, so a stowed weapon
        is skipped outright rather than refused: a PC with both hands full of
        shield swings a fist, which is what actually happens.
        """
        if c.character_id and c.character_id in profiles:
            for cand in profiles[c.character_id].weapons:
                if not cand.ranged and not cand.stowed:
                    return self._weapon_dict(cand)
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

    def _underwater(self) -> bool:
        """Is a board out, and is the fight in the water? False without one."""
        try:
            return bool(self.spatial is not None
                        and getattr(self.spatial, "underwater", None)
                        and self.spatial.underwater())
        except Exception:
            return False

    def _squeezing(self, c: Combatant) -> bool:
        try:
            return bool(self.spatial is not None
                        and getattr(self.spatial, "squeezing", None)
                        and self.spatial.squeezing(c))
        except Exception:
            return False

    def _swims(self, c: Combatant) -> bool:
        try:
            return bool(self.spatial is not None
                        and getattr(self.spatial, "swims", None)
                        and self.spatial.swims(c))
        except Exception:
            return False

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
                          ranged: bool, encounter_id: int,
                          weapon: Optional[dict] = None) -> tuple[bool, bool, list[str]]:
        adv, dis, notes = False, False, []
        # Underwater, most of what you can swing is wrong for it. The board
        # knows the fight is in the water and whether this creature swims; the
        # weapon decides the rest. Before this the rule lived as a line of
        # prose in the arena catalogue asking the DM to remember it.
        if weapon is not None and self._underwater():
            name = str(weapon.get("name") or "")
            if not ranged and not self._swims(atk) \
                    and not _weapon_matches(name, _UNDERWATER_MELEE_OK):
                dis = True
                notes.append(f"underwater: {name or 'that weapon'} is swung, "
                             f"not thrust, and {atk.name} has no swimming "
                             f"speed — disadvantage")
            elif ranged and not _weapon_matches(name, _UNDERWATER_RANGED_OK):
                dis = True
                notes.append(f"underwater: {name or 'that weapon'} fights the "
                             f"water — disadvantage")
        ac_conds, tc_conds = self._conds(atk), self._conds(tgt)
        # Weapon Mastery riders. Each is ONE attack long AND time-limited, and
        # both bounds are enforced here — a rider that waits forever for its
        # victim to swing is a better rider than the book prints. Sap is on the
        # creature that was hit; Vex is on the one that hit, and names who it
        # may be spent against.
        rnd = int(getattr(self.tracker.get_encounter(encounter_id), "round", 1) or 1)
        sap = self._live_rider(atk, "sapped:", rnd)
        if sap:
            dis = True
            notes.append("Sap: disadvantage on this attack")
            self.tracker.remove_condition(atk.id, sap)
        vex = self._live_rider(atk, f"vexing:{tgt.name.lower()}:", rnd)
        if vex:
            adv = True
            notes.append(f"Vex: advantage against {tgt.name}")
            self.tracker.remove_condition(atk.id, vex)
        if ranged and self._engaged_enemies(encounter_id, atk):
            dis = True
            notes.append("ranged attack while in melee: disadvantage")
        if ac_conds & _ATTACKER_DISADV:
            dis = True
            notes.append(f"attacker {', '.join(sorted(ac_conds & _ATTACKER_DISADV))}: disadvantage")
        if "invisible" in ac_conds or "hidden" in ac_conds:
            adv = True
            notes.append("unseen attacker: advantage")
        # The BOARD knows something conditions don't: light. Attacking what you
        # can't make out is at disadvantage, and being unseen by your target is
        # advantage — the two are separate facts and a dark room usually grants
        # both at once. Silent when there is no board, or when the board can't
        # place these two, so theater-of-the-mind play is unchanged.
        if self.spatial is not None and hasattr(self.spatial, "can_see"):
            try:
                sees = self.spatial.can_see(atk, tgt)
                seen = self.spatial.can_see(tgt, atk)
            except Exception:
                sees = seen = None
            if sees is False:
                dis = True
                notes.append(f"{atk.name} can't see {tgt.name}: disadvantage")
            if seen is False:
                adv = True
                notes.append(f"{tgt.name} can't see {atk.name}: advantage")
        if "helped" in ac_conds:
            adv = True
            notes.append("helped: advantage")
        # Squeezing: forcing yourself through a gap costs you the fight as well
        # as the movement. Board state, because the board is what knows the
        # corridor is too narrow for you.
        if self._squeezing(atk):
            dis = True
            notes.append(f"{atk.name} is squeezing: disadvantage")
        if self._squeezing(tgt):
            adv = True
            notes.append(f"{tgt.name} is squeezing: advantage")
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
                        profiles: Optional[dict[int, PCProfile]] = None,
                        env: Optional[dict] = None) -> TurnReport:
        """Answer the frozen reaction and finish the interrupted attack.
        May pause again (a declined Shield can chain into an Uncanny ask)."""
        profiles = profiles or {}
        self._env = dict(env or {})
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
            out = self.tracker.apply_damage(
                target.id, rolled=[(self._packet(weapon), total)])
            total = out.get("damage_taken", total)
            self._note_resistance(out, ev.setdefault("notes", []))
            ev["damage"] = total
            ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
            if out.get("defeated"):
                ev["defeated"] = True
            self._note_concentration(out, ev)
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
        out = self.tracker.apply_damage(
            target.id, rolled=[(self._packet(payload.get("weapon") or {}), total)])
        total = out.get("damage_taken", total)
        self._note_resistance(out, ev.setdefault("notes", []))
        ev["damage"] = total
        ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
        if out.get("defeated"):
            ev["defeated"] = True
        self._note_concentration(out, ev)
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
        adv, dis, notes = self._attack_advantage(enemy, mover, False,
                                                 encounter_id, weapon=mprof)
        eff_ac = self._eff_ac(mover)
        oa_exh = self._combat_roll_mod(enemy, profiles)
        if oa_exh:
            notes.append(f"Roll mod {oa_exh}")
        atk = attack_roll(mprof["attack_bonus"] + oa_exh, eff_ac, advantage=adv,
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
            out = self.tracker.apply_damage(
                mover.id, rolled=self._attack_parts(mprof, total))
            total = out.get("damage_taken", total)
            self._note_resistance(out, notes)
            oa_rolls.append(self._roll_dict(
                f"{mprof['name']} damage", dmg.detail, total,
                expr=mprof["damage"]))
            oa["damage"] = total
            oa["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
            if out.get("defeated"):
                oa["defeated"] = True
            self._note_concentration(out, oa)
        rep.events.append(oa)

    # ---------------- intent resolution ----------------

    def resolve(self, encounter_id: int, intents: list[dict],
                profiles: Optional[dict[int, PCProfile]] = None,
                env: Optional[dict] = None) -> TurnReport:
        """Resolve intents for the CURRENT creature's turn. Illegal intents
        are rejected with reasons; nothing about them is applied. ``env`` is the
        location's arcane aura (roll modifiers), applied to every D20 Test this turn."""
        profiles = profiles or {}
        self._env = dict(env or {})
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
            mod = self._save_mod(fresh, sv.get("ability") or "con", profiles)
            mod += self._combat_roll_mod(fresh, profiles)
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
                off = next((w for w in prof.weapons if w.grip == "off"), None)
                opts.append(f"off-hand attack with the {off.name}" if off
                            else "bonus-action attack")
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
        # Whether this swing is the two-weapon bonus attack has to be decided
        # BEFORE the reach and range checks: the bonus swing is made with the
        # other hand, and a dagger's reach is not a shortbow's range.
        fresh0 = self.tracker.get_combatant(actor.id)
        pc_prof = profiles.get(actor.character_id) if actor.character_id else None
        base_allowed = (self._multiattack_count(actor) if actor.monster_slug
                        else (pc_prof.attacks_per_action if pc_prof else 1))
        # Nick moves the Light property's extra attack OUT of the Bonus Action
        # and into the Attack action. Expressed as one more attack in the
        # action's budget, which is exactly what it is — and which leaves the
        # bonus action free, the whole reason anyone takes it.
        nick = bool(pc_prof and "bonus attack" in pc_prof.features
                    and any(w.mastery == "nick" for w in pc_prof.weapons
                            if w.grip in ("main", "off"))
                    and self._bonus_swing_earned(fresh0, pc_prof))
        allowed = base_allowed + (1 if nick else 0)
        is_nick_swing = bool(nick and fresh0.action_used
                             and fresh0.attacks_made == base_allowed)
        is_bonus_swing = bool(
            fresh0.action_used and fresh0.attacks_made >= allowed
            and pc_prof and "bonus attack" in pc_prof.features
            and not fresh0.bonus_used
            and self._bonus_swing_earned(fresh0, pc_prof))
        prof = self._attack_profile(actor, intent.get("arg") or "", profiles,
                                    offhand=is_bonus_swing or is_nick_swing)
        if prof is None:
            rep.rejections.append({"intent": intent,
                                   "reason": f"{actor.name} has no attack to make."})
            return
        # A weapon in the pack is not a weapon in a hand. Drawing one is a free
        # object interaction, so this is a remedy rather than a dead end — and
        # it only ever fires for a PC who is holding something else, because a
        # PC holding nothing has no stowed weapons at all.
        if prof.get("stowed"):
            rep.rejections.append({
                "intent": intent,
                "reason": (f"{prof['name']} is not in {actor.name}'s hands — "
                           f"draw it first ([[GRIP: draw | {prof['name']}]], a "
                           "free object interaction) or swing what they are "
                           "holding.")})
            return
        # A thrown weapon reaches past its wielder's arm. Before this a dagger
        # could not be thrown AT ALL: it is a melee weapon, so an out-of-reach
        # target was simply refused, and the throw ranges the SRD ships with
        # (20/60 for a dagger, 30/120 for a javelin) had never been read out of
        # the downloaded rows. Out of reach and Thrown means it is a throw.
        thrown_now = False
        if (not prof.get("ranged") and prof.get("thrown")
                and prof.get("throw_normal")
                and self._steps_between(actor, target) > 0):
            prof = {**prof, "ranged": True,
                    "range_normal": prof.get("throw_normal"),
                    "range_long": prof.get("throw_long")}
            thrown_now = True

        # With a board out we know the exact gap, so a ranged weapon's bands
        # mean something: past normal range is disadvantage, past long range is
        # not a shot at all. Without a board (or without range data) this is
        # skipped entirely and the band model rules as before.
        long_shot = False
        drowned_shot = False
        gap = self._spatial_gap(actor, target)
        if gap is not None and prof.get("ranged"):
            dist = gap[0]
            r_long = prof.get("range_long")
            r_norm = prof.get("range_normal")
            if r_long and dist > int(r_long):
                rep.rejections.append({
                    "intent": intent,
                    "reason": (f"{target.name} is {dist} ft away — beyond "
                               f"{prof['name']}'s maximum range of {r_long} ft.")})
                return
            if r_norm and dist > int(r_norm):
                long_shot = True
                # Underwater this is not a harder shot, it is a missed one: the
                # water stops the missile. Rolled and spent rather than refused,
                # because that is what the rule says happens — the shot is
                # taken and it fails.
                drowned_shot = self._underwater()

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
        # ``allowed`` and the bonus-swing decision were both made at the top,
        # because the weapon had to be chosen before reach was checked.
        fresh = self.tracker.get_combatant(actor.id)
        bonus_note = None
        # Which weapon was swung is recorded on every attack: the two-weapon
        # bonus attack has to be made with a DIFFERENT weapon, and the Light
        # property hangs it off what the ACTION attacked with.
        mark = _mark_last_weapon(prof)
        if not fresh.action_used:
            self.tracker.update_economy(actor.id, action_used=True,
                                        attacks_made=1, last_weapon=mark)
        elif fresh.attacks_made < allowed:
            self.tracker.update_economy(actor.id, last_weapon=mark,
                                        attacks_made=fresh.attacks_made + 1)
            if is_nick_swing:
                bonus_note = ("off-hand attack (Nick — part of the Attack "
                              "action, the bonus action is still free)")
        elif (pc_prof and "bonus attack" in pc_prof.features
              and not fresh.bonus_used and is_bonus_swing):
            self.tracker.update_economy(actor.id, bonus_used=True,
                                        last_weapon=mark)
            bonus_note = ("off-hand attack (bonus action)"
                          if prof.get("grip") in ("main", "off")
                          else "bonus-action attack")
        else:
            left = []
            if not fresh.bonus_used:
                left.append("a bonus action")
            if fresh.move_left > 0:
                left.append("movement")
            why = ""
            if (pc_prof and "bonus attack" in pc_prof.features
                    and not fresh.bonus_used and not is_bonus_swing):
                # The hands qualify but the Attack action didn't: the Light
                # property buys the extra swing off attacking WITH a Light
                # weapon, not off merely holding one.
                why = (" — the extra attack from the Light property needs the "
                       "Attack action to have been made with a Light weapon")
            rep.rejections.append({
                "intent": intent,
                "reason": f"{actor.name} has no attacks left this turn" + why
                          + (f" — still available: {', '.join(left)}" if left and not why
                             else "" if why else " — declare the end of the turn")
                          + "."})
            return

        adv, dis, notes = self._attack_advantage(actor, target, prof["ranged"],
                                                 encounter_id, weapon=prof)
        if long_shot:
            dis = True
            notes.append(f"long range ({prof.get('range_normal')} ft): disadvantage")
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
        exh = self._combat_roll_mod(actor, profiles)
        if exh:
            atk_bonus += exh
            notes.append(f"Roll mod {exh}")
        eff_ac = self._eff_ac(target)
        atk = attack_roll(atk_bonus, eff_ac, advantage=adv,
                          disadvantage=dis, label=f"{prof['name']} ({actor.name})",
                          rng=self.rng)
        # May freeze the fight to ask the target's player (Shield).
        self._maybe_prompt_shield(actor, target, atk, eff_ac, profiles,
                                  prof, notes, after=None)
        hit = bool(atk.hit)
        if drowned_shot and hit:
            hit = False
            notes.append("underwater: past its normal range the shot is stopped "
                         "by the water — an automatic miss")
        rolls = [self._roll_dict(f"{prof['name']} — {actor.name}", atk.detail,
                                 atk.total, dc=eff_ac, success=hit)]
        ev = {"kind": "attack", "actor": actor.name, "target": target.name,
              "weapon": prof["name"], "hit": hit, "crit": atk.is_crit,
              "notes": notes, "rolls": rolls}
        if thrown_now:
            # It left the hand. The engine never touches the character DB, so
            # it says so and the backend takes it out of the grip.
            ev["thrown"] = prof["name"]
            ev["thrown_by"] = actor.character_id
            notes.append(f"the {prof['name']} leaves {actor.name}'s hand")
        if "hidden" in self._conds(actor):
            self.tracker.remove_condition(actor.id, "hidden")
        if "helped" in self._conds(actor):
            self.tracker.remove_condition(actor.id, "helped")
        if hit:
            dmg = damage_roll(prof["damage"], crit=atk.is_crit, rng=self.rng)
            total = dmg.total
            rolls.append(self._roll_dict(f"{prof['name']} damage", dmg.detail,
                                         dmg.total, expr=prof["damage"]))
            # Every rider below is its OWN typed lump: Sneak Attack is the
            # weapon's type, a Smite is Radiant, and a creature immune to one
            # is not immune to the other. Summing them into a single number and
            # resisting that once is the mistake this list prevents.
            parts: list = self._attack_parts(prof, dmg.total)

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
                parts.append((self._packet(prof, actor, label="Sneak Attack"),
                              sneak.total))   # a rogue's dice, the weapon's type
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
                    parts.append((dmgtypes.Packet(
                        dice=f"{ndice}d8", type="radiant", magical=True,
                        label="Divine Smite"), sm.total))
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
                parts.append((self._packet(prof, actor, label="Rage"), rb))
                notes.append(f"Rage +{rb}")

            shaved = self._maybe_prompt_uncanny(actor.name, target, total,
                                                profiles, ev_ctx=ev, after=None)
            parts = self._scale_parts(parts, total, shaved)
            total = shaved
            out = self.tracker.apply_damage(target.id, rolled=parts)
            total = out.get("damage_taken", total)
            self._note_resistance(out, notes)
            ev["damage"] = total
            ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
            if out.get("defeated"):
                ev["defeated"] = True
            self._note_concentration(out, ev)
            self._apply_mastery(encounter_id, actor, target, prof, pc_prof,
                                ev, notes, rolls, profiles, hit=True,
                                damage=total)
        else:
            self._apply_mastery(encounter_id, actor, target, prof, pc_prof,
                                ev, notes, rolls, profiles, hit=False)
        rep.events.append(ev)

    # ---------------- 2024 Weapon Mastery ----------------

    # ---------------- damage typing ----------------

    @staticmethod
    def _packet(prof: dict, actor: Optional[Combatant] = None,
                label: str = "") -> "dmgtypes.Packet":
        """The typed lump one weapon or attack deals.

        ``magical`` decides whether a "nonmagical" resistance applies, and half
        the bestiary carries one — so getting it wrong is the difference
        between a specter taking a full hit and half of it. A +N weapon is
        magical by name, which is the only signal the catalogue reliably gives.
        """
        name = str(prof.get("name") or "")
        magical = bool(prof.get("magical")) or bool(re.search(r"\+\d", name))
        materials = set()
        low = name.lower()
        for mat in ("silvered", "adamantine"):
            if mat in low:
                materials.add(mat)
        return dmgtypes.Packet(dice=str(prof.get("damage") or ""),
                               type=prof.get("damage_type"), magical=magical,
                               materials=materials, label=label or name)

    def _attack_parts(self, prof: dict, total: int) -> list:
        """A monster attack's typed lumps, main damage plus any rider.

        The rider is rolled at PARSE time in the profile, so here it is only
        given its type; the point is that a bite that is piercing plus acid
        meets an acid-immune target as two separate lumps.
        """
        extras = prof.get("damage_extra") or []
        if not extras:
            return [(self._packet(prof), total)]
        parts = [(self._packet(prof), total)]
        for ex in extras:
            r = damage_roll(str(ex.get("dice") or "0"), rng=self.rng)
            parts.append((dmgtypes.Packet(
                dice=str(ex.get("dice") or ""), type=ex.get("type"),
                magical=bool(prof.get("magical")),
                label=str(prof.get("name") or "")), r.total))
        return parts

    @staticmethod
    def _scale_parts(parts: list, before: int, after: int) -> list:
        """Re-spread a total that something outside the type system changed.

        Uncanny Dodge halves a whole blow, not one type of it, and it does so
        before resistances. Rather than lose the breakdown, each lump keeps its
        share of the new total — the last one absorbs the rounding so the parts
        still sum to exactly what was dealt.
        """
        if after == before or before <= 0 or not parts:
            return parts
        out, running = [], 0
        for i, (pk, n) in enumerate(parts):
            share = (after - running) if i == len(parts) - 1 \
                else int(n * after / before)
            out.append((pk, max(0, share)))
            running += share
        return out

    @staticmethod
    def _note_resistance(out: dict, notes: list) -> None:
        """Say what the target's defences did. A halving nobody is told about
        reads at the table as a bad damage roll."""
        for n in out.get("damage_notes") or []:
            notes.append(n)
        rolled = out.get("damage_rolled")
        took = out.get("damage_taken")
        if rolled is not None and took is not None and rolled != took:
            notes.append(f"{rolled} rolled → {took} taken")

    def _live_rider(self, c: Combatant, prefix: str, rnd: int) -> Optional[str]:
        """A time-limited mastery rider on this creature, if it hasn't lapsed.

        The expiry ROUND is stamped into the condition ("sapped:4") rather than
        kept anywhere else, because a condition is already a string the tracker
        persists and a second table for four riders would be the more complex
        answer. A lapsed one is cleared on the way past, so it never lingers on
        a sheet or in a UI.
        """
        for raw in (c.conditions or []):
            s = str(raw).lower()
            if not s.startswith(prefix.lower()):
                continue
            try:
                until = int(s.rsplit(":", 1)[1])
            except (ValueError, IndexError):
                until = rnd
            if rnd <= until:
                return str(raw)
            self.tracker.remove_condition(c.id, str(raw))
        return None

    def _creature_size(self, c: Combatant,
                       profiles: dict[int, PCProfile]) -> str:
        """A creature's size, lowercased. PCs are Medium unless a board says
        otherwise; a monster's stat block knows. Push is gated on it."""
        if self.spatial is not None:
            try:
                s = self.spatial.size(c)
                if s:
                    return str(s).lower()
            except Exception:
                pass
        m = self._monster(c)
        return str(getattr(m, "size", None) or "Medium").lower()

    def _apply_mastery(self, encounter_id: int, actor: Combatant,
                       target: Combatant, prof: dict,
                       pc_prof: Optional[PCProfile], ev: dict, notes: list,
                       rolls: list, profiles: dict[int, PCProfile],
                       *, hit: bool, damage: int = 0) -> None:
        """The weapon's mastery, if it has one and this character chose it.

        The backend resolves WHICH mastery is in play (the assignment is book
        data — see ``rules/mastery.py``); everything here is the mechanism, so
        an SRD-only checkout never has one to apply. Nick is absent on purpose:
        it changes the action economy, which happened long before the roll.

        Every rider here is bounded in time, and the bound is enforced by
        stamping the round it expires into the condition — "before the start of
        your next turn" is a real limit, and a Sap that lasts until its victim
        happens to attack is a different, much better rider than the one the
        book prints.
        """
        m = (prof.get("mastery") or "").lower()
        if not m or m == "nick":
            return
        # "The ability modifier you used to make the attack roll" — decided by
        # the backend when it built this weapon, not guessed at from the sheet.
        amod = int(prof.get("ability_mod") or 0)
        enc = self.tracker.get_encounter(encounter_id)
        rnd = int(getattr(enc, "round", 1) or 1)

        if not hit:
            # Graze — the only mastery that does anything on a MISS. The damage
            # is the ability modifier and nothing else: no crit, no Rage, no
            # Sneak Attack, because it "can be increased only by increasing the
            # ability modifier".
            if m == "graze" and amod > 0:
                out = self.tracker.apply_damage(
                    target.id,
                    rolled=[(self._packet(prof, actor, label="Graze"), amod)])
                amod = out.get("damage_taken", amod)
                ev["damage"] = amod
                ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
                if out.get("defeated"):
                    ev["defeated"] = True
                dtype = prof.get("damage_type") or ""
                notes.append(f"Graze — the miss still scores {amod}"
                             + (f" {dtype}" if dtype else ""))
                self._note_concentration(out, ev)
            return

        if m == "topple":
            dc = 8 + (pc_prof.prof if pc_prof else 2) + amod
            t_mod = self._save_mod(target, "con", profiles)
            t_mod += self._combat_roll_mod(target, profiles)
            sv = saving_throw(t_mod, dc=dc,
                              label=f"{target.name} Con save (Topple)",
                              rng=self.rng)
            rolls.append(self._roll_dict(f"Topple — {target.name}", sv.detail,
                                         sv.total, dc=dc, success=bool(sv.success)))
            if not sv.success and not self._immune_to(target, "prone", profiles):
                self.tracker.add_condition(target.id, "prone")
                notes.append(f"Topple — {target.name} is knocked prone")
            else:
                notes.append(f"Topple — {target.name} keeps their feet")

        elif m == "push":
            # "up to 10 feet straight away from yourself if it is Large or
            # smaller" — a size gate, and forced movement rather than a walk.
            size = self._creature_size(target, profiles)
            if size in ("huge", "gargantuan"):
                notes.append(f"Push — {target.name} is {size.title()}: too big "
                             "to shift")
            else:
                moved = self._push(actor, target, 10)
                notes.append(f"Push — {target.name} is driven "
                             f"{moved if moved is not None else 10} ft back")
                ev["push"] = target.name

        elif m == "sap":
            # "Disadvantage on its NEXT attack roll before the start of your
            # next turn" — one attack, and it lapses either way.
            self.tracker.add_condition(target.id, f"sapped:{rnd + 1}")
            notes.append(f"Sap — {target.name} has disadvantage on its next "
                         "attack roll")

        elif m == "slow":
            # "If you hit a creature ... and DEAL DAMAGE to it." The reduction
            # never exceeds 10 ft however many times it lands.
            if damage > 0:
                if self._slow(target, 10, until_round=rnd + 1):
                    notes.append(f"Slow — {target.name}'s Speed drops by 10 ft")
                else:
                    notes.append(f"Slow — {target.name} is already slowed "
                                 "(it doesn't stack)")

        elif m == "vex":
            # Also gated on damage, and it lasts until the END of your next
            # turn — one round longer than Sap.
            if damage > 0:
                self.tracker.add_condition(
                    actor.id, f"vexing:{target.name.lower()}:{rnd + 1}")
                notes.append("Vex — advantage on the next attack against "
                             f"{target.name}")

        elif m == "cleave":
            self._cleave(encounter_id, actor, target, prof, profiles, ev,
                         notes, rolls)

    def _push(self, actor: Combatant, target: Combatant,
              distance_ft: int) -> Optional[int]:
        """Shove a creature straight back. Returns the feet it actually moved.

        The board already owns forced movement — it ignores the target's speed,
        provokes no opportunity attack, and stops at the first obstacle — so
        Push is that primitive with a distance, not a second implementation.
        Without a board there is nothing finer than a band, and 10 ft is less
        than a band, so it is narrated instead.
        """
        if self.spatial is None:
            return None
        try:
            return self.spatial.push(target, actor, distance_ft)
        except Exception as e:
            print(f"[mastery] push: {e}")
            return None

    def _slow(self, target: Combatant, amount_ft: int,
              *, until_round: int) -> bool:
        """Reduce a creature's Speed. True if this application changed anything.

        The condition is the record even when a board is out, because the
        gridless table has no feet to take away and still has to know the
        creature is slowed. The board's own speed is reduced on top, which is
        where the 10 ft is actually felt.
        """
        if any(str(c).lower().startswith("slowed:")
               for c in (target.conditions or [])):
            return False                     # "doesn't exceed 10 feet"
        self.tracker.add_condition(target.id, f"slowed:{until_round}")
        if self.spatial is not None:
            try:
                self.spatial.slow(target, amount_ft)
            except Exception as e:
                print(f"[mastery] slow: {e}")
        return True

    def _cleave(self, encounter_id: int, actor: Combatant,
                first: Combatant, prof: dict, profiles: dict[int, PCProfile],
                ev: dict, notes: list, rolls: list) -> None:
        """A second melee attack roll against a creature beside the first.

        Once per turn, melee only, and the second creature "takes the weapon's
        damage" with NO ability modifier — so this is a real attack roll and a
        real damage roll, not a note asking the DM to make one. Sneak Attack,
        Smite and Rage are deliberately not re-applied: they are riders on the
        attack you took, and this is the mastery's own swing.
        """
        if prof.get("ranged"):
            return
        fresh = self.tracker.get_combatant(actor.id)
        if any(str(f).lower() == "cleave" for f in (fresh.used_features or [])):
            return
        # "within 5 feet of the first that is also within your reach". With a
        # board that is two real measurements; without one, the bands cannot
        # express 5 ft at all, and two creatures both in melee with the same
        # attacker are as adjacent as a gridless table can say.
        def beside(c: Combatant) -> bool:
            if not self._engaged_with(actor, c):
                return False
            gap = self._spatial_gap(first, c)
            return gap[0] <= 5 if gap is not None else True

        second = next(
            (c for c in self.tracker.order(encounter_id)
             if not c.defeated and c.id not in (first.id, actor.id)
             and self._side(c) != self._side(actor) and beside(c)),
            None)
        if second is None:
            notes.append("Cleave — nobody else stands within 5 ft of "
                         f"{first.name}")
            return
        self.tracker.update_economy(
            actor.id, used_features=[*(fresh.used_features or []), "cleave"])
        adv, dis, _ = self._attack_advantage(actor, second, False, encounter_id,
                                             weapon=prof)
        atk = attack_roll(prof["attack_bonus"], self._eff_ac(second),
                          advantage=adv, disadvantage=dis,
                          label=f"Cleave ({actor.name})", rng=self.rng)
        rolls.append(self._roll_dict(f"Cleave — {second.name}", atk.detail,
                                     atk.total, dc=self._eff_ac(second),
                                     success=bool(atk.hit)))
        if not atk.hit:
            notes.append(f"Cleave — the follow-through misses {second.name}")
            return
        expr = prof.get("damage_flat") or prof["damage"]
        dmg = damage_roll(expr, crit=atk.is_crit, rng=self.rng)
        out = self.tracker.apply_damage(
            second.id,
            rolled=[(self._packet(prof, actor, label="Cleave"), dmg.total)])
        self._note_resistance(out, notes)
        rolls.append(self._roll_dict("Cleave damage", dmg.detail, dmg.total,
                                     expr=expr))
        notes.append(f"Cleave — {second.name} is also struck for {dmg.total}")
        ev["cleave"] = {"target": second.name, "damage": dmg.total,
                        "target_hp": f"{out['current_hp']}/{out['max_hp']}"}
        if out.get("defeated"):
            ev["cleave"]["defeated"] = True
        self._note_concentration(out, ev)

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

    def _do_reposition(self, encounter_id, actor, intent, profiles, rep):
        """Move without changing how far away you are.

        The engine thinks in BANDS, so every move it knew how to make was a
        change of band — and a creature already at the range it wants could not
        move at all. That is most of a fight: an archer holding its distance
        still wants the ledge, the cover, the square that is not in the open.
        Measured before this existed, monsters moved on 6 turns out of 30 and
        the rest of the time stood exactly where they spawned.

        It costs a step of movement and provokes nothing: staying inside the
        same band means staying inside the reach you were already in, and 5e
        does not punish moving WITHIN reach. What square it actually lands on
        is the board's business — ``vtt.bridge.apply_band_move`` re-picks the
        best one for the band, which is where cover and high ground are chosen.
        """
        fresh = self.tracker.get_combatant(actor.id) or actor
        if fresh.move_left <= 0:
            rep.rejections.append({
                "intent": intent,
                "reason": f"{actor.name} has no movement left to reposition."})
            return
        band = fresh.position or "near"
        self.tracker.update_economy(actor.id, move_left=fresh.move_left - 1)
        rep.events.append({"kind": "move", "actor": actor.name, "to": band,
                           "reposition": True, "steps": 1, "rolls": [],
                           "notes": ["takes better ground at the same range"]})

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

    def _do_search(self, encounter_id, actor, intent, profiles, rep):
        """Take the Search action, and let the BOARD do the finding.

        The board has resolved hiding as a contest since the hide rules went in
        — a Stealth roll it remembers, a Perception check against that number,
        and a `found_by` list so the guard who spotted you sees you while the
        rest of the room does not. Nothing ever took the Search action except a
        player typing it. A creature that has heard something and cannot see
        anyone now does, which is what makes an ambush a thing you can lose.
        """
        spent = self._spend_action_or_cunning(actor, rep, intent, profiles, "Search")
        if spent is None:
            return
        ev = {"kind": "search", "actor": actor.name, "rolls": [], "notes": []}
        finder = getattr(self.spatial, "search", None) if self.spatial else None
        if finder is None:
            ev["notes"].append("searches, but there is no board to search")
            rep.events.append(ev)
            return
        got = finder(actor) or {}
        found = [f.get("name") for f in (got.get("found") or [])]
        ev["notes"].append(
            f"searches and finds {', '.join(found)}" if found
            else "searches and finds nothing")
        rep.events.append(ev)

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
            if not self._can_heal(actor, profiles):
                ev["notes"].append("a curse blocks healing — no HP regained")
            else:
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
        mod += self._combat_roll_mod(actor, profiles)
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
        a_mod += self._combat_roll_mod(actor, profiles)
        t_mod = max(self._ability_mod(target, "str", profiles),
                    self._ability_mod(target, "dex", profiles))
        t_mod += self._combat_roll_mod(target, profiles)
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
        if healed and not self._can_heal(actor, profiles):
            ev["notes"].append(f"{item} does nothing — a curse blocks healing")
        elif healed:
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
            out = self.tracker.apply_damage(target.id, rolled=[(
                dmgtypes.Packet(dice=expr, type="force", magical=True,
                                label=sp.name), dmg.total)])
            self._note_resistance(out, ev["notes"])
            ev["rolls"].append(self._roll_dict(
                f"{sp.name} ({darts} darts)", dmg.detail, dmg.total, expr=expr))
            ev["damage"] = out.get("damage_taken", dmg.total)
            ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
            ev["notes"].append("auto-hit")
            if out.get("defeated"):
                ev["defeated"] = True
            self._note_concentration(out, ev)
        elif eff and eff.get("heal"):
            tgt = target or actor
            n = 1 + max(0, (slot_spent or base_lv) - base_lv)
            mod = (prof.ability_mods.get(prof.spell_mod, 0)
                   if prof and prof.spell_mod
                   else self._ability_mod(actor, "wis", profiles))
            if not self._can_heal(tgt, profiles):
                ev["notes"].append(f"{tgt.name} regains nothing — a curse blocks healing")
            else:
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
            exh = self._combat_roll_mod(actor, profiles, spell=True)
            if exh:
                bonus += exh
                notes.append(f"Roll mod {exh}")
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
                out = self.tracker.apply_damage(target.id, rolled=[(
                    dmgtypes.Packet(dice=dmg_expr, type=self._spell_type(sp),
                                    magical=True, label=sp.name), dmg.total)])
                self._note_resistance(out, ev["notes"])
                ev["rolls"].append(self._roll_dict(
                    f"{sp.name} damage", dmg.detail, dmg.total, expr=dmg_expr))
                ev["damage"] = out.get("damage_taken", dmg.total)
                ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
                if out.get("defeated"):
                    ev["defeated"] = True
                self._note_concentration(out, ev)
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
            dc += int((self._env or {}).get("spell_dc", 0) or 0)  # site aura on the DC
            # One damage roll shared by every creature in the effect (RAW).
            shared = damage_roll(dmg_expr, rng=self.rng) if dmg_expr else None
            if shared is not None:
                ev["rolls"].append(self._roll_dict(
                    f"{sp.name} damage", shared.detail, shared.total,
                    expr=dmg_expr))
            results: list[dict] = []
            spell_l = (sp.name or "").lower()
            for tgt in targets:
                # Humanoid-only spells (Hold/Charm Person) can't touch a non-Humanoid.
                if spell_l in _HUMANOID_ONLY_SPELLS \
                        and self._creature_type(tgt, profiles) != "humanoid":
                    results.append({"target": tgt.name,
                                    "unaffected": "not a Humanoid"})
                    ev["notes"].append(f"{tgt.name} is not a Humanoid — unaffected")
                    continue
                t_mod = self._save_mod(tgt, sp.dc_type, profiles)
                t_mod += self._combat_roll_mod(tgt, profiles)
                save = saving_throw(t_mod, dc=dc,
                                    label=f"{sp.dc_type.upper()} save ({tgt.name})",
                                    rng=self.rng)
                ev["rolls"].append(self._roll_dict(
                    f"{sp.dc_type.upper()} save — {tgt.name}", save.detail,
                    save.total, dc=dc, success=bool(save.success)))
                res: dict = {"target": tgt.name, "saved": bool(save.success)}
                if not save.success and eff and eff.get("save_condition"):
                    cond = eff["save_condition"]
                    if self._immune_to(tgt, cond, profiles):
                        res["immune"] = cond
                        ev["notes"].append(f"{tgt.name} is immune to {cond}")
                    else:
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
                    # "half as much damage on a successful one" is a phrase in
                    # the description for most of these rows — `dc_success` is
                    # populated on a handful — so the prose is the fallback,
                    # and without it every Fireball a target saved against
                    # dealt nothing instead of half.
                    halves = ((sp.dc_success or "").lower() == "half"
                              or dmgtypes.save_halves(getattr(sp, "desc", None)))
                    if save.success and halves:
                        total //= 2
                    elif save.success:
                        total = 0
                    if total > 0:
                        out = self.tracker.apply_damage(tgt.id, rolled=[(
                            dmgtypes.Packet(dice=dmg_expr or "",
                                            type=self._spell_type(sp),
                                            magical=True, label=sp.name),
                            total)])
                        total = out.get("damage_taken", total)
                        self._note_resistance(out, ev["notes"])
                        res["damage"] = total
                        res["hp"] = f"{out['current_hp']}/{out['max_hp']}"
                        if out.get("defeated"):
                            res["defeated"] = True
                        self._note_concentration(out, ev)
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

    def _spell_type(self, sp: Optional[Spell]) -> Optional[str]:
        """A spell's damage type, from its structured row or its own prose.

        Only 17 of 430 spell rows in this project carry a structured `damage`
        dict — the rest came from a PDF parse that kept the description and
        nothing else — so the sentence is where a Fireball's "Fire" is
        actually written down. Derived at read time, like a component's price.
        """
        if sp is None:
            return None
        if isinstance(sp.damage, dict):
            t = dmgtypes.normalize_type(
                (sp.damage.get("damage_type") or {}).get("name")
                if isinstance(sp.damage.get("damage_type"), dict)
                else sp.damage.get("damage_type"))
            if t:
                return t
        packets = dmgtypes.parse_damage(getattr(sp, "desc", None))
        return packets[0].type if packets else None

    def _spell_damage(self, sp: Optional[Spell], prof: Optional[PCProfile],
                      slot: Optional[int] = None) -> Optional[str]:
        if sp is None:
            return None
        if not isinstance(sp.damage, dict):
            # No structured row: read the dice out of the description, the
            # same place the type comes from. Without this the engine rolled
            # to hit with a Fire Bolt and then dealt nothing at all.
            packets = dmgtypes.parse_damage(getattr(sp, "desc", None))
            return packets[0].dice if packets else None
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
            mod += self._combat_roll_mod(actor, profiles)
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

    def _plan_turn(self, encounter_id: int, cur: Combatant,
                   foes: list[Combatant]) -> list[dict]:
        """What a creature with no player behind it does with its whole turn.

        Written as a plan rather than a single act, because a turn is an
        ECONOMY and the old AI spent about a third of one: it swung if a target
        was already in reach and otherwise stood still. Everything here is a
        thing a player does without thinking —

        * hit what is nearly dead rather than spreading damage around;
        * shooters take better ground every turn instead of standing where they
          spawned, which is what ``reposition`` is for;
        * close the distance when closing is what is needed, Dashing when it
          takes a Dash;
        * break contact when badly hurt and able to fight at range, which
          spends the Disengage rather than eating an opportunity attack;
        * and never end a turn having done nothing — a creature with no reach
          and no shot DODGES, which is a real use of the action and the
          difference between a monster and a statue.

        Deliberately not a search over every option: this is a stat block's
        instinct, not a chess engine, and a fight the players cannot predict at
        all is not more fun than one they can read.
        """
        # A creature that has only HEARD something does not know where you are.
        # It searches — which is the action the board already resolves against
        # each hider's own Stealth roll — rather than swinging at a square it
        # has no reason to think you are standing in. Only when it can see
        # nobody: once a target is in the open, being suspicious is moot.
        if (cur.awareness or Awareness.ALERT) == Awareness.SUSPICIOUS:
            seen = None
            if self.spatial is not None and hasattr(self.spatial, "can_see"):
                seen = any(self.spatial.can_see(cur, f) for f in foes)
            if seen is False:
                return [{"verb": "search", "actor": cur.name}]

        atks = self._monster_attacks(cur)
        has_ranged = any(a["ranged"] for a in atks)
        has_melee = any(not a["ranged"] for a in atks)
        in_reach = [f for f in foes if self._steps_between(cur, f) == 0]
        hurt = cur.current_hp * 4 <= max(1, cur.max_hp)

        # Finish what is nearly down; among equals, the nearest.
        pool = in_reach or foes
        tgt = min(pool, key=lambda p: (p.current_hp, self._steps_between(cur, p)))
        steps = self._steps_between(cur, tgt)
        me = cur.name

        if in_reach and hurt and has_ranged:
            # Wounded and able to shoot: get out rather than trade in reach.
            return [{"verb": "disengage", "actor": me},
                    {"verb": "move", "actor": me, "arg": "near"},
                    {"verb": "attack", "actor": me, "target": tgt.name,
                     "arg": "ranged"}]
        if in_reach and has_melee:
            return [{"verb": "attack", "actor": me, "target": tgt.name}]
        if has_ranged:
            # Shoot, and use the move to stand somewhere better while doing it.
            return [{"verb": "reposition", "actor": me},
                    {"verb": "attack", "actor": me, "target": tgt.name,
                     "arg": "ranged"}]
        if steps <= 1:
            return [{"verb": "move", "actor": me,
                     "arg": f"melee with {tgt.name}"},
                    {"verb": "attack", "actor": me, "target": tgt.name}]
        return [{"verb": "dash", "actor": me},
                {"verb": "move", "actor": me, "arg": "near"},
                {"verb": "dodge", "actor": me}]

    def run_monster_turn(self, encounter_id: int,
                         intents: Optional[list[dict]] = None,
                         profiles: Optional[dict[int, PCProfile]] = None,
                         env: Optional[dict] = None) -> TurnReport:
        """Resolve the current (non-PC) creature's whole turn: proposed intents
        first; if none land, a default AI acts. Always advances the turn."""
        profiles = profiles or {}
        self._env = dict(env or {})
        cur = self.tracker.current_combatant(encounter_id)
        rep = TurnReport()
        if cur is None or cur.kind == "pc":
            rep.rejections.append({"reason": "Not a monster's turn."})
            return rep
        enc = self.tracker.get_encounter(encounter_id)
        rnd = int(getattr(enc, "round", 1) or 1)
        if (cur.awareness or Awareness.ALERT) == Awareness.UNAWARE and rnd <= 1:
            # SURPRISED. 5e is precise about this and it is worth being precise
            # too: no move, no action, and no reaction until the turn ends. It
            # applies to the first round only, so the creature comes out of it
            # SUSPICIOUS rather than unaware — steel has been drawn and someone
            # is shouting, even if it still cannot see who. Without that it
            # would be surprised for the whole fight, which is a statue.
            self.tracker.update_economy(cur.id, action_used=True, bonus_used=True,
                                        reaction_used=True, move_left=0)
            self.tracker.set_awareness(cur.id, Awareness.SUSPICIOUS)
            rep.events.append({"kind": "skip", "actor": cur.name, "rolls": [],
                               "notes": ["SURPRISED — caught unaware, and loses "
                                         "this turn entirely"]})
            self.tracker.next_turn(encounter_id)
            rep.turn_over = True
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
            rep = self.resolve(encounter_id, intents, profiles, env=env)
            if rep.paused:
                return rep
        if not rep.events and not rep.turn_over:
            # Default AI: hit whoever is in reach, else close and swing.
            #
            # ENEMIES, not player characters. This read `kind == "pc"`, which
            # is the same assumption `Combatant.side` was added to kill: a
            # conjured spirit fights FOR the party, a charmed guard fights
            # against its own, and two monster sides fighting each other found
            # no targets at all and stood on opposite edges of the board for
            # twenty turns doing nothing (measured — `scripts/ai_arena.py`).
            mine = self._side(cur)
            foes = [c for c in self.tracker.order(encounter_id)
                    if not c.defeated and self._side(c) != mine]
            if foes:
                seq = self._plan_turn(encounter_id, cur, foes)
                rep = self.resolve(encounter_id, seq, profiles, env=env)
        if rep.paused:
            return rep
        if not rep.turn_over:
            cur2 = self.tracker.current_combatant(encounter_id)
            if cur2 is not None:
                self._end_of_turn_saves(encounter_id, cur2, profiles, rep)
            self.tracker.next_turn(encounter_id)
            rep.turn_over = True
        return rep

    def apply_environment_hazards(self, encounter_id: int, env: Optional[dict] = None,
                                  profiles: Optional[dict[int, PCProfile]] = None) -> list[dict]:
        """Environmental hazard tick (a gas cloud, spore field): every living combatant
        makes the hazard's save or takes its damage. Call once per round. Returns event
        dicts for the backend to render — this does not advance turns.

        A hazard may carry ``"targets": [combatant_id, ...]`` to hit only those
        creatures — that's how a tactical board's damaging areas (a wall of
        fire, a spike growth) bite only whoever is actually standing in them,
        while a location-wide gas cloud still catches the whole room."""
        hazards = (env or {}).get("hazards") or []
        if not hazards:
            return []
        profiles = profiles or {}
        events: list[dict] = []
        for c in self.tracker.order(encounter_id):
            if c.defeated:
                continue
            for hz in hazards:
                targets = hz.get("targets")
                if targets is not None and c.id not in set(targets):
                    continue
                ability = (hz.get("ability") or "con")[:3]
                mod = self._save_mod(c, ability, profiles) + self._exh_pen(c, profiles)
                dc = int(hz.get("dc", 12) or 12)
                hname = hz.get("name", "a hazard")
                save = saving_throw(mod, dc=dc, label=f"{hname} save ({c.name})",
                                    rng=self.rng)
                ev = {"kind": "environment", "actor": c.name, "hazard": hname,
                      "success": bool(save.success), "notes": [],
                      "rolls": [self._roll_dict(f"{hname} — {c.name}", save.detail,
                                                save.total, dc=dc, success=bool(save.success))]}
                if not save.success:
                    expr = str(hz.get("damage", "1d6"))
                    dmg = damage_roll(expr, rng=self.rng)
                    # An arcane site's hazard says what kind of harm it is
                    # ("necrotic", "fire"); a creature immune to that walks
                    # through it, which is most of the point of being a fire
                    # elemental in a burning place.
                    out = self.tracker.apply_damage(c.id, rolled=[(
                        dmgtypes.Packet(dice=expr, type=hz.get("damage_type"),
                                        magical=True, label=hname), dmg.total)])
                    self._note_resistance(out, ev["notes"])
                    ev["damage"] = out.get("damage_taken", dmg.total)
                    ev["rolls"].append(self._roll_dict(f"{hname} damage", dmg.detail,
                                                       dmg.total, expr=expr))
                    ev["target_hp"] = f"{out['current_hp']}/{out['max_hp']}"
                    if out.get("defeated"):
                        ev["defeated"] = True
                    ev["notes"].append(f"takes {dmg.total}")
                    self._note_concentration(out, ev)
                else:
                    ev["notes"].append("resists")
                events.append(ev)
        return events

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
