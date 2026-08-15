"""Building one: what a character may raise, what it costs, and what it is.

The bastion layer could already RUN a stronghold — facilities, orders, turns,
travel — and the only way to get one was a REST call with a name in it. There
was no moment where a player decides what their place IS.

That decision has two halves and they are governed completely differently, which
is the whole design of this module:

**The constraints are the game's.** A bastion needs a level to own, a facility
needs a level to unlock, everything costs gold, and a bastion that means to move
needs something aboard that can move it. Those are checked here, in one place,
so the builder screen and the DM's own hook cannot disagree about what is legal
— the same reason the arena prices its cart on the server and never in the
client.

**The expression is the player's, and nothing here validates it.** The name, the
look, the motif, what the great hall smells like: free text, carried through to
the world entity and to the picture, and never refused. A builder that argues
with somebody's description of their own home is a builder nobody uses twice.

Nothing in here writes to the database. :func:`plan` says what is possible and
:func:`price` says what a choice would cost; the caller commits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .catalog import facilities_for_level, get_facility, propulsion_facilities
from .turn import can_own_bastion, facility_cost_gp, min_bastion_level

#: What a bastion can be built INTO. Not a rules distinction — the model has
#: carried `vehicle_kind` and `airship_id` since mobile bastions went in — but
#: it is the first question a builder has to ask, because it changes which
#: facilities are worth anything and whether a vessel has to be chosen.
KINDS: tuple[tuple[str, str, str], ...] = (
    ("keep", "A fixed place",
     "Walls and foundations. It stays where you raise it, and everything you "
     "build into it is yours for good."),
    ("mobile", "A travelling place",
     "Built into a vehicle — a wagon train, a barge, a walking hall. It goes "
     "where you go, provided something aboard can move it."),
    ("airship", "A flying place",
     "A vessel with a hall inside it. It flies, it fights, and it is a real "
     "ship in the world with a hull, crew stations and an elemental core."),
)

#: The gold a character is assumed to be able to raise if the sheet carries no
#: purse at all. Not a gift — the builder shows it as the ceiling and the caller
#: still checks the real purse when it commits.
DEFAULT_BUDGET_GP = 0.0


@dataclass
class Choice:
    """What a player asked for. Every expressive field is free text."""

    kind: str = "keep"
    name: str = ""
    #: The player's own words. Never validated, never refused, carried through
    #: to the world entity and into the picture.
    description: str = ""
    motif: str = ""
    facilities: tuple[str, ...] = ()
    #: For a flying bastion: which vessel out of `airships/catalog.py`.
    vessel_slug: str = ""
    #: For a travelling one: what it IS, in the player's words ("a barge", "a
    #: walking hall on six legs"). Fiction, not a rules term.
    vehicle_kind: str = ""


@dataclass
class Verdict:
    """Whether a choice may be built, what it costs, and why not."""

    ok: bool
    cost_gp: float = 0.0
    reasons: list[str] = field(default_factory=list)
    #: Advice that does not block: a facility that would be wasted, a vessel
    #: with no room for what was asked for.
    notes: list[str] = field(default_factory=list)


def kinds_for(level: int) -> list[dict]:
    """The kinds this character may build, each with why it is or is not open."""
    out: list[dict] = []
    movers = {f["slug"] for f in propulsion_facilities()}
    have_movers = bool(movers & {f["slug"] for f in facilities_for_level(level)})
    for slug, name, blurb in KINDS:
        why = ""
        if not can_own_bastion(level):
            why = f"a bastion needs level {min_bastion_level()}"
        elif slug in ("mobile", "airship") and not have_movers:
            why = "nothing you can build yet could move it"
        out.append({"slug": slug, "name": name, "blurb": blurb,
                    "available": not why, "why": why})
    return out


def plan(level: int, *, purse_gp: float = DEFAULT_BUDGET_GP,
         vessels: Optional[dict] = None) -> dict:
    """Everything a builder screen needs to offer, for THIS character.

    Facilities are the catalogue's own, filtered to what the level unlocks and
    priced through the live config — so a table running the gritty preset sees
    gritty prices, and nobody has to know that here.
    """
    per = facility_cost_gp("")
    movers = {f["slug"] for f in propulsion_facilities()}
    facs = [{
        "slug": f["slug"], "name": f["name"], "space": f.get("space", ""),
        "min_level": int(f.get("min_level") or 5),
        "desc": f.get("desc", ""),
        "orders": list(f.get("orders") or []),
        "income_gp": float(f.get("income_gp") or 0),
        "propulsion": f["slug"] in movers,
        "cost_gp": per,
    } for f in facilities_for_level(level)]
    fleet = [{"slug": k, "name": v.get("name", k),
              "crew": v.get("crew"), "passengers": v.get("passengers"),
              "cargo_tons": v.get("cargo_tons"),
              "cost_gp": float(v.get("cost_gp") or 0)}
             for k, v in sorted((vessels or {}).items())]
    return {
        "level": int(level),
        "can_own": can_own_bastion(level),
        "min_level": min_bastion_level(),
        "purse_gp": float(purse_gp),
        "cost_per_facility_gp": per,
        "kinds": kinds_for(level),
        "facilities": facs,
        "vessels": fleet,
    }


def price(choice: Choice, *, vessels: Optional[dict] = None) -> float:
    """What this choice costs in gold. Facilities plus the hull, if any."""
    total = facility_cost_gp("") * len(set(choice.facilities))
    if choice.kind == "airship" and choice.vessel_slug:
        v = (vessels or {}).get(choice.vessel_slug) or {}
        total += float(v.get("cost_gp") or 0)
    return round(total, 2)


def check(choice: Choice, level: int, *, purse_gp: float = 0.0,
          vessels: Optional[dict] = None) -> Verdict:
    """May this be built? The ONE place that decides.

    Errs toward refusing loudly and early rather than half-building something:
    a stronghold is expensive, and a player who is told afterwards that their
    gold went on a facility their level cannot use has been robbed by a bug.
    """
    reasons: list[str] = []
    notes: list[str] = []

    if not can_own_bastion(level):
        reasons.append(
            f"A bastion needs level {min_bastion_level()}; you are {level}.")
    if not (choice.name or "").strip():
        reasons.append("It needs a name.")
    if choice.kind not in {k for k, _n, _b in KINDS}:
        reasons.append(f"'{choice.kind}' is not something a bastion can be.")

    picked = list(dict.fromkeys(choice.facilities))
    if len(picked) != len(choice.facilities):
        notes.append("The same facility was chosen twice; it counts once.")
    for slug in picked:
        cat = get_facility(slug)
        if cat is None:
            reasons.append(f"There is no facility called '{slug}'.")
            continue
        need = int(cat.get("min_level") or 5)
        if need > level:
            reasons.append(
                f"{cat['name']} needs level {need}; you are {level}.")

    movers = {f["slug"] for f in propulsion_facilities()}
    if choice.kind in ("mobile", "airship") and not (set(picked) & movers):
        # The rule mobile bastions have always had, said at the moment it can
        # still be acted on rather than the first time somebody tries to leave.
        have = ", ".join(sorted(
            get_facility(s)["name"] for s in movers if get_facility(s)))
        reasons.append(
            f"A bastion that means to travel needs something aboard that can "
            f"move it ({have or 'a propulsion facility'}).")
    if choice.kind == "airship" and not choice.vessel_slug:
        reasons.append("A flying bastion needs a vessel to be built into.")
    if choice.kind == "keep" and (set(picked) & movers):
        notes.append("A fixed place has no use for propulsion; it will sit idle.")

    cost = price(choice, vessels=vessels)
    if purse_gp and cost > purse_gp:
        reasons.append(
            f"That comes to {cost:g} gp and you have {purse_gp:g}.")

    # Room aboard: advice, never a refusal. A cramped ship is a fair choice.
    if choice.kind == "airship" and choice.vessel_slug:
        v = (vessels or {}).get(choice.vessel_slug) or {}
        room = int(v.get("crew") or 0) + int(v.get("passengers") or 0)
        if room and len(picked) * 4 > room:
            notes.append(
                f"{v.get('name', 'She')} carries {room}; {len(picked)} "
                f"facilities aboard will be a tight fit.")

    return Verdict(ok=not reasons, cost_gp=cost, reasons=reasons, notes=notes)


def describe(choice: Choice, *, vessels: Optional[dict] = None) -> str:
    """One line for the world, in the player's own words where they gave any.

    This is what the place entity and the arrival art get, so the player's
    description leads and the mechanical facts follow it — the picture should
    be of the thing they imagined, not of a list of rooms.
    """
    bits: list[str] = []
    if choice.description.strip():
        bits.append(choice.description.strip())
    if choice.motif.strip():
        bits.append(choice.motif.strip())
    if choice.kind == "airship":
        v = (vessels or {}).get(choice.vessel_slug) or {}
        bits.append(f"built into {v.get('name', 'a vessel')}")
    elif choice.kind == "mobile" and choice.vehicle_kind.strip():
        bits.append(f"built into {choice.vehicle_kind.strip()}")
    named = [get_facility(s) for s in dict.fromkeys(choice.facilities)]
    rooms = ", ".join(f["name"].lower() for f in named if f)
    if rooms:
        bits.append(f"with {rooms}")
    return "; ".join(bits)
