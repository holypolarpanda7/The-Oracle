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
    # What ALREADY in the world could serve as this thread's anchor. A village
    # the party watched burn is a better lost home than one invented at
    # creation, and it costs the world nothing to reuse it — see
    # ``candidates_for``.
    match_types: tuple[str, ...] = ()
    match_statuses: tuple[str, ...] = ()   # empty = anything still standing
    match_cues: tuple[str, ...] = ()       # words in its own recorded history


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
        match_types=(EntityType.PLACE,),
        match_statuses=("destroyed", "ruined", "razed", "burned", "abandoned",
                        "lost", "sacked"),
        match_cues=("burn", "burned", "razed", "sacked", "destroyed", "ruin",
                    "drowned", "flooded", "abandoned", "put to the torch"),
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
        match_types=(EntityType.NPC, EntityType.FACTION),
        match_cues=("burn", "razed", "sacked", "slaughter", "murder", "raid",
                    "betray", "hanged", "killed", "destroyed"),
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
        match_types=(EntityType.NPC,),
        match_statuses=("missing", "vanished", "lost", "departed", "unknown",
                        "taken", "captured"),
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
        match_types=(EntityType.ITEM,),
        match_cues=("stolen", "taken", "lost", "plundered", "looted", "seized"),
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
        match_types=(EntityType.FACTION, EntityType.NPC),
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
        match_types=(EntityType.PLACE,),
        match_cues=("betray", "died", "killed", "lost", "fell", "burned",
                    "opened the gate", "failed"),
    ),
)

_BY_SLUG = {k.slug: k for k in THREAD_KINDS}


def kind(slug: str) -> Optional[ThreadKind]:
    return _BY_SLUG.get((slug or "").strip().lower())


def questions(graph: Optional[WorldGraph] = None,
              species: Optional[str] = None) -> list[dict]:
    """The catalogue as the character-creation screen wants it.

    Given a graph, each question also carries what the WORLD already has that
    could serve as its anchor — offered before the invented option, the same
    order `/cc/origins` puts existing homelands in, and for a stronger reason:
    hitching to a village the party actually watched burn costs the world no
    new entity at all.
    """
    out = []
    for k in THREAD_KINDS:
        row = {
            "slug": k.slug,
            "label": k.label,
            "question": k.question,
            "subject_prompt": k.subject_prompt,
            "wants_subject": k.subject_type is not None,
            "reach": k.reach,
            "suggestions": list(k.suggestions),
            "candidates": [],
        }
        if graph is not None:
            try:
                row["candidates"] = candidates_for(graph, k.slug, species=species)
            except Exception as e:  # noqa: BLE001
                print(f"[threads] candidates for {k.slug} failed: {e}")
        out.append(row)
    return out


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


def _placed_world(graph: WorldGraph) -> tuple[list[tuple[str, geo.Coords]], set[str]]:
    """Every positioned place, and which of them are somebody's thread anchor.

    ONE query returning two COLUMNS, not entities. Placement needs a slug and
    a position; building a full ORM object for each place — and JSON-decoding
    every attribute blob to do it — was the whole remaining cost once the name
    lookup was fixed. Both answers come off the same scan, because they used to
    be two scans of the same table.
    """
    from sqlmodel import select
    places: list[tuple[str, geo.Coords]] = []
    threads: set[str] = set()
    with Session(graph.engine) as s:
        rows = s.exec(select(Entity.slug, Entity.attributes)
                      .where(Entity.type == EntityType.PLACE)).all()
    for slug, attrs in rows:
        if not isinstance(attrs, dict):
            continue
        c = geo.coords_from_attrs(attrs)
        if c is not None:
            places.append((slug, c))
        if attrs.get("thread"):
            threads.add(slug)
    return places, threads


# --- the clearance grid -----------------------------------------------------
# Proving a candidate spot is clear used to mean comparing it against EVERY
# place in the world, because `all()` cannot stop early when the answer is yes.
# That is one great-circle sum per place per candidate, and it grew with the
# world: 53 ms per character at 200 anchors, 367 ms at 800.
#
# But the question is only ever "is anything within N miles", and a place five
# hundred miles away is obviously not. So the world is chopped into cells a
# little wider than the largest radius anyone asks about, and a candidate is
# compared only against its own cell and the eight touching it — everything
# else is further away than N by the geometry of the grid, with no arithmetic
# needed to prove it. Cost stops growing with the world and starts tracking
# only how crowded the neighbourhood is.
_CELL_MI = 30.0


def _cell_of(c: geo.Coords, cell_mi: float) -> tuple[int, int]:
    """Which grid cell a position falls in.

    Latitude is uniform; a degree of LONGITUDE shrinks toward the poles, so the
    cell's longitude width is widened by 1/cos(lat). That makes high-latitude
    cells cover more degrees rather than less ground, which is the direction
    that stays CORRECT — a cell too wide costs a few extra comparisons, a cell
    too narrow silently misses a neighbour and lets two anchors land on top of
    each other.
    """
    deg_lat = cell_mi / (geo.WORLD_CIRCUMFERENCE_MI / 360.0)
    lat_i = int(math.floor(c[0] / deg_lat))
    shrink = max(0.05, math.cos(math.radians(c[0])))
    deg_lon = deg_lat / shrink
    return lat_i, int(math.floor(c[1] / deg_lon))


class _Clearance:
    """Places bucketed by cell, asked 'is anything within N miles of here'."""

    def __init__(self, places: list[tuple[str, geo.Coords]], cell_mi: float):
        self.cell_mi = cell_mi
        self.cells: dict[tuple[int, int], list[geo.Coords]] = {}
        for _slug, c in places:
            self.cells.setdefault(_cell_of(c, cell_mi), []).append(c)

    def nearby(self, c: geo.Coords) -> list[geo.Coords]:
        lat_i, lon_i = _cell_of(c, self.cell_mi)
        out: list[geo.Coords] = []
        for dlat in (-1, 0, 1):
            for dlon in (-1, 0, 1):
                out += self.cells.get((lat_i + dlat, lon_i + dlon), ())
        return out

    def clear_of(self, c: geo.Coords, radius_mi: float) -> bool:
        return all(geo.distance_mi(c, o) >= radius_mi for o in self.nearby(c))


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
    # Other people's threads want a day's room; anything else wants only the
    # cartographer's clearance, so a ruin may still sit near a real town.
    taken, tslugs = _placed_world(graph)
    # Two grids, because the two rules have different radii. The cell must be
    # at least as wide as the radius it answers for, or a neighbouring cell
    # would not be neighbouring enough.
    all_grid = _Clearance(taken, max(_CELL_MI, ANCHOR_CLEARANCE_MI))
    thread_grid = _Clearance([(s_, c) for s_, c in taken if s_ in tslugs],
                             max(_CELL_MI, THREAD_SPACING_MI))

    def clear(c: geo.Coords) -> bool:
        return (all_grid.clear_of(c, ANCHOR_CLEARANCE_MI)
                and thread_grid.clear_of(c, THREAD_SPACING_MI))

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


def _taken_names(graph: WorldGraph) -> set[str]:
    """Every name in use, lowercased — ONE query, one column.

    `graph.find_entities_by_name` answers this for a single name by loading
    every entity in the database and comparing in Python, which is fine once
    and ruinous in a loop: profiling anchor placement at 600 anchors put 87% of
    the time inside it, half a million JSON deserializations to pick a name.
    The collision check needs the name column and nothing else.
    """
    from sqlmodel import select
    with Session(graph.engine) as s:
        return {str(n).lower() for n in s.exec(select(Entity.name)).all() if n}


def _anchor_name(graph: WorldGraph, k: ThreadKind, rng: random.Random) -> str:
    noun = rng.choice(_ANCHOR_NOUNS.get(k.place_hint, ["Waypoint"]))
    taken = _taken_names(graph)
    for _ in range(12):
        name = f"The {rng.choice(_ANCHOR_ADJECTIVES)} {noun}"
        if name.lower() not in taken:
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


# --------------------------------------------------------------------------
# Hitching to what the world ALREADY has
# --------------------------------------------------------------------------
# The cheapest anchor is one that exists. A village the party watched burn is a
# better lost home than one invented at character creation: it costs the world
# no new entity, it sits somewhere with real history, and it ties two players
# to each other — the same argument `/cc/origins` already makes for offering
# the homelands the world has before letting somebody invent one.
#
# Two ways a thing qualifies. Its STATUS may say so outright (the extractor is
# told to set 'destroyed' when narration destroys a place, so a burned village
# is already marked). Or its recorded HISTORY may — an entity named in a
# WorldEvent whose summary reads like the kind of thing this thread is about.
# The second is what catches a village the DM burned in prose without the
# status ever being updated, which is most of them.

# Only offer history the world can still remember clearly.
CANDIDATE_MAX_AGE_DAYS = 3650
_CANDIDATE_LIMIT = 12


def _recent_events(graph: WorldGraph, limit: int = 400) -> list:
    from sqlmodel import select
    from .models import WorldEvent
    with Session(graph.engine) as s:
        return list(s.exec(select(WorldEvent)
                           .order_by(WorldEvent.world_day.desc())
                           .limit(limit)).all())


def candidates_for(
    graph: WorldGraph, kind_slug: str, *, species: Optional[str] = None,
    limit: int = _CANDIDATE_LIMIT,
) -> list[dict]:
    """What the world already has that could BE this thread's anchor.

    Returns rows carrying the reason each one qualifies, because a player
    choosing "the village that burned" needs to be told which village and what
    happened to it — a bare list of names is somebody else's campaign notes.
    """
    from sqlmodel import select

    k = kind(kind_slug)
    if k is None or not k.match_types:
        return []
    today = graph.current_day()

    with Session(graph.engine) as s:
        rows = list(s.exec(select(Entity)
                           .where(Entity.type.in_(list(k.match_types)))).all())
        by_id = {e.id: e for e in rows}
        # Anything already claimed as somebody's anchor is still offerable —
        # two characters out of the same burned village is the POINT — but a
        # place invented for one character's backstory is not history the
        # world made, so it is not offered to the next.
        claimed_invented = {
            e.slug for e in rows
            if isinstance(e.attributes, dict) and e.attributes.get("thread")
        }

    # Why each one qualifies: its own status first, then its recorded history.
    reasons: dict[int, tuple[str, int]] = {}
    for e in rows:
        st = (e.status or "").lower()
        if k.match_statuses and st in k.match_statuses:
            reasons[e.id] = (f"{st} — the world records it", e.created_day or 0)

    if k.match_cues:
        for ev in _recent_events(graph):
            if today - (ev.world_day or 0) > CANDIDATE_MAX_AGE_DAYS:
                continue
            low = (ev.summary or "").lower()
            if not any(c in low for c in k.match_cues):
                continue
            for eid in (ev.involved or []):
                if eid in by_id and eid not in reasons:
                    reasons[eid] = (ev.summary.strip(), ev.world_day or 0)

    out: list[dict] = []
    for eid, (why, day) in reasons.items():
        e = by_id[eid]
        if e.slug in claimed_invented:
            continue
        c = geo.coords_from_attrs(e.attributes)
        fit, note = fit_for(graph, e, species)
        out.append({
            "slug": e.slug, "name": e.name, "type": e.type,
            "status": e.status, "why": why[:160], "day": day,
            "has_coords": c is not None,
            "fit": fit, "fit_note": note,
        })
    # Ones that FIT first, then freshest — the thing that happened most
    # recently is the thing a new character is most plausibly walking out of.
    # An outsider's option is ranked last and never removed: a tiefling raised
    # among humans is a backstory, not a mistake.
    out.sort(key=lambda r: (r["fit"] != "native", -r["day"]))
    return out[:limit]


def attach_thread(
    graph: WorldGraph, pc: Entity, kind_slug: str, summary: str,
    anchor_ref: str, *, subject: Optional[str] = None,
) -> Optional[dict]:
    """Open a thread against something the world ALREADY has.

    The free ride: no place is created, no bearing is rolled, and the anchor
    keeps whatever history put it there. Everything downstream — the DM lines,
    the world slice, resolution — reads it exactly as it reads an invented one,
    because the only difference is who made the entity.
    """
    k = kind(kind_slug)
    if k is None or not (summary or "").strip():
        return None
    anchor = graph.get_entity(anchor_ref)
    if anchor is None:
        return None

    # The edge points at a PLACE for the same reason `open_threads_for` only
    # lists places: a thread is one destination. When the world's own answer is
    # a person or a thing, the destination is wherever it currently is.
    place = anchor
    subject_ent = None
    if anchor.type != EntityType.PLACE:
        subject_ent = anchor
        place = graph.location_of(anchor.slug) or anchor

    graph.add_relation(
        pc.slug, RelationType.UNRESOLVED, place.slug,
        attributes={"kind": k.slug, "summary": summary.strip(),
                    "verb": k.verb, "adopted": True},
    )
    out: dict = {"kind": k.slug, "summary": summary.strip(),
                 "place": place.name, "place_slug": place.slug, "adopted": True}
    if subject_ent is not None:
        graph.add_relation(
            pc.slug, RelationType.UNRESOLVED, subject_ent.slug,
            attributes={"kind": k.slug, "summary": summary.strip(),
                        "verb": k.verb, "adopted": True},
        )
        out["subject"] = subject_ent.name
        out["subject_slug"] = subject_ent.slug
    elif (subject or "").strip():
        # They named somebody the world does not have; that half is still new.
        ent = graph.create_entity(
            subject.strip(), k.subject_type or EntityType.NPC, status="active",
            attributes={"thread": k.slug},
            tags=["thread", k.slug])
        graph.add_relation(ent.slug, RelationType.LOCATED_IN, place.slug)
        graph.add_relation(
            pc.slug, RelationType.UNRESOLVED, ent.slug,
            attributes={"kind": k.slug, "summary": summary.strip(),
                        "verb": k.verb})
        out["subject"] = ent.name
        out["subject_slug"] = ent.slug
    return out


# --------------------------------------------------------------------------
# Does this piece of history fit the character being made?
# --------------------------------------------------------------------------
# A tiefling should not be offered "my sister was taken from the wood elf and
# human village" as though it were the obvious answer. But it must not be
# REFUSED either — a tiefling raised among humans is one of the oldest
# backstories there is, and the book's own tieflings are born to human
# families. So this ranks and ANNOTATES; it never blocks. That is the same
# line `bastion/build.py` draws between a refusal and a note: something merely
# unusual gets a remark, and a gate nobody can argue with is a gate nobody
# uses twice.

# Species words that describe a mixed or unremarkable population — a place
# recorded as any of these tells us nothing about who would look out of place.
_MIXED_PEOPLES = {"folk", "people", "peoples", "mixed", "traders", "settlers"}


def species_tokens(species: Optional[str]) -> set[str]:
    """Loose tokens for a species as character creation writes it.

    Arrives as "Elf (Wood Elf)" or "Human"; both the whole name and its
    lineage matter, and a HALF-species deliberately keeps its parent word so a
    half-elf reads as at home among elves.
    """
    raw = (species or "").strip().lower()
    if not raw:
        return set()
    out = {raw}
    m = re.match(r"^(.*?)\s*\((.*?)\)\s*$", raw)
    if m:
        out |= {m.group(1).strip(), m.group(2).strip()}
    for tok in list(out):
        for word in re.split(r"[^a-z]+", tok.replace("-", " ")):
            if len(word) >= 3 and word not in ("the", "of"):
                out.add(word)
    return {t for t in out if t}


def _peoples_of(graph: WorldGraph, ent: Entity) -> set[str]:
    """Who the world records as living at (or being) this entity.

    A PLACE has no population field — `denizens` is the hazard table, wolves
    and bandits, not the neighbours — so the honest signal is the NPCs the
    world actually put there, which do carry a race, plus whatever the place's
    own description says about its people.
    """
    from sqlmodel import select
    from .models import Relation

    words: set[str] = set()
    attrs = ent.attributes if isinstance(ent.attributes, dict) else {}
    if ent.type != EntityType.PLACE:
        words |= species_tokens(str(attrs.get("race") or ""))
        return {w for w in words if w not in _MIXED_PEOPLES}

    with Session(graph.engine) as s:
        rels = s.exec(select(Relation).where(
            Relation.rel_type == RelationType.LOCATED_IN,
            Relation.dst_id == ent.id,
            Relation.valid_to.is_(None),   # noqa: E711
        )).all()
        for r in rels:
            e = s.get(Entity, r.src_id)
            if e is None or e.type != EntityType.NPC:
                continue
            a = e.attributes if isinstance(e.attributes, dict) else {}
            words |= species_tokens(str(a.get("race") or ""))
    # …and what the place says about itself. Only species the world has heard
    # of, so a description full of ordinary prose contributes nothing.
    desc = str(attrs.get("description") or "").lower()
    for w in re.findall(r"[a-z]+(?:-[a-z]+)?", desc):
        if w in _KNOWN_PEOPLE_WORDS:
            words.add(w)
    return {w for w in words if w not in _MIXED_PEOPLES}


# Species words common enough to be worth reading out of loose prose. Kept
# short on purpose: a miss costs nothing (the candidate is simply not
# annotated), while a false hit would label somebody an outsider at home.
_KNOWN_PEOPLE_WORDS = {
    "human", "humans", "elf", "elves", "dwarf", "dwarves", "halfling",
    "halflings", "gnome", "gnomes", "orc", "orcs", "tiefling", "tieflings",
    "dragonborn", "goliath", "goliaths", "aasimar", "genasi", "githyanki",
    "githzerai", "firbolg", "kenku", "tabaxi", "tortle", "triton", "warforged",
    "changeling", "kalashtar", "shifter", "goblin", "goblins", "hobgoblin",
}


def fit_for(graph: WorldGraph, ent: Entity, species: Optional[str]) -> tuple[str, str]:
    """(fit, note) for offering this entity to a character of ``species``.

    "native" means nobody would blink — including the very common case where
    the world records nothing about who lives there. "outsider" means the
    world DOES record a people and this character is not one of them, which is
    a story rather than a problem, so it is said out loud and still offered.
    """
    if not (species or "").strip():
        return "native", ""
    peoples = _peoples_of(graph, ent)
    if not peoples:
        return "native", ""
    mine = species_tokens(species)
    if mine & peoples:
        return "native", ""
    named = _name_peoples(peoples)
    if not named:
        return "native", ""
    if ent.type == EntityType.PLACE:
        return "outsider", f"mostly {named} — you would have been an outsider there"
    return "outsider", f"{named} — not your own people"


# The matcher works on loose tokens, so a wood-elf village's people come out as
# {"elf", "elves", "wood", "wood elf", "elf (wood elf)"}. That set is right for
# COMPARING and unreadable for SAYING, and printing the first three of it
# alphabetically cut "humans" off a village that was half human. Display is its
# own step, over canonical plurals only.
_PEOPLE_PLURAL = {
    "human": "humans", "humans": "humans", "elf": "elves", "elves": "elves",
    "dwarf": "dwarves", "dwarves": "dwarves", "halfling": "halflings",
    "halflings": "halflings", "gnome": "gnomes", "gnomes": "gnomes",
    "orc": "orcs", "orcs": "orcs", "tiefling": "tieflings",
    "tieflings": "tieflings", "dragonborn": "dragonborn",
    "goliath": "goliaths", "goliaths": "goliaths", "aasimar": "aasimar",
    "genasi": "genasi", "firbolg": "firbolgs", "kenku": "kenku",
    "tabaxi": "tabaxi", "tortle": "tortles", "triton": "tritons",
    "warforged": "warforged", "changeling": "changelings",
    "kalashtar": "kalashtar", "shifter": "shifters", "goblin": "goblins",
    "goblins": "goblins", "hobgoblin": "hobgoblins",
}


def _name_peoples(peoples: set[str], limit: int = 2) -> str:
    """A readable "elves and humans" out of the comparison tokens."""
    seen: list[str] = []
    for w in sorted(peoples):
        canon = _PEOPLE_PLURAL.get(w)
        if canon and canon not in seen:
            seen.append(canon)
    if not seen:
        return ""
    if len(seen) == 1:
        return seen[0]
    head, tail = seen[:limit][:-1], seen[:limit][-1]
    return (", ".join(head) + " and " + tail) if head else tail
