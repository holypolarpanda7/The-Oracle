"""
Ventures — an NPC steps out of their role and goes questing on their own account.

The world already had NPCs who *are* something (a role, a disposition, a hook)
and quests the PARTY takes. It had nothing in between: nobody in it ever WANTED
anything badly enough to act on it. A venture is that missing middle. The
blacksmith who has been saying for three sessions that his brother never came
back from the hills one day packs a hammer and goes, and whether he comes home
is a fact about the world, not about whether the players were interested.

Four properties, and every one of them is load-bearing:

* **They are not companions.** A companion is a body the party directs
  (``travels_with``, ``CompanionControl``). A venturer LEADS; the party may
  ``accompany`` them (pc -> npc) and may stop at any moment. The relation is
  deliberately the mirror of the companion one so nothing can confuse the two.
* **They progress unwatched.** ``advance_ventures`` runs on the entropy cadence
  and rolls each unaccompanied venture forward a stage at a time. The party
  hears about it afterwards, through the event log, like everyone else.
* **They have DEPTH, 1 to 3.** A venture is a ladder of stages ending in a
  climax; a one-stage venture is an errand, a three-stage one is a small saga.
  Depth is chosen at birth, so a table can watch a thread it joined at stage one
  pay off two stages later.
* **They MUTATE the world when they land.** Success and failure both leave a
  mark through primitives that already exist — a place's danger, an NPC's role,
  a new item, a death, a successor taking the post.

A venture IS a QUEST entity (tier ``venture``), which buys three things for
nothing: it shows up in the journal, ``_quests_touching`` pulls it into the DM's
world slice whenever its NPC is nearby, and entropy's main-cast protection
already refuses to let time quietly kill a quest-involved NPC — which is exactly
the person whose errand is half-finished.

What it never does: it does not take a party STAKES clock. Those escalate on the
party's neglect, and a venture's whole point is that neglect is not what decides
it — the venturer is out there deciding it themselves.
"""
from __future__ import annotations

import random
from typing import Optional

from sqlmodel import Session, select

from .graph import WorldGraph, slugify
from .models import (Entity, EntityType, QuestState, QuestTier, Relation,
                     RelationType)

# --- tuning ----------------------------------------------------------------
STEP_DAYS = 6              # world-days between attempts at the current stage
MAX_LIVE = 4               # ventures underway in the world at once
NEW_PER_PASS = 1           # how many may be born on one pass
# The chance that a pass produces ANYTHING, rolled once for the pass rather
# than once per candidate — per candidate it is not a rate limit at all, since
# a town of ten people gets ten chances and something is born almost every time.
BIRTH_CHANCE = 0.3
DEPTH_WEIGHTS = (0.45, 0.35, 0.20)   # depth 1 / 2 / 3
COOLDOWN_DAYS = 90         # a season before the same person sets out again

# A venture dies at `depth + SETBACK_GRACE` setbacks, and every setback makes
# the current stage HARDER — they have spent their surprise and the opposition
# is awake now. Two things fall out of that, both wanted: a one-step errand
# usually comes off, and a three-step saga is close to a coin flip. DEPTH is
# therefore a real dial on how likely this is to end well, which is what makes
# a long venture worth walking beside somebody.
SETBACK_GRACE = 2
SETBACK_DC_STEP = 2

# Accompanying a venture to a good end is worth real standing with its owner.
TRUST_ON_SUCCESS = 15
TRUST_ON_FAILURE = -4      # they don't blame you much; they blame themselves

# --- working against one -----------------------------------------------------
# Opposition is worth about the same as one setback's worth of DC, and unlike a
# setback it is not baked into the stored stage — it lifts the bar only while
# the party is actually set against them, and stops the moment they relent.
OPPOSED_DC = 3
# Whether the venturer ever learns WHO. Rolled once, when the venture ends,
# because a discovery clock ticking through a covert operation is a lot of
# machinery for a question that only matters at the end. The more the party
# actually interfered, the more there is to trace back.
DISCOVERY_BASE = 0.2
DISCOVERY_PER_ACT = 0.15
DISCOVERY_MAX = 0.85


# ---------------------------------------------------------------------------
# What an NPC of a given trade wants badly enough to leave the shop for.
# ---------------------------------------------------------------------------
# A venture is: a GOAL (one line, the NPC's own words' worth), up to two
# preparatory STEPS, and a CLIMAX. Depth picks `steps[:depth-1] + [climax]`, so
# the climax is always the last thing that happens and a depth-1 venture is a
# whole small story rather than a truncated big one.
#
# `{home}` and `{away}` are filled with real place names from the graph — the
# same discipline the rest of the project keeps: the fiction may say "the old
# barrow", the CODE says which square of the world that is.
#
# `outcome` decides what the world does about it (see _apply_outcome):
#   safer    — the venture is about a threat; success calms a place, failure feeds it
#   standing — it is about the NPC's position; success promotes them, failure ruins it
#   wealth   — it is about money; it moves their fortune and their town's
#   relic    — it is about a thing; success puts a real ITEM in their hands
#   peril    — it can kill them, and failure does

_MARTIAL = [
    {"goal": "hunt down the raiders who have been bleeding the road",
     "steps": ["press the survivors in {home} for a description of the band",
               "shadow the road out of {home} until the raiders show their hand"],
     "climax": "corner the band at {away} and end them",
     "outcome": "safer", "away": True, "hard": 2},
    {"goal": "win the captaincy that was promised and then quietly given away",
     "steps": ["gather the muster rolls that prove who actually held the wall",
               "put the case to whoever holds authority in {home}"],
     "climax": "stand the trial by arms the disputed post is decided by",
     "outcome": "standing", "away": False, "hard": 0},
    {"goal": "bring home the body of a soldier left where they fell",
     "steps": ["find the one witness who knows where the line broke",
               "beg or buy an escort as far as {away}"],
     "climax": "walk into {away} and carry them out",
     "outcome": "peril", "away": True, "hard": 3},
]

_DEVOUT = [
    {"goal": "put down the thing that has been taking the dead out of the ground",
     "steps": ["keep vigil in {home} until the pattern of the nights is plain",
               "consecrate what is needed and gather hands willing to hold a torch"],
     "climax": "go into {away} and finish it at its root",
     "outcome": "safer", "away": True, "hard": 2},
    {"goal": "carry a relic back to the shrine it was stripped from",
     "steps": ["trace which hands the relic passed through after {home}",
               "raise the price, or the leverage, to get it back"],
     "climax": "take the relic out of {away} and set it where it belongs",
     "outcome": "relic", "away": True, "hard": 1},
    {"goal": "found a chapel here, against a town that does not want one",
     "steps": ["preach in the open in {home} until somebody listens",
               "win over the households that would have to pay for it"],
     "climax": "raise the roof-beam before the season turns",
     "outcome": "standing", "away": False, "hard": 0},
]

_MERCANTILE = [
    {"goal": "open a trade road that everyone says cannot be kept open",
     "steps": ["find out from the drovers in {home} why the last three trains failed",
               "buy the carts and the guards, and go once themselves"],
     "climax": "bring a loaded train through {away} and back",
     "outcome": "wealth", "away": True, "hard": 2},
    {"goal": "clear a debt that is about to cost them the shop",
     "steps": ["sell off everything in {home} that is not the trade itself",
               "find who actually holds the note now, and what they want"],
     "climax": "settle with the debt-holder, one way or another",
     "outcome": "wealth", "away": False, "hard": 1},
    {"goal": "buy out the rival who has been undercutting them for a year",
     "steps": ["learn where the rival's stock is really coming from",
               "quietly take up every supplier the rival depends on"],
     "climax": "make an offer in {home} that cannot be refused",
     "outcome": "standing", "away": False, "hard": 1},
]

_CRAFT = [
    {"goal": "make the one piece their trade will be remembered for",
     "steps": ["gather materials no one in {home} stocks",
               "go to {away} for the last of what the work needs"],
     "climax": "work it through, and find out whether the hands are still good",
     "outcome": "relic", "away": True, "hard": 1},
    {"goal": "reopen the workings that fed this town before it was abandoned",
     "steps": ["find the old survey, or someone who remembers the way in",
               "hire the labour {home} can barely spare"],
     "climax": "break into the old workings at {away} and see what took them",
     "outcome": "safer", "away": True, "hard": 2},
    {"goal": "take the guild seat their family lost two generations back",
     "steps": ["produce work good enough that the guild has to look at it",
               "find the vote in {home} that can be turned"],
     "climax": "stand for the seat and take it",
     "outcome": "standing", "away": False, "hard": 0},
]

_LEARNED = [
    {"goal": "prove what is really written on the stones nobody can read",
     "steps": ["copy every inscription still legible in {home}",
               "find a hand that has seen the older script"],
     "climax": "go to {away} where the rest of it stands, and read it",
     "outcome": "relic", "away": True, "hard": 1},
    {"goal": "break the ring that has been quietly robbing this town by ledger",
     "steps": ["get at the accounts nobody is supposed to see",
               "find one clerk in {home} frightened enough to talk"],
     "climax": "lay the whole thing out where it cannot be buried",
     "outcome": "standing", "away": False, "hard": 2},
    {"goal": "chart the country past {away} before somebody else claims it",
     "steps": ["outfit for a season away from any road",
               "find a guide who has been out that far and come back"],
     "climax": "walk the ground beyond {away} and bring the survey home",
     "outcome": "wealth", "away": True, "hard": 2},
]

_UNDERWORLD = [
    {"goal": "settle a score that has been waiting three winters",
     "steps": ["find where the one they want has gone to ground",
               "buy the silence of everyone who would warn them"],
     "climax": "walk in at {away} and collect",
     "outcome": "peril", "away": True, "hard": 2},
    {"goal": "take over the trade in {home} from the crew that runs it now",
     "steps": ["turn one of the crew, cheaply, before anyone notices",
               "cut the crew off from the coin that keeps them loyal"],
     "climax": "take the trade, and hold it through the week after",
     "outcome": "standing", "away": False, "hard": 2},
    {"goal": "lift the one thing worth more than everything they have stolen so far",
     "steps": ["learn the house and its habits from the servants",
               "get hold of the keys, the plan, or the person who has both"],
     "climax": "do the job at {away} and get clear",
     "outcome": "relic", "away": True, "hard": 3},
]

_RUSTIC = [
    {"goal": "find out what has been taking the stock, and stop it",
     "steps": ["sit up three nights with the flock and see it for themselves",
               "follow the drag-marks as far as they run"],
     "climax": "put an end to it at {away}",
     "outcome": "safer", "away": True, "hard": 1},
    {"goal": "bring back a kinsman who went to {away} and never wrote",
     "steps": ["scrape together the money for the road",
               "learn at the last waystation whether they got that far"],
     "climax": "find them at {away} — living or otherwise",
     "outcome": "peril", "away": True, "hard": 2},
    {"goal": "get the water back to the fields before the season is lost",
     "steps": ["walk the whole ditch and find where it was cut",
               "get {home} to agree on who pays for the mending"],
     "climax": "put it right, over whoever cut it in the first place",
     "outcome": "safer", "away": False, "hard": 1},
]

_COMMON = [
    {"goal": "make good on a promise they have been dodging for years",
     "steps": ["face the person they made it to",
               "find what keeping it would actually cost"],
     "climax": "keep it, at whatever that turns out to be",
     "outcome": "standing", "away": False, "hard": 0},
    {"goal": "get out of {home} for good, and have somewhere to go",
     "steps": ["turn everything they own into coin",
               "find a company or a berth going the right way"],
     "climax": "take the road out through {away} and not look back",
     "outcome": "wealth", "away": True, "hard": 1},
    {"goal": "answer a thing they saw once and have never been able to explain",
     "steps": ["find anyone else in {home} who saw it too",
               "learn what the old people say happens out that way"],
     "climax": "go back to {away}, at the same hour, and look",
     "outcome": "peril", "away": True, "hard": 2},
]

_FAMILIES: dict[str, list[dict]] = {
    "martial": _MARTIAL, "devout": _DEVOUT, "mercantile": _MERCANTILE,
    "craft": _CRAFT, "learned": _LEARNED, "underworld": _UNDERWORLD,
    "rustic": _RUSTIC, "common": _COMMON,
}

# Role word -> family. Matched on WORD boundaries against the role, so "guard
# sergeant" and "sergeant" both land in martial and "grain factor" doesn't get
# read as a factor of anything else. Order matters only in that the first
# family with a hit wins; roles are short enough that overlaps are rare.
_ROLE_FAMILY: dict[str, tuple[str, ...]] = {
    "martial": ("guard", "captain", "sergeant", "soldier", "watchman", "marshal",
                "warden", "hunter", "ranger", "mercenary", "quartermaster",
                "drill", "bounty"),
    "devout": ("priest", "acolyte", "cleric", "almoner", "gravedigger", "monk",
               "abbot", "oracle", "hermit", "templar"),
    "mercantile": ("merchant", "trader", "factor", "moneychanger", "innkeeper",
                   "keeper", "carter", "peddler", "broker", "banker", "victualler",
                   "harbormaster", "toll"),
    "craft": ("smith", "blacksmith", "cooper", "weaver", "tanner", "brewer",
              "mason", "carpenter", "miller", "netmaker", "wright", "fletcher",
              "jeweler", "baker", "tattoo"),
    "learned": ("scribe", "clerk", "sage", "scholar", "magistrate", "steward",
                "assessor", "map-maker", "cartographer", "alchemist", "herbalist",
                "physician", "librarian", "wizard", "apprentice"),
    "underworld": ("smuggler", "fence", "thief", "cutpurse", "pawnbroker",
                   "beggar-king", "den", "rat-catcher", "informer", "assassin"),
    "rustic": ("farmer", "shepherd", "reeve", "fisher", "ferryman", "drover",
               "woodcutter", "trapper", "forester", "cottar", "swineherd"),
}


def family_for(role: str) -> str:
    """Which venture family a trade belongs to. Unknown trades get `common` —
    the point is that ANY named person may turn out to want something."""
    slug = slugify(role or "")
    words = {w for w in slug.split("-") if w}
    if not words:
        return "common"
    for fam, keys in _ROLE_FAMILY.items():
        for k in keys:
            # A key may be a whole word of the trade ("smith" in "blacksmith")
            # or a hyphenated trade in its own right ("rat-catcher"), which no
            # single word will ever match.
            if k in words or any(k in w for w in words) or ("-" in k and k in slug):
                return fam
    return "common"


# ---------------------------------------------------------------------------
# Reading a venture
# ---------------------------------------------------------------------------

def is_venture(quest: Entity | dict | None) -> bool:
    attrs = (quest if isinstance(quest, dict)
             else (getattr(quest, "attributes", None) or {}))
    return str(attrs.get("tier", "")) == QuestTier.VENTURE


def _live(attrs: dict) -> bool:
    return str(attrs.get("state", QuestState.ACTIVE)) in (
        QuestState.OFFERED, QuestState.ACTIVE)


def effective_dc(graph: WorldGraph, npc_ref, stage: dict) -> int:
    """What this step actually costs right now.

    The stored `dc` is what the world asks; opposition is added on top and NOT
    written back, because it must lift the bar only while somebody is actually
    set against them and drop the moment they relent. A setback, which IS
    permanent, is baked into the stage — the two are different kinds of fact
    and are deliberately stored differently.
    """
    dc = int(stage.get("dc", 12))
    return dc + (OPPOSED_DC if opponents(graph, npc_ref) else 0)


def setback_limit(attrs: dict) -> int:
    """How much can go wrong before the whole thing is off. Scales with depth,
    so a long venture has room to stumble and a short one does not."""
    depth = int(attrs.get("depth") or len(attrs.get("stages") or []) or 1)
    return max(1, depth + SETBACK_GRACE)


def current_stage(attrs: dict) -> Optional[dict]:
    stages = list(attrs.get("stages") or [])
    i = int(attrs.get("stage", 0))
    return stages[i] if 0 <= i < len(stages) else None


def venture_of(graph: WorldGraph, npc_ref) -> Optional[Entity]:
    """The live venture an NPC is pursuing, if any."""
    with Session(graph.engine) as s:
        npc = graph._resolve_entity(s, npc_ref)
        if npc is None:
            return None
        for r in s.exec(select(Relation).where(
                Relation.src_id == npc.id,
                Relation.rel_type == RelationType.PURSUES,
                Relation.valid_to == None)).all():  # noqa: E711
            q = s.get(Entity, r.dst_id)
            if q is not None and _live(q.attributes or {}):
                return q
    return None


def accompanying(graph: WorldGraph, pc_ref) -> list[dict]:
    """Ventures this PC is currently travelling on. Each row carries the NPC,
    the venture entity and its current stage — everything a caller needs to
    render the situation without going back to the graph."""
    out: list[dict] = []
    with Session(graph.engine) as s:
        pc = graph._resolve_entity(s, pc_ref)
        if pc is None:
            return out
        rels = s.exec(select(Relation).where(
            Relation.src_id == pc.id,
            Relation.rel_type == RelationType.ACCOMPANIES,
            Relation.valid_to == None)).all()  # noqa: E711
        for r in rels:
            npc = s.get(Entity, r.dst_id)
            if npc is None:
                continue
            quest = None
            for qr in s.exec(select(Relation).where(
                    Relation.src_id == npc.id,
                    Relation.rel_type == RelationType.PURSUES,
                    Relation.valid_to == None)).all():  # noqa: E711
                q = s.get(Entity, qr.dst_id)
                if q is not None and _live(q.attributes or {}):
                    quest = q
                    break
            attrs = dict((quest.attributes or {}) if quest is not None else {})
            out.append({
                "npc": npc, "npc_slug": npc.slug, "npc_name": npc.name,
                "quest": quest,
                "goal": attrs.get("goal", ""),
                "stage": current_stage(attrs),
                "stage_no": int(attrs.get("stage", 0)) + 1,
                "stages": len(attrs.get("stages") or []),
                "setbacks": int(attrs.get("setbacks", 0)),
                "since_day": (r.attributes or {}).get("joined_day"),
            })
    return out


def _pcs_related(graph: WorldGraph, npc_ref, rel_type: str) -> list[tuple[Entity, dict]]:
    """The PCs holding `rel_type` toward this NPC, with the edge's own record."""
    with Session(graph.engine) as s:
        npc = graph._resolve_entity(s, npc_ref)
        if npc is None:
            return []
        rels = s.exec(select(Relation).where(
            Relation.dst_id == npc.id,
            Relation.rel_type == rel_type,
            Relation.valid_to == None)).all()  # noqa: E711
        out: list[tuple[Entity, dict]] = []
        for r in rels:
            e = s.get(Entity, r.src_id)
            if e is not None:
                out.append((e, dict(r.attributes or {})))
        return out


def followers(graph: WorldGraph, npc_ref) -> list[Entity]:
    """The PCs currently travelling on this NPC's venture."""
    return [e for e, _ in _pcs_related(graph, npc_ref, RelationType.ACCOMPANIES)]


def opponents(graph: WorldGraph, npc_ref) -> list[tuple[Entity, dict]]:
    """The PCs currently set against this NPC's venture, and how openly.

    Returned with the edge attributes because `covert` is the whole difference
    between a rival and a rumour: an open enemy is somebody the venturer can
    name, and a covert one is bad luck until they work it out.
    """
    return _pcs_related(graph, npc_ref, RelationType.OPPOSES)


def opposing(graph: WorldGraph, pc_ref) -> list[dict]:
    """Ventures this PC is working against. The mirror of `accompanying`."""
    out: list[dict] = []
    with Session(graph.engine) as s:
        pc = graph._resolve_entity(s, pc_ref)
        if pc is None:
            return out
        rels = s.exec(select(Relation).where(
            Relation.src_id == pc.id,
            Relation.rel_type == RelationType.OPPOSES,
            Relation.valid_to == None)).all()  # noqa: E711
        for r in rels:
            npc = s.get(Entity, r.dst_id)
            if npc is None:
                continue
            quest = None
            for qr in s.exec(select(Relation).where(
                    Relation.src_id == npc.id,
                    Relation.rel_type == RelationType.PURSUES,
                    Relation.valid_to == None)).all():  # noqa: E711
                q = s.get(Entity, qr.dst_id)
                if q is not None and _live(q.attributes or {}):
                    quest = q
                    break
            attrs = dict((quest.attributes or {}) if quest is not None else {})
            edge = dict(r.attributes or {})
            out.append({
                "npc": npc, "npc_slug": npc.slug, "npc_name": npc.name,
                "quest": quest,
                "goal": attrs.get("goal", ""),
                "stage": current_stage(attrs),
                "stage_no": int(attrs.get("stage", 0)) + 1,
                "stages": len(attrs.get("stages") or []),
                "covert": bool(edge.get("covert")),
                "reason": edge.get("reason", ""),
                "acts": int(attrs.get("hindrances", 0)),
            })
    return out


def live_ventures(graph: WorldGraph) -> list[tuple[Entity, Entity]]:
    """Every (npc, venture) pair underway in the world."""
    out: list[tuple[Entity, Entity]] = []
    with Session(graph.engine) as s:
        for r in s.exec(select(Relation).where(
                Relation.rel_type == RelationType.PURSUES,
                Relation.valid_to == None)).all():  # noqa: E711
            npc, q = s.get(Entity, r.src_id), s.get(Entity, r.dst_id)
            if npc is None or q is None or not _live(q.attributes or {}):
                continue
            out.append((npc, q))
    return out


# ---------------------------------------------------------------------------
# Birth
# ---------------------------------------------------------------------------

def _settlement_and_away(graph: WorldGraph, npc: Entity) -> tuple[Optional[Entity],
                                                                 Optional[Entity]]:
    """Where this person lives, and somewhere out there worth going.

    `away` prefers unexplored or dangerous country over the next tidy village:
    a venture that ends in the neighbouring market square is an errand.
    """
    from . import census
    home = graph.location_of(npc.slug)
    settle = census.settlement_of(graph, home.slug) if home is not None else None
    anchor = settle or home
    if anchor is None:
        return None, None
    with Session(graph.engine) as s:
        a = graph._resolve_entity(s, anchor.slug)
        if a is None:
            return home, None
        ids: set[int] = set()
        for r in s.exec(select(Relation).where(
                Relation.rel_type == RelationType.ADJACENT_TO,
                Relation.valid_to == None)).all():  # noqa: E711
            if r.src_id == a.id:
                ids.add(r.dst_id)
            elif r.dst_id == a.id:
                ids.add(r.src_id)
        cands = [e for e in (s.get(Entity, i) for i in ids)
                 if e is not None and e.type == EntityType.PLACE]
    if not cands:
        return home, None

    def weight(p: Entity) -> tuple:
        at = p.attributes or {}
        wild = (p.status == "unexplored" or at.get("stub")
                or str(at.get("danger", "")).lower() in ("moderate", "high", "deadly"))
        return (0 if wild else 1, p.slug)

    return home, sorted(cands, key=weight)[0]


def _competence(npc: Entity, rng: random.Random) -> int:
    """How good this person actually is at the thing they have set out to do.

    Level when the sheet has one; otherwise a modest baseline plus a
    deterministic streak of grit, so the same shepherd is the same shepherd
    every time the pass runs and one of them is simply better than the others.
    """
    attrs = npc.attributes or {}
    try:
        lvl = int(attrs.get("level") or 0)
    except (TypeError, ValueError):
        lvl = 0
    base = 2 + min(5, max(0, lvl - 1))
    return base + rng.choice([-1, 0, 0, 1, 2])


def _build_stages(arch: dict, depth: int, home: str, away: str) -> list[dict]:
    """`depth` stages ending in the climax, DCs rising as it goes."""
    steps = [t for t in (arch.get("steps") or [])][:max(0, depth - 1)]
    texts = steps + [arch["climax"]]
    hard = int(arch.get("hard", 0))
    out: list[dict] = []
    for i, t in enumerate(texts):
        out.append({
            "text": t.format(home=home, away=away),
            # The climax is always the hard one; the run-up gets easier the
            # longer it is, so a 3-stage venture isn't three climaxes.
            "dc": 12 + 2 * i + (hard if i == len(texts) - 1 else 0),
            "state": "open",
        })
    return out


def _venture_title(npc_name: str, arch: dict) -> str:
    """Quest entities are addressed by name, so a venture needs one that reads
    like a thread in a journal and cannot collide with another NPC's."""
    goal = arch["goal"]
    short = goal.split(",")[0].split(" before ")[0].split(" that ")[0]
    short = short.strip().rstrip(".")
    if len(short) > 60:
        short = short[:57].rsplit(" ", 1)[0] + "..."
    return f"{npc_name}: {short}"


def open_venture(graph: WorldGraph, npc_ref, *, depth: Optional[int] = None,
                 goal: Optional[str] = None, seed: Optional[str] = None,
                 session_id: Optional[str] = None) -> Optional[dict]:
    """Give an NPC a quest of their own. Returns a summary, or None if they
    already have one (nobody runs two errands at once) or cannot have one."""
    npc = graph.get_entity(npc_ref)
    if npc is None or npc.type != EntityType.NPC or npc.status != "active":
        return None
    if venture_of(graph, npc.slug) is not None:
        return None

    today = graph.current_day()
    rng = random.Random(seed or f"venture:{npc.slug}:{today}")
    attrs = npc.attributes or {}
    role = str(attrs.get("role") or npc.subtype or "")
    fam = family_for(role)
    arch = dict(rng.choice(_FAMILIES[fam]))
    if goal:
        arch["goal"] = goal

    home, away = _settlement_and_away(graph, npc)
    home_name = home.name if home is not None else "home"
    away_name = away.name if away is not None else "the country beyond"

    if depth is None:
        depth = rng.choices((1, 2, 3), weights=DEPTH_WEIGHTS)[0]
    depth = max(1, min(3, int(depth)))
    stages = _build_stages(arch, depth, home_name, away_name)

    title = _venture_title(npc.name, arch)
    # create_entity, never upsert: a person may set out twice in a long life and
    # the second time is a NEW thread. Upserting by slug would reopen the
    # finished one — the record of what they did last year overwritten by what
    # they are doing now, and a resolved venture back to active.
    quest = graph.create_entity(
        title, EntityType.QUEST,
        attributes={
            "tier": QuestTier.VENTURE,
            "state": QuestState.ACTIVE,
            "owner": npc.slug,
            "owner_name": npc.name,
            "goal": arch["goal"].format(home=home_name, away=away_name),
            "family": fam,
            "outcome_kind": arch.get("outcome", "standing"),
            "stages": stages,
            "stage": 0,
            "setbacks": 0,
            "depth": depth,
            "opened_day": today,
            "last_step_day": today,
            "last_touched_day": today,
            "home_slug": home.slug if home is not None else None,
            "away_slug": away.slug if away is not None else None,
            "location_slug": (away.slug if (arch.get("away") and away is not None)
                              else (home.slug if home is not None else None)),
        },
        tags=["quest", "venture", fam],
    )
    graph.add_relation(npc, RelationType.PURSUES, quest)
    # INVOLVES is what pulls the venture into the DM's world slice when its
    # owner is nearby, and what makes entropy refuse to quietly kill them.
    graph.add_relation(quest, RelationType.INVOLVES, npc)
    if home is not None:
        graph.add_relation(quest, RelationType.LOCATED_AT, home)

    graph.add_event(
        f"{npc.name}" + (f", the {role}," if role else "")
        + f" set out to {quest.attributes['goal']}.",
        location=home.slug if home is not None else None,
        involved=[npc.slug, quest.slug], session_id=session_id)
    return _summary(npc, quest)


def _candidates(graph: WorldGraph, today: int) -> list[Entity]:
    """Who might plausibly step out of their role this pass.

    The filter that matters is the last one: somebody has to be able to HEAR
    about it. A venture by a stranger in a town no player has entered is a
    dice roll nobody will ever see, so it is not rolled at all.
    """
    with Session(graph.engine) as s:
        npcs = list(s.exec(select(Entity).where(
            Entity.type == EntityType.NPC, Entity.status == "active")).all())
        busy: set[int] = set()
        for r in s.exec(select(Relation).where(
                Relation.rel_type.in_([RelationType.PURSUES,   # type: ignore[attr-defined]
                                       RelationType.TRAVELS_WITH]),
                Relation.valid_to == None)).all():  # noqa: E711
            busy.add(r.src_id)
        pc_ids = {e.id for e in s.exec(
            select(Entity).where(Entity.type == EntityType.PC)).all()}
        known: set[int] = set()
        for r in s.exec(select(Relation).where(
                Relation.rel_type == RelationType.KNOWS,
                Relation.valid_to == None)).all():  # noqa: E711
            if r.dst_id in pc_ids:
                known.add(r.src_id)
        # Where each NPC lives, and whether the party has ever been there.
        loc: dict[int, int] = {}
        for r in s.exec(select(Relation).where(
                Relation.rel_type == RelationType.LOCATED_IN,
                Relation.valid_to == None)).all():  # noqa: E711
            loc.setdefault(r.src_id, r.dst_id)
        visited: set[int] = set()
        for p in s.exec(select(Entity).where(Entity.type == EntityType.PLACE)).all():
            if (p.attributes or {}).get("last_visited_day") is not None:
                visited.add(p.id)
        # A venue inside a visited settlement counts as visited too.
        for r in s.exec(select(Relation).where(
                Relation.rel_type == RelationType.PART_OF,
                Relation.valid_to == None)).all():  # noqa: E711
            if r.dst_id in visited:
                visited.add(r.src_id)

        out: list[Entity] = []
        for npc in npcs:
            if npc.id in busy:
                continue
            attrs = npc.attributes or {}
            if not attrs.get("role"):
                continue    # a person with no trade has nothing to step out of
            last = attrs.get("last_venture_day")
            if last is not None and today - int(last) < COOLDOWN_DAYS:
                continue    # they only just got back
            if npc.id in known or loc.get(npc.id) in visited:
                out.append(npc)
        return out


def spawn_ventures(graph: WorldGraph, today: int, *,
                   limit: int = NEW_PER_PASS,
                   session_id: Optional[str] = None) -> list[dict]:
    """Somebody, somewhere, decides this is the year. Runs on the entropy
    cadence; rationed so the journal never fills with other people's errands."""
    live = live_ventures(graph)
    room = max(0, MAX_LIVE - len(live))
    if room <= 0:
        return []
    cands = _candidates(graph, today)
    if not cands:
        return []
    era = today // max(1, STEP_DAYS)
    rng = random.Random(f"ventures:{era}")
    if rng.random() > BIRTH_CHANCE:
        return []
    rng.shuffle(cands)
    born: list[dict] = []
    for npc in cands:
        if len(born) >= min(limit, room):
            break
        got = open_venture(graph, npc.slug, seed=f"venture:{npc.slug}:{era}",
                           session_id=session_id)
        if got:
            born.append(got)
    return born


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

def _summary(npc: Entity, quest: Entity) -> dict:
    a = quest.attributes or {}
    st = current_stage(a)
    return {
        "npc": npc.name, "npc_slug": npc.slug,
        "quest": quest.name, "slug": quest.slug,
        "goal": a.get("goal", ""), "state": a.get("state"),
        "stage_no": int(a.get("stage", 0)) + 1,
        "stages": len(a.get("stages") or []),
        "stage_text": (st or {}).get("text", ""),
        "setbacks": int(a.get("setbacks", 0)),
        "outcome_kind": a.get("outcome_kind"),
    }


def _patch(graph: WorldGraph, quest: Entity, patch: dict) -> Entity:
    return graph.upsert_entity(quest.name, EntityType.QUEST, slug=quest.slug,
                               status=quest.status, attributes=patch,
                               tags=quest.tags or ["quest", "venture"])


def step_venture(graph: WorldGraph, npc_ref, outcome: str, *,
                 note: str = "", session_id: Optional[str] = None) -> Optional[dict]:
    """Resolve the CURRENT stage one way or the other.

    This is the one place a stage moves, whether the roll came from the offline
    pass or from the DM at a table where the party is standing right there —
    so an accompanied venture and a neglected one can never advance by
    different rules.
    """
    npc = graph.get_entity(npc_ref)
    quest = venture_of(graph, npc_ref) if npc is not None else None
    if npc is None or quest is None:
        return None
    a = dict(quest.attributes or {})
    stages = [dict(x) for x in (a.get("stages") or [])]
    i = int(a.get("stage", 0))
    if not (0 <= i < len(stages)):
        return None
    today = graph.current_day()
    good = str(outcome).lower().startswith(("s", "win", "y"))
    text = stages[i].get("text", "")

    if good:
        stages[i]["state"] = "done"
        i += 1
        patch = {"stages": stages, "stage": i, "last_step_day": today,
                 "last_touched_day": today}
        graph.add_event(f"[{npc.name}] {note or text} — done.",
                        location=a.get("location_slug"),
                        involved=[npc.slug, quest.slug], session_id=session_id)
        if i >= len(stages):
            _patch(graph, quest, patch)
            return resolve_venture(graph, npc.slug, True, note=note,
                                   session_id=session_id)
        # The venturer moves toward the thing they are going to do next.
        _advance_location(graph, npc, a, i, len(stages))
        _patch(graph, quest, patch)
    else:
        setbacks = int(a.get("setbacks", 0)) + 1
        # The next try at this step is harder: the surprise is spent and
        # whatever stands in the way now knows somebody is coming.
        stages[i]["dc"] = int(stages[i].get("dc", 12)) + SETBACK_DC_STEP
        patch = {"stages": stages, "setbacks": setbacks, "last_step_day": today,
                 "last_touched_day": today}
        graph.add_event(f"[{npc.name}] {note or text} — it went badly.",
                        location=a.get("location_slug"),
                        involved=[npc.slug, quest.slug], session_id=session_id)
        if setbacks >= setback_limit(a):
            _patch(graph, quest, patch)
            return resolve_venture(graph, npc.slug, False, note=note,
                                   session_id=session_id)
        _patch(graph, quest, patch)

    fresh = graph.get_entity(quest.slug)
    return _summary(npc, fresh if fresh is not None else quest)


def _advance_location(graph: WorldGraph, npc: Entity, attrs: dict,
                      stage_i: int, total: int) -> None:
    """Move the venturer to where the next stage happens.

    Preparatory stages happen at home; the climax is where the venture was
    always going. A party ACCOMPANYING them is not dragged along by the graph —
    the table's own movement is the players', and the DM narrates the road.
    """
    away = attrs.get("away_slug")
    home = attrs.get("home_slug")
    target = away if (stage_i == total - 1 and away) else home
    if not target:
        return
    here = graph.location_of(npc.slug)
    if here is not None and here.slug == target:
        return
    try:
        graph.move_entity(npc.slug, target)
    except Exception as e:  # noqa: BLE001 — a venture must never break the clock
        print(f"[ventures] move failed for {npc.slug}: {e}")


def advance_ventures(graph: WorldGraph, today: int, *,
                     session_id: Optional[str] = None) -> dict:
    """The half nobody watches: every unaccompanied venture whose stage clock is
    due rolls its owner's competence against the stage and moves.

    An ACCOMPANIED venture is skipped and its clock stamped forward, because the
    party is standing in it — resolving it behind their backs would make walking
    alongside somebody the one way to stop mattering to their story.
    """
    out = {"stepped": 0, "advanced": 0, "setbacks": 0,
           "completed": 0, "failed": 0, "born": 0}
    for npc, quest in live_ventures(graph):
        a = dict(quest.attributes or {})
        last = int(a.get("last_step_day", a.get("opened_day", today)))
        if today - last < STEP_DAYS:
            continue
        if followers(graph, npc.slug):
            _patch(graph, quest, {"last_step_day": today})
            continue
        stage = current_stage(a)
        if stage is None:
            continue
        era = today // max(1, STEP_DAYS)
        rng = random.Random(f"venture-step:{quest.slug}:{era}")
        roll = rng.randint(1, 20) + _competence(npc, rng)
        good = roll >= effective_dc(graph, npc.slug, stage)
        res = step_venture(graph, npc.slug, "success" if good else "failure",
                           note="", session_id=session_id)
        out["stepped"] += 1
        out["advanced" if good else "setbacks"] += 1
        if res and res.get("state") == QuestState.COMPLETED:
            out["completed"] += 1
        elif res and res.get("state") == QuestState.FAILED:
            out["failed"] += 1
    return out


def run_if_due(graph: WorldGraph, today: int, *,
               session_id: Optional[str] = None) -> dict:
    """Both halves in the order they have to happen: move what is underway
    first, then let the room made by a finished venture be taken by a new one."""
    out = advance_ventures(graph, today, session_id=session_id)
    out["born"] = len(spawn_ventures(graph, today, session_id=session_id))
    return out


# ---------------------------------------------------------------------------
# Landing — what a finished venture does to the world
# ---------------------------------------------------------------------------

# A promotion and its opposite, per family. A venture that was ABOUT the NPC's
# standing actually moves it: the role string is what the census, the shop
# roller and the DM prompt all read, so changing it changes who this person is
# everywhere at once.
_PROMOTION = {
    "martial": ("captain of the watch", "disgraced guard"),
    "devout": ("high priest", "defrocked priest"),
    "mercantile": ("guild merchant", "ruined trader"),
    "craft": ("master of the guild", "journeyman again"),
    "learned": ("magistrate", "struck-off clerk"),
    "underworld": ("crime boss", "marked thief"),
    "rustic": ("village elder", "landless labourer"),
    "common": ("person of standing", "outcast"),
}

# What a `relic` venture puts in somebody's hands. Deliberately vague on
# mechanics — this is a world ITEM entity for the fiction to hang on, not a
# rules row; a party that wants it takes it up with its owner.
_RELICS = {
    "devout": ("the {home} Reliquary", "A relic carried home from far away."),
    "craft": ("the {npc} Piece", "The one thing its maker will be remembered for."),
    "learned": ("the {away} Inscription", "A copy of writing nobody else can read."),
    "underworld": ("the {away} Take", "Something valuable, taken from somewhere careful."),
    "common": ("the {away} Find", "Brought back from out there, and worth something."),
}


def _apply_outcome(graph: WorldGraph, npc: Entity, quest: Entity, success: bool,
                   *, session_id: Optional[str] = None) -> list[str]:
    """The world moves. Everything here goes through a primitive that already
    exists, so a venture can never mutate the world in a way nothing else can."""
    from . import census, entropy
    a = dict(quest.attributes or {})
    kind = str(a.get("outcome_kind") or "standing")
    fam = str(a.get("family") or "common")
    home_slug, away_slug = a.get("home_slug"), a.get("away_slug")
    loc = a.get("location_slug") or home_slug
    notes: list[str] = []
    today = graph.current_day()

    if kind == "safer":
        step = -1 if success else +1
        if loc:
            entropy.shift_place_danger(
                graph, loc, step,
                (f"{npc.name} put an end to it" if success
                 else f"{npc.name} tried, and did not"), session_id=session_id)
            notes.append("safer" if success else "worse")

    elif kind == "standing":
        up, down = _PROMOTION.get(fam, _PROMOTION["common"])
        new_role = up if success else down
        graph.upsert_entity(
            npc.name, npc.type, slug=npc.slug, status=npc.status,
            attributes={"role": new_role,
                        "description": ((npc.attributes or {}).get("description") or "")
                        + (f" Rose to {new_role} after {quest.name}." if success
                           else f" Broken by {quest.name}.")})
        notes.append(f"now {new_role}")

    elif kind == "wealth":
        attrs = dict(npc.attributes or {})
        purse = int(attrs.get("wealth", 0) or 0) + (1 if success else -1)
        graph.upsert_entity(npc.name, npc.type, slug=npc.slug, status=npc.status,
                            attributes={"wealth": max(-2, min(3, purse))})
        # A fortune made or lost is felt by the town, not just the person.
        if home_slug:
            home = graph.get_entity(home_slug)
            settle = census.settlement_of(graph, home_slug) if home is not None else None
            if settle is not None and (settle.attributes or {}).get("population"):
                pop = int(settle.attributes["population"])
                graph.upsert_entity(
                    settle.name, settle.type, slug=settle.slug, status=settle.status,
                    attributes={"population": max(10, int(pop * (1.02 if success
                                                                else 0.99)))})
        notes.append("richer" if success else "ruined")

    elif kind == "relic":
        if success:
            tmpl, desc = _RELICS.get(fam, _RELICS["common"])
            home = graph.get_entity(home_slug) if home_slug else None
            away = graph.get_entity(away_slug) if away_slug else None
            name = tmpl.format(npc=npc.name,
                               home=home.name if home is not None else "Old",
                               away=away.name if away is not None else "Far")
            item = graph.upsert_entity(name, EntityType.ITEM,
                                       attributes={"description": desc,
                                                   "rarity": "uncommon",
                                                   "won_by": npc.slug,
                                                   "won_day": today},
                                       tags=["item", "venture-prize"])
            graph.add_relation(npc, RelationType.OWNS, item)
            notes.append(f"holds {item.name}")
        else:
            if loc:
                entropy.shift_place_danger(
                    graph, loc, +1, f"whatever {npc.name} disturbed is still there",
                    session_id=session_id)
            notes.append("came back empty-handed")

    elif kind == "peril":
        if success:
            notes.append("came home")
        else:
            # The one outcome that costs a life. It routes through the same
            # claim the failed-quest peril path uses, so a companion and a PC
            # are never culled and the death is logged like any other.
            lost = entropy.claim_peril_npc(graph, quest.name, npc.slug,
                                           session_id=session_id)
            if lost:
                notes.append("did not come back")
                dead = graph.get_entity(npc.slug)
                if dead is not None:
                    try:
                        heir = census.spawn_successor(graph, dead)
                        # They died where the venture took them, which is the
                        # truth; their SUCCESSOR takes the post back home. Left
                        # to spawn_successor's own lookup the heir is born in
                        # the wilds the predecessor never came back from.
                        if heir is not None and home_slug:
                            graph.move_entity(heir.slug, home_slug)
                    except Exception as e:  # noqa: BLE001
                        print(f"[ventures] succession failed: {e}")
            else:
                notes.append("barely came home")

    # A venture that ended badly out in the country leaves the country worse,
    # whatever it was about — somebody went out there and stirred something.
    if not success and kind not in ("safer", "relic") and away_slug and \
            a.get("location_slug") == away_slug:
        entropy.shift_place_danger(graph, away_slug, +1,
                                   f"what {npc.name} left unfinished",
                                   session_id=session_id)
    return notes


def resolve_venture(graph: WorldGraph, npc_ref, success: bool, *, note: str = "",
                    session_id: Optional[str] = None) -> Optional[dict]:
    """End a venture and let it leave its mark. Anyone who walked it with them
    ends the journey here too — with the standing they earned by being there."""
    npc = graph.get_entity(npc_ref)
    quest = venture_of(graph, npc_ref) if npc is not None else None
    if npc is None or quest is None:
        return None
    state = QuestState.COMPLETED if success else QuestState.FAILED
    walked = followers(graph, npc.slug)
    # Settled BEFORE the companions are released: whether an act was betrayal
    # rather than mere rivalry is a question about who was walking beside them
    # at the time, and closing those edges first would lose the answer.
    exposed = _settle_opposition(graph, npc, quest, success, session_id=session_id)

    marks = _apply_outcome(graph, npc, quest, success, session_id=session_id)
    quest = _patch(graph, quest, {"state": state, "resolved_day": graph.current_day(),
                                  "marks": marks})
    graph.close_relation(npc.slug, RelationType.PURSUES, quest.slug)

    # The errand is over, so they go home. Without this a venturer who walked
    # out to the climax simply STAYS in the wilds for the rest of the world's
    # life: the town loses its smith, the census keeps a post nobody fills, and
    # the pool of people who could ever set out again drains to nothing.
    home = (quest.attributes or {}).get("home_slug")
    alive = graph.get_entity(npc.slug)
    if home and alive is not None and alive.status == "active":
        here = graph.location_of(npc.slug)
        if here is None or here.slug != home:
            try:
                graph.move_entity(npc.slug, home)
            except Exception as e:  # noqa: BLE001
                print(f"[ventures] homecoming failed for {npc.slug}: {e}")
    if alive is not None and alive.status == "active":
        # One errand at a time, and a season between them — otherwise the same
        # three villagers are permanently on the road and nobody else ever is.
        graph.upsert_entity(alive.name, alive.type, slug=alive.slug,
                            status=alive.status,
                            attributes={"last_venture_day": graph.current_day()})

    for pc in walked:
        graph.close_relation(pc.slug, RelationType.ACCOMPANIES, npc.slug)
        # An NPC who is dead has no opinion left to hold — and one who has just
        # worked out that this companion was the saboteur is not about to
        # thank them for the company. The deed ledger has that stance now.
        if pc.name in exposed:
            continue
        if graph.get_entity(npc.slug) is not None and \
                graph.get_entity(npc.slug).status == "active":
            try:
                graph.adjust_trust(npc.slug, pc.slug,
                                   TRUST_ON_SUCCESS if success else TRUST_ON_FAILURE,
                                   reason=(f"stood with them through {quest.name}"
                                           if success else
                                           f"was there when {quest.name} came apart"))
            except Exception as e:  # noqa: BLE001
                print(f"[ventures] trust failed: {e}")

    tail = f" — {note}" if note else ""
    mark = f" ({'; '.join(marks)})" if marks else ""
    graph.add_event(
        (f"{npc.name} finished what they set out to do: {quest.attributes.get('goal', '')}"
         if success else
         f"{npc.name} failed: {quest.attributes.get('goal', '')}") + tail + mark,
        location=quest.attributes.get("location_slug"),
        involved=[npc.slug, quest.slug], session_id=session_id)
    return {**_summary(npc, quest), "state": state, "marks": marks,
            "walked_with": [p.name for p in walked], "exposed": exposed}


def abandon_venture(graph: WorldGraph, npc_ref, *, reason: str = "",
                    session_id: Optional[str] = None) -> bool:
    """Give it up without resolving it — the NPC thought better of it. No world
    mutation: nothing happened, which is the point."""
    npc = graph.get_entity(npc_ref)
    quest = venture_of(graph, npc_ref) if npc is not None else None
    if npc is None or quest is None:
        return False
    _patch(graph, quest, {"state": QuestState.FAILED, "abandoned": True,
                          "resolved_day": graph.current_day()})
    graph.close_relation(npc.slug, RelationType.PURSUES, quest.slug)
    for pc in followers(graph, npc.slug):
        graph.close_relation(pc.slug, RelationType.ACCOMPANIES, npc.slug)
    # Nobody is exposed by a venture that was never attempted — giving it up
    # is the one ending that leaves no trail back to whoever wanted it given up.
    for pc, _edge in opponents(graph, npc.slug):
        graph.close_relation(pc.slug, RelationType.OPPOSES, npc.slug)
    home = (quest.attributes or {}).get("home_slug")
    if home:
        try:
            graph.move_entity(npc.slug, home)
        except Exception as e:  # noqa: BLE001
            print(f"[ventures] homecoming failed for {npc.slug}: {e}")
    graph.upsert_entity(npc.name, npc.type, slug=npc.slug, status=npc.status,
                        attributes={"last_venture_day": graph.current_day()})
    graph.add_event(f"{npc.name} gave up on {quest.name}"
                    + (f" — {reason}" if reason else "."),
                    involved=[npc.slug], session_id=session_id)
    return True


# ---------------------------------------------------------------------------
# Walking with them
# ---------------------------------------------------------------------------

def accompany(graph: WorldGraph, pc_ref, npc_ref) -> Optional[dict]:
    """The party throws in with somebody else's errand.

    Not recruitment: no companion relation is opened, the NPC keeps their own
    location and their own venture, and nothing about the party's control
    changes. All this buys is that the venture stops resolving behind their
    backs and starts happening at the table.
    """
    npc = graph.get_entity(npc_ref)
    pc = graph.get_entity(pc_ref)
    if npc is None or pc is None or pc.type != EntityType.PC:
        return None
    quest = venture_of(graph, npc.slug)
    if quest is None:
        return None
    graph.add_relation(pc, RelationType.ACCOMPANIES, npc,
                       attributes={"joined_day": graph.current_day(),
                                   "venture": quest.slug})
    # Stamp the clock so joining never lands an overdue offline roll on the
    # first turn the party is standing there.
    _patch(graph, quest, {"last_step_day": graph.current_day()})
    graph.add_event(f"{pc.name} set out with {npc.name}.",
                    involved=[pc.slug, npc.slug])
    return _summary(npc, quest)


def oppose(graph: WorldGraph, pc_ref, npc_ref, *, reason: str = "",
           covert: bool = False, session_id: Optional[str] = None) -> Optional[dict]:
    """The party sets itself against somebody else's errand.

    Deliberately NOT the mirror of accompanying in one respect: an opposed
    venture keeps rolling on the world clock, harder. Sabotage is a thing you
    do and then walk away from, and a declared enemy who has to stand there
    watching to matter is not an enemy, it is an escort.

    Opposing while ALSO accompanying is allowed and is not a bug — it is the
    saboteur inside the camp. Presence still pauses the offline roll; the
    opposition still lifts the DC of every step settled at the table.
    """
    npc = graph.get_entity(npc_ref)
    pc = graph.get_entity(pc_ref)
    if npc is None or pc is None or pc.type != EntityType.PC:
        return None
    quest = venture_of(graph, npc.slug)
    if quest is None:
        return None
    graph.add_relation(pc, RelationType.OPPOSES, npc,
                       attributes={"since_day": graph.current_day(),
                                   "venture": quest.slug, "covert": bool(covert),
                                   "reason": reason})
    # An OPEN enemy is a fact about the world the moment it is declared; a
    # covert one leaves no event, or the log itself gives the game away.
    if not covert:
        graph.add_event(f"{pc.name} set themselves against {npc.name}"
                        + (f" — {reason}" if reason else "."),
                        involved=[pc.slug, npc.slug], session_id=session_id)
    return {**_summary(npc, quest), "covert": bool(covert)}


def relent(graph: WorldGraph, pc_ref, npc_ref, *,
           session_id: Optional[str] = None) -> bool:
    """Stop working against them. The venture gets its own DC back."""
    npc = graph.get_entity(npc_ref)
    pc = graph.get_entity(pc_ref)
    if npc is None or pc is None:
        return False
    covert = any(e.slug == pc.slug and edge.get("covert")
                 for e, edge in opponents(graph, npc.slug))
    if not graph.close_relation(pc.slug, RelationType.OPPOSES, npc.slug):
        return False
    if not covert:
        graph.add_event(f"{pc.name} let {npc.name} be.",
                        involved=[pc.slug, npc.slug], session_id=session_id)
    return True


def hinder_venture(graph: WorldGraph, npc_ref, *, note: str = "", by=None,
                   session_id: Optional[str] = None) -> Optional[dict]:
    """One concrete act of sabotage: it costs the venture a setback.

    This is `step_venture(... failure)` with a name on it. The distinction is
    not cosmetic — a hindrance is COUNTED, and the count is what the venturer
    has to trace back when they come to work out why nothing has gone right.
    """
    quest = venture_of(graph, npc_ref)
    if quest is None:
        return None
    acts = int((quest.attributes or {}).get("hindrances", 0)) + 1
    _patch(graph, quest, {"hindrances": acts})
    if by is not None:
        # An act of sabotage by somebody not yet declared an enemy makes them
        # one — covertly, since a party that wanted it known would have said so.
        pc = graph.get_entity(by)
        if pc is not None and not any(e.slug == pc.slug
                                      for e, _ in opponents(graph, npc_ref)):
            oppose(graph, pc.slug, npc_ref, reason=note, covert=True,
                   session_id=session_id)
    return step_venture(graph, npc_ref, "failure", note=note,
                        session_id=session_id)


def thwart_venture(graph: WorldGraph, npc_ref, *, reason: str = "", by=None,
                   session_id: Optional[str] = None) -> Optional[dict]:
    """The party got there first, and the goal is simply gone.

    Not a roll — a race the venturer lost. The venture resolves FAILED and its
    failure mutates the world exactly as any other failure does: sabotage the
    ranger who was going to make the road safe and the road stays unsafe. That
    the cost lands on somebody else is the point, not an oversight.
    """
    quest = venture_of(graph, npc_ref)
    if quest is None:
        return None
    pc = graph.get_entity(by) if by is not None else None
    acts = int((quest.attributes or {}).get("hindrances", 0)) + 1
    _patch(graph, quest, {"hindrances": acts,
                          "thwarted_by": pc.slug if pc is not None else "unknown",
                          "thwarted_why": reason})
    if pc is not None and not any(e.slug == pc.slug
                                  for e, _ in opponents(graph, npc_ref)):
        oppose(graph, pc.slug, npc_ref, reason=reason, covert=True,
               session_id=session_id)
    return resolve_venture(graph, npc_ref, False, note=reason,
                           session_id=session_id)


def _settle_opposition(graph: WorldGraph, npc: Entity, quest: Entity,
                       success: bool, *,
                       session_id: Optional[str] = None) -> list[str]:
    """When it is over, does the venturer work out who was against them?

    Rolled once, here, rather than on a clock through the operation: the
    question only ever matters at the end, and a covert party that got away
    with it should get away with it cleanly. An OPEN enemy skips the roll —
    they were never hiding — and lands the deed regardless of the outcome,
    because what the party DID is the deed and how it turned out is the story.
    """
    from . import relationships as _rel
    a = quest.attributes or {}
    acts = int(a.get("hindrances", 0))
    found: list[str] = []
    walked = {p.slug for p in followers(graph, npc.slug)}
    for pc, edge in opponents(graph, npc.slug):
        covert = bool(edge.get("covert"))
        if covert:
            chance = min(DISCOVERY_MAX, DISCOVERY_BASE + DISCOVERY_PER_ACT * acts)
            rng = random.Random(f"venture-discovery:{quest.slug}:{pc.slug}")
            if rng.random() > chance:
                graph.close_relation(pc.slug, RelationType.OPPOSES, npc.slug)
                continue    # they never learn whose hand it was
        # Walking beside somebody and working against them at once has its own
        # name, and the deed ledger already knows what it is worth.
        tag = "betrayal" if pc.slug in walked else "theft"
        try:
            _rel.record_deed(
                graph, pc.slug, npc.slug, tag=tag,
                text=(a.get("thwarted_why")
                      or f"worked against {quest.name}")[:120],
                session_id=session_id)
        except Exception as e:  # noqa: BLE001
            print(f"[ventures] deed failed: {e}")
        graph.add_event(
            f"{npc.name} knows who was behind it: {pc.name}."
            + ("" if success else f" {quest.name} was lost because of them."),
            involved=[npc.slug, pc.slug], session_id=session_id)
        graph.close_relation(pc.slug, RelationType.OPPOSES, npc.slug)
        found.append(pc.name)
    return found


def part_ways(graph: WorldGraph, pc_ref, npc_ref, *,
              session_id: Optional[str] = None) -> bool:
    """Stop following. The venture carries on without them, from where it is."""
    npc = graph.get_entity(npc_ref)
    pc = graph.get_entity(pc_ref)
    if npc is None or pc is None:
        return False
    closed = graph.close_relation(pc.slug, RelationType.ACCOMPANIES, npc.slug)
    if not closed:
        return False
    quest = venture_of(graph, npc.slug)
    if quest is not None:
        # The clock restarts from the parting, so the next unwatched roll is a
        # fair interval away rather than instantly overdue.
        _patch(graph, quest, {"last_step_day": graph.current_day()})
    graph.add_event(f"{pc.name} parted from {npc.name}, who went on alone.",
                    involved=[pc.slug, npc.slug], session_id=session_id)
    return True


# ---------------------------------------------------------------------------
# What the DM is told
# ---------------------------------------------------------------------------

def render_block(graph: WorldGraph, entities: list, pc_slug: Optional[str]) -> str:
    """The venture block for the DM prompt.

    Two lists, because they are two different situations. Ventures the party is
    ON are the scene: the DM needs the current step and the fact that the NPC
    leads. Ventures merely NEARBY are colour and an opening — somebody in this
    room is preparing to do something, and the party may ask, help, hinder or
    ignore it.
    """
    with_them = accompanying(graph, pc_slug) if pc_slug else []
    against = opposing(graph, pc_slug) if pc_slug else []
    riding = {row["npc_slug"] for row in with_them} | {r["npc_slug"] for r in against}

    nearby: list[str] = []
    for e in entities or []:
        if getattr(e, "type", None) != EntityType.QUEST:
            continue
        a = getattr(e, "attributes", None) or {}
        if not is_venture(a) or not _live(a):
            continue
        owner = a.get("owner")
        if owner in riding:
            continue
        st = current_stage(a)
        line = f"- **{a.get('owner_name') or owner}** wants to {a.get('goal', '')}"
        if st:
            line += f" — right now: {st.get('text', '')}"
        n = int(a.get("stage", 0)) + 1
        line += f" (step {n} of {len(a.get('stages') or [])}"
        if int(a.get("setbacks", 0)):
            line += f", {a['setbacks']} setback(s) so far"
        line += ")"
        nearby.append(line)

    if not with_them and not against and not nearby:
        return ""

    out = ["# Other people's quests (ventures)"]
    if with_them:
        out.append("The party is TRAVELLING WITH these people. They are NOT "
                   "companions — each leads their own errand, makes their own "
                   "calls, and may refuse the party's plan. Play them with their "
                   "own judgement, and let the party help, hinder or watch.")
        for row in with_them:
            st = row.get("stage") or {}
            out.append(f"- **{row['npc_name']}** — goal: {row['goal']}")
            out.append(f"  · step {row['stage_no']} of {row['stages']}: "
                       f"{st.get('text', '')}"
                       + (f" (setbacks: {row['setbacks']})" if row["setbacks"] else ""))
        out.append("When a step is actually settled in play, say so: "
                   "[[VENTURE: step | <npc> | success|setback | what happened]]. "
                   "That is the ONLY thing that moves it while they are together — "
                   "do not narrate a step done without the hook. If the party "
                   "walks away, [[VENTURE: leave | <npc>]] and it continues "
                   "without them.")
    if against:
        out.append("The party is WORKING AGAINST these people. Every step they "
                   "attempt is harder for it, watched or not. They do not "
                   "automatically know why — play the venturer's own attempts to "
                   "find out, and let them suspect the wrong person.")
        for row in against:
            st = row.get("stage") or {}
            out.append(f"- **{row['npc_name']}** — still trying to {row['goal']}"
                       + (" [the party's hand in this is HIDDEN from them]"
                          if row["covert"] else
                          " [they know the party stands against them]"))
            if st.get("text"):
                out.append(f"  · step {row['stage_no']} of {row['stages']}: "
                           f"{st['text']}"
                           + (f" ({row['acts']} act(s) of interference so far)"
                              if row["acts"] else ""))
        out.append("A concrete act of sabotage is "
                   "[[VENTURE: hinder | <npc> | what the party did]] — it costs "
                   "them a setback and leaves a trail. If the party makes the "
                   "goal outright unreachable (took the prize, won the seat, got "
                   "there first), that is [[VENTURE: thwart | <npc> | how]] and "
                   "it ends. Backing off is [[VENTURE: relent | <npc>]].")
    if nearby:
        out.append("Underway nearby — the party may hear of these, join them "
                   "([[VENTURE: follow | <npc>]]), or leave them to it:")
        out.extend(nearby)
    return "\n".join(out)


def catch_up_lines(graph: WorldGraph, pc_slug: str, since_day: int,
                   limit: int = 4) -> list[str]:
    """What became of people the party once walked with, while they were away.

    Read straight off the resolved venture rows — a player who joined a thread
    and left deserves to learn how it ended without having to go back and ask.
    """
    out: list[str] = []
    with Session(graph.engine) as s:
        pc = graph._resolve_entity(s, pc_slug)
        if pc is None:
            return out
        # Everyone this PC has ever accompanied, open relation or closed.
        npc_ids = {r.dst_id for r in s.exec(select(Relation).where(
            Relation.src_id == pc.id,
            Relation.rel_type == RelationType.ACCOMPANIES)).all()}
        if not npc_ids:
            return out
        for r in s.exec(select(Relation).where(
                Relation.rel_type == RelationType.PURSUES,
                Relation.src_id.in_(npc_ids))).all():  # type: ignore[attr-defined]
            q = s.get(Entity, r.dst_id)
            npc = s.get(Entity, r.src_id)
            if q is None or npc is None:
                continue
            a = q.attributes or {}
            if a.get("state") not in (QuestState.COMPLETED, QuestState.FAILED):
                continue
            if int(a.get("resolved_day", 0)) < int(since_day):
                continue
            verb = ("saw it through" if a["state"] == QuestState.COMPLETED
                    else "did not")
            out.append(f"- {npc.name} {verb}: {a.get('goal', '')}")
    return out[:limit]
