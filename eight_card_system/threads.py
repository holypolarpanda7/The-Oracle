"""Unresolved threads — the part of a backstory the DM can actually use.

A personality trait ("I am brave", "I quote scripture") describes how somebody
behaves in a scene that is already happening. It is no help at all to a DM
answering the one question players actually ask at a loose end: *what could we
do next?* What answers that is a thread left OPEN — something lost, taken,
owed, or done — because every one of those carries a verb with it. A burned
village is not a character detail; it is a quest with the target left blank.

So a thread is not stored as prose. It follows the rule the origin ties
already follow — **an origin is world state, not paragraph decoration** — and
becomes real entities:

  * a PLACE the thread LEADS TO, with real coordinates, so the journey can be
    costed by ``[[ROUTES]]`` and drawn by the mapmaker like anywhere else;
  * optionally a SUBJECT (the person who did it, the heirloom taken, the
    people owed) standing at that place;
  * an ``UNRESOLVED`` edge from the PC to the place, carrying the kind.

Which buys three things prose cannot. The DM's world slice surfaces it as
state rather than as a paragraph they must remember to honour; resolving it
CHANGES the world, so it stops being suggested; and another player's character
can walk into that ruin, because it is a place and not a sentence.

**Where an anchor lands is the scale decision.** It is placed on a bearing and
at a distance seeded from the CHARACTER, never beside wherever they happen to
be standing — so a hundred players' backstories scatter around the globe and
seed the map outward, instead of piling a hundred ruins on the starting
village. Deterministic for the cartographer's reason: a retry must produce the
same map.

The world knows where a thread leads and the PLAYER does not — the anchor is
created ``unexplored``, the ``hoards.py`` bargain. The DM is told a direction
and a rough travel time, which is what a rumour in a taproom carries anyway.
"""
from __future__ import annotations

import hashlib
import math
import random
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlmodel import Session

from . import geo
from .graph import WorldGraph
from .models import Entity, EntityType, PlaceScale, RelationType

# How far a thread's anchor lands, by reach band. A thread you can walk to in
# a week is a different offer from one that is a campaign away, and the DM
# needs to be able to tell them apart when suggesting something.
REACH_MILES = {
    "near": (60.0, 140.0),        # days away — a detour
    "far": (180.0, 420.0),        # weeks — a journey you decide on
    "distant": (500.0, 1100.0),   # a campaign's worth of world in between
}
# Never drop an anchor on top of something that already exists (the
# cartographer's own clearance rule, same number for the same reason).
ANCHOR_CLEARANCE_MI = 6.0
# But two THREADS want much more room than that. Six miles keeps a ruin from
# landing inside a village; it does nothing about a hundred players' ruins
# crowding one valley, and the failure that produces is a world that is more
# backstory than country — you walk past three strangers' burned homes on the
# way to your own. A day on foot is the honest minimum between two people's
# pasts. Measured: at 300 characters a 6-mile rule left a MEDIAN gap of 16 mi
# and two thirds of anchors within a day of another.
THREAD_SPACING_MI = 24.0
# How many bearings to try before giving up on a clear spot.
_PLACEMENT_TRIES = 24
# When every try in the band is crowded, step the band OUTWARD and try again.
# This is what makes the system scale: the planet is 50 million square miles
# and the bands reach 1,100, so a filling world should spend the rest of it
# rather than pack tighter around the starting village.
_CONGESTION_STEPS = 6
_CONGESTION_FACTOR = 1.6


@dataclass(frozen=True)
class ThreadKind:
    """One kind of unfinished business, and everything the rest of the code
    needs to turn an answer into world state and a suggestion.

    ``verb`` is the whole point: it is what makes a thread a hook rather than
    flavour. ``suggestions`` are self-authored examples — they exist so a
    player faces a choice instead of a blank box, and are deliberately generic
    so that nothing here is derived from a book.
    """
    slug: str
    label: str
    question: str
    verb: str                 # what the DM can offer to DO about it
    place_hint: str           # what the anchor place IS, when we name it
    subject_type: Optional[str] = None   # an NPC/FACTION/ITEM standing there
    subject_prompt: Optional[str] = None
    reach: str = "far"
    suggestions: tuple[str, ...] = ()
    # How the DM is told about it. ``{who}``/``{what}``/``{where}`` are filled.
    hook: str = "{who} has unfinished business with {what}."


THREAD_KINDS: tuple[ThreadKind, ...] = (
    ThreadKind(
        slug="lost-home",
        label="Somewhere you can't go back to",
        question="Is there a place you can't return to — burned, drowned, taken, or simply closed to you?",
        verb="go back, and see what is left of it",
        place_hint="ruin",
        reach="far",
        suggestions=(
            "the village burned while I was away",
            "the holding my family lost to a debt",
            "a valley drowned when the dam was raised",
            "the quarter I was exiled from",
            "a steading swallowed by the marsh",
        ),
        hook="{who} has never gone back to {what} ({where}).",
    ),
    ThreadKind(
        slug="vengeance",
        label="A wrong done to you or yours",
        question="Did somebody do something to you or your people that is still unanswered?",
        verb="find them and settle it",
        place_hint="stronghold",
        subject_type=EntityType.NPC,
        subject_prompt="who did it — a name, a title, or a band",
        reach="distant",
        suggestions=(
            "the captain who put my village to the torch",
            "the lord who hanged my brother on a false charge",
            "the raiders who took our winter stores",
            "the guild that ruined my family and called it law",
            "whoever it was — I never saw a face",
        ),
        hook="{who} is owed an answer by {what}, and has never had it ({where}).",
    ),
    ThreadKind(
        slug="missing",
        label="Someone you're looking for",
        question="Is there somebody you're trying to find?",
        verb="pick up the trail and find them",
        place_hint="last-seen",
        subject_type=EntityType.NPC,
        subject_prompt="who you're looking for",
        reach="far",
        suggestions=(
            "my sister, who went to the capital and stopped writing",
            "the master who taught me and then vanished",
            "a parent I have never met",
            "the friend I left behind when I ran",
            "the person who paid my ransom, so I could thank them",
        ),
        hook="{who} is still looking for {what} ({where}).",
    ),
    ThreadKind(
        slug="taken",
        label="Something taken from you",
        question="Is there something of yours out there in somebody else's hands?",
        verb="track it down and take it back",
        place_hint="hoard",
        subject_type=EntityType.ITEM,
        subject_prompt="what was taken",
        reach="far",
        suggestions=(
            "my family's blade, sold to pay a debt",
            "the signet that proves who I am",
            "a book my order would kill to have back",
            "the ring my mother was buried in, before it was dug up",
            "my name — somebody else is using it",
        ),
        hook="{who} means to get {what} back ({where}).",
    ),
    ThreadKind(
        slug="debt",
        label="Something you owe",
        question="Do you owe somebody — coin, a favour, or an oath you have not kept?",
        verb="go and settle up, one way or another",
        place_hint="seat",
        subject_type=EntityType.FACTION,
        subject_prompt="who holds the debt",
        reach="near",
        suggestions=(
            "coin I borrowed from people who do not forget",
            "an oath I swore and walked away from",
            "the passage somebody paid for me",
            "a life I was given and have not earned",
            "a promise to return, made to someone still waiting",
        ),
        hook="{who} owes {what}, and has not settled it ({where}).",
    ),
    ThreadKind(
        slug="guilt",
        label="A wrong you did",
        question="Is there something you did that you would undo if you could?",
        verb="go back and try to put it right",
        place_hint="site",
        reach="near",
        suggestions=(
            "I ran, and people I could have helped did not",
            "I gave up a name under questioning",
            "I took something that was not mine and it cost somebody",
            "I let the wrong person walk free",
            "I was the one who opened the gate",
        ),
        hook="{who} is trying to make up for {what} ({where}).",
    ),
)

_BY_SLUG = {k.slug: k for k in THREAD_KINDS}


def kind(slug: str) -> Optional[ThreadKind]:
    return _BY_SLUG.get((slug or "").strip().lower())


def questions() -> list[dict]:
    """The catalogue as the character-creation screen wants it."""
    return [
        {
            "slug": k.slug,
            "label": k.label,
            "question": k.question,
            "subject_prompt": k.subject_prompt,
            "wants_subject": k.subject_type is not None,
            "reach": k.reach,
            "suggestions": list(k.suggestions),
        }
        for k in THREAD_KINDS
    ]


# --------------------------------------------------------------------------
# Placement — where a thread's anchor lands
# --------------------------------------------------------------------------

def _seed_for(pc_slug: str, kind_slug: str) -> int:
    """A stable seed per (character, thread kind).

    Deterministic for the cartographer's reason: a retried registration must
    lay the same map down, not a second ruin beside the first.
    """
    h = hashlib.sha256(f"{pc_slug}|{kind_slug}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big")


def _coordful_places(s: Session) -> list[tuple[str, geo.Coords]]:
    from sqlmodel import select
    out: list[tuple[str, geo.Coords]] = []
    for e in s.exec(select(Entity).where(Entity.type == EntityType.PLACE)).all():
        c = geo.coords_from_attrs(e.attributes)
        if c is not None:
            out.append((e.slug, c))
    return out


def anchor_coords(
    graph: WorldGraph, pc_slug: str, k: ThreadKind,
    *, origin: Optional[geo.Coords] = None,
) -> tuple[geo.Coords, float, float]:
    """Pick a clear spot for this character's thread: (coords, miles, bearing).

    Bearing and distance are drawn from the character's own seed rather than
    from where they are standing, which is the whole scale argument: a hundred
    players' ruins scatter around the world and seed the map outward, where
    "just past the party" would pile a hundred of them on the starting
    village and leave the rest of the planet empty.
    """
    rng = random.Random(_seed_for(pc_slug, k.slug))
    lo, hi = REACH_MILES.get(k.reach, REACH_MILES["far"])
    start = origin or (geo.ORIGIN_LAT, geo.ORIGIN_LON)
    with Session(graph.engine) as s:
        taken = _coordful_places(s)
    # Other people's threads want a day's room; anything else wants only the
    # cartographer's clearance, so a ruin may still sit near a real town.
    tslugs = _thread_slugs(graph)   # once — this is a query, not a lookup
    other_threads = [c for slug, c in taken if slug in tslugs]

    def clear(c: geo.Coords) -> bool:
        if any(geo.distance_mi(c, o) < ANCHOR_CLEARANCE_MI for _, o in taken):
            return False
        return all(geo.distance_mi(c, o) >= THREAD_SPACING_MI for o in other_threads)

    best: Optional[tuple[geo.Coords, float, float]] = None
    for step in range(_CONGESTION_STEPS):
        # Area-uniform within the ring: drawing the distance flat piles points
        # toward the inner edge, because a ring's area grows with its radius.
        span_lo = lo * (_CONGESTION_FACTOR ** step)
        span_hi = hi * (_CONGESTION_FACTOR ** step)
        for _ in range(_PLACEMENT_TRIES):
            bearing = rng.uniform(0.0, 360.0)
            miles = math.sqrt(rng.uniform(span_lo ** 2, span_hi ** 2))
            c = geo.offset_bearing(start, bearing, miles)
            if best is None:
                best = (c, miles, bearing)
            if clear(c):
                return c, miles, bearing
    # Every ring out to the far side of the world was crowded. Take the first
    # spot rather than refusing to make the thread at all — a tight map is a
    # better failure than a lost past.
    return best  # type: ignore[return-value]


def _thread_slugs(graph: WorldGraph) -> set[str]:
    """Slugs of places that are somebody's thread anchor."""
    from sqlmodel import select
    with Session(graph.engine) as s:
        rows = s.exec(select(Entity).where(Entity.type == EntityType.PLACE)).all()
    return {e.slug for e in rows
            if isinstance(e.attributes, dict) and e.attributes.get("thread")}


_ANCHOR_NOUNS = {
    "ruin": ["Ashes", "Burnt Acre", "Hollow", "Cinderfield", "Drowned Rows"],
    "stronghold": ["Keep", "Bastion", "Watch", "Hold", "Redoubt"],
    "last-seen": ["Crossing", "Waystation", "Last Road", "Turning", "Ferry"],
    "hoard": ["Vault", "Strongroom", "Cache", "Reliquary", "Countinghouse"],
    "seat": ["Chapterhouse", "Counting Hall", "Seat", "Lodge", "Exchange"],
    "site": ["Gate", "Ford", "Field", "Stair", "Threshold"],
}
_ANCHOR_ADJECTIVES = ["Grey", "Silent", "Cold", "Broken", "Far", "Old",
                      "Sunken", "Quiet", "Bitter", "Long"]


def _anchor_name(graph: WorldGraph, k: ThreadKind, rng: random.Random) -> str:
    noun = rng.choice(_ANCHOR_NOUNS.get(k.place_hint, ["Waypoint"]))
    for _ in range(12):
        name = f"The {rng.choice(_ANCHOR_ADJECTIVES)} {noun}"
        if not graph.find_entities_by_name(name):
            return name
    return f"The {rng.choice(_ANCHOR_ADJECTIVES)} {noun} ({rng.randint(2, 99)})"


def open_thread(
    graph: WorldGraph,
    pc: Entity,
    kind_slug: str,
    summary: str,
    *,
    subject: Optional[str] = None,
    place_name: Optional[str] = None,
    origin: Optional[geo.Coords] = None,
) -> Optional[dict]:
    """Turn one answer into world state. Returns a description, or None.

    ``summary`` is the player's own words. ``subject`` names the person, the
    people or the thing when the kind wants one; ``place_name`` is used when
    the player named the place themselves, and otherwise one is rolled.
    """
    k = kind(kind_slug)
    if k is None or not (summary or "").strip():
        return None

    rng = random.Random(_seed_for(pc.slug, k.slug))
    coords, miles, _bearing = anchor_coords(graph, pc.slug, k, origin=origin)
    climate = geo.climate_for(coords)
    name = (place_name or "").strip() or _anchor_name(graph, k, rng)

    # The anchor is a place like any other place: real coordinates, so
    # [[ROUTES]] can cost the journey and the mapmaker can draw it — and
    # UNEXPLORED, because the world knows where this leads and the player does
    # not (the hoards.py bargain).
    anchor = graph.create_entity(
        name, EntityType.PLACE, subtype=PlaceScale.POI, status="unexplored",
        attributes={
            "stub": True,
            "thread": k.slug,
            "description": f"{k.place_hint.replace('-', ' ').title()} — {summary.strip()}",
            "coords": geo.coords_attr(*coords),
            "climate": climate,
        },
        tags=["thread", k.slug, "stub"],
    )
    graph.add_relation(
        pc.slug, RelationType.UNRESOLVED, anchor.slug,
        attributes={
            "kind": k.slug, "summary": summary.strip(),
            "verb": k.verb, "miles": round(miles),
        },
    )

    out: dict = {
        "kind": k.slug, "summary": summary.strip(),
        "place": anchor.name, "place_slug": anchor.slug,
        "miles": round(miles),
    }

    # The person, the people or the thing at the far end of it. Located AT the
    # anchor, so finding the place and finding them is one journey.
    subj = (subject or "").strip()
    if k.subject_type and subj:
        ent = graph.create_entity(
            subj, k.subject_type, status="active",
            attributes={"thread": k.slug,
                        "description": f"{k.subject_prompt or 'subject'}: {summary.strip()}"},
            tags=["thread", k.slug],
        )
        graph.add_relation(ent.slug, RelationType.LOCATED_IN, anchor.slug)
        graph.add_relation(
            pc.slug, RelationType.UNRESOLVED, ent.slug,
            attributes={"kind": k.slug, "summary": summary.strip(), "verb": k.verb},
        )
        out["subject"] = ent.name
        out["subject_slug"] = ent.slug
    return out


# --------------------------------------------------------------------------
# Reading them back — what the DM is offered, and closing one out
# --------------------------------------------------------------------------

def open_threads_for(graph: WorldGraph, pc_ref) -> list[dict]:
    """Every thread this character still has open, nearest first.

    Only PLACE anchors are listed: the subject edge exists so the world slice
    reaches the person, but a thread is ONE offer and listing it twice would
    read to the DM as two.
    """
    from sqlmodel import select
    from .models import Relation

    out: list[dict] = []
    with Session(graph.engine) as s:
        pc = graph._resolve_entity(s, pc_ref)
        if pc is None:
            return out
        # A character registered but not yet placed has no position of their
        # own; the starting settlement is where they will walk out from, so a
        # direction is always available rather than sometimes missing.
        here = graph._coords_in_db(s, pc) or (geo.ORIGIN_LAT, geo.ORIGIN_LON)
        rels = s.exec(select(Relation).where(
            Relation.src_id == pc.id,
            Relation.rel_type == RelationType.UNRESOLVED,
            Relation.valid_to.is_(None),   # noqa: E711
        )).all()
        for r in rels:
            dst = s.get(Entity, r.dst_id)
            if dst is None or dst.type != EntityType.PLACE:
                continue
            attrs = r.attributes if isinstance(r.attributes, dict) else {}
            k = kind(str(attrs.get("kind") or ""))
            if k is None:
                continue
            c = geo.coords_from_attrs(dst.attributes)
            miles = (geo.distance_mi(here, c) if (here and c)
                     else float(attrs.get("miles") or 0.0))
            # Who or what stands at the far end, if the thread named one.
            subject = None
            for r2 in rels:
                if r2 is r:
                    continue
                e2 = s.get(Entity, r2.dst_id)
                a2 = r2.attributes if isinstance(r2.attributes, dict) else {}
                if e2 is not None and e2.type != EntityType.PLACE \
                        and str(a2.get("kind") or "") == k.slug:
                    subject = e2.name
                    break
            out.append({
                "kind": k.slug,
                "label": k.label,
                "summary": str(attrs.get("summary") or ""),
                "verb": k.verb,
                "place": dst.name,
                "place_slug": dst.slug,
                "subject": subject,
                "miles": round(miles),
                "bearing": (geo.compass_between(here, c) if (here and c) else None),
                "travel": geo.travel_time_str(miles) if miles else None,
                "hook": k.hook,
            })
    out.sort(key=lambda t: t["miles"])
    return out


def hook_lines(graph: WorldGraph, pc_ref, who: str) -> list[str]:
    """The open threads as lines for the DM's brief.

    A direction and a rough walking time — which is exactly what a rumour in a
    taproom carries, and is the same line ``[[ROUTES]]`` holds: no
    coordinates, no bearings in degrees, nothing a map would tell you.
    """
    lines: list[str] = []
    for t in open_threads_for(graph, pc_ref):
        # The PLACE is the thing when the thread named no person — a burned
        # village is a destination, and the player's sentence about it is not.
        what = t["subject"] or t["place"]
        where_bits = []
        if t.get("bearing") and t["bearing"] != "here":
            where_bits.append(t["bearing"])
        if t.get("travel"):
            where_bits.append(t["travel"])
        where = ", ".join(where_bits) if where_bits else "somewhere out there"
        line = t["hook"].format(who=who, what=what, where=where)
        # Quoted, and attributed. The player wrote it in the first person
        # ("my sister"), so run on as the DM's own prose it says the wrong
        # thing about the wrong person.
        if t["summary"]:
            line += f' Their words: "{t["summary"]}".'
        lines.append(f"- {line} They could {t['verb']}.")
    return lines


def mentions_thread(open_: list[dict], message: str) -> bool:
    """Did the player bring their OWN past up by name?

    The second half of the gate. A character naming the ruin they came from,
    or the person they are looking for, is asking about it as plainly as any
    of the stock phrases — and this is the half that reads a particular
    player's answers rather than a fixed word list, so it has to be asked of
    the threads themselves.
    """
    low = (message or "").lower()
    if not low.strip():
        return False
    for t in open_:
        for field in ("place", "subject"):
            name = (t.get(field) or "").strip().lower()
            if not name:
                continue
            # On WORD BOUNDARIES, never as a substring — the same rule
            # `setpieces.landmark_for` follows, and for the same reason: "The
            # Ford" is inside "afford", and a length guard does not help,
            # because the name that trips it is a real four-letter name.
            # A leading article is optional: a player writes "we go to
            # Ashmere", not "we go to The Grey Ford" word for word.
            bare = re.sub(r"^(the|a|an)\s+", "", name)
            for cand in {name, bare}:
                if cand and re.search(rf"\b{re.escape(cand)}\b", low):
                    return True
    return False


def resolve_thread(
    graph: WorldGraph, pc_ref, kind_slug: str, outcome: str = "",
) -> int:
    """Close a thread out. Returns how many edges were closed.

    Closing is what stops it being suggested, and it is the reason a thread is
    world state rather than prose: a paragraph in a text box goes on saying
    the village is burned long after the party rebuilt it.
    """
    from sqlmodel import select
    from .models import Relation

    k = kind(kind_slug)
    if k is None:
        return 0
    closed = 0
    with Session(graph.engine) as s:
        pc = graph._resolve_entity(s, pc_ref)
        if pc is None:
            return 0
        rels = s.exec(select(Relation).where(
            Relation.src_id == pc.id,
            Relation.rel_type == RelationType.UNRESOLVED,
            Relation.valid_to.is_(None),   # noqa: E711
        )).all()
        targets = [r for r in rels
                   if str((r.attributes or {}).get("kind") or "") == k.slug]
        pc_slug = pc.slug
        dsts = [s.get(Entity, r.dst_id) for r in targets]
    for dst in dsts:
        if dst is None:
            continue
        closed += graph.close_relation(pc_slug, RelationType.UNRESOLVED, dst.slug)
    if closed:
        graph.add_event(
            f"{k.label}: {outcome or 'settled'}",
            involved=[pc_slug] + [d.slug for d in dsts if d is not None],
        )
    return closed
