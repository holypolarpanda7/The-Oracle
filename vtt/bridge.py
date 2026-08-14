"""
Board <-> tracker bridge.

``combat/`` already runs fights: it owns initiative, HP, conditions, the action
economy, and a deliberately gridless spacing model ("melee with Gruk", "near",
"far"). The VTT does *not* replace that. It adds exact position underneath it
and keeps both descriptions of the same fight true at once:

* every ``combat_combatant`` gets a token, hydrated with its real size, speed
  and reach;
* after anything moves, each combatant's ``position`` band is recomputed from
  actual distance, so ``combat/engine.py`` keeps validating reach exactly as it
  did before the board existed;
* when the *engine* moves someone by band (a monster's default AI closing to
  melee), the token is walked to a matching square, so the picture doesn't
  drift from the fiction.

The rule of thumb: **the grid is the truth when a board is out, the bands are
the interface**. Nothing in ``combat/`` needs to know the VTT exists.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from . import geometry as geo
from .models import Team, TokenKind, size_squares
from .scene import VttEngine

_MELEE_RE = re.compile(r"melee\s+with\s+(.+)", re.I)

#: Distance bands the gridless engine understands, in feet.
NEAR_FT = 30


def _size_for(monster_row: Any) -> str:
    size = getattr(monster_row, "size", None)
    return str(size).lower() if size else "medium"


def _speed_for(monster_row: Any, default: int = 30) -> int:
    """Walking speed in feet from an SRD monster row's speed blob."""
    raw = getattr(monster_row, "speed", None)
    if isinstance(raw, dict):
        walk = raw.get("walk") or raw.get("Walk")
        if isinstance(walk, str):
            m = re.search(r"(\d+)", walk)
            if m:
                return int(m.group(1))
        if isinstance(walk, int):
            return walk
    if isinstance(raw, str):
        m = re.search(r"(\d+)", raw)
        if m:
            return int(m.group(1))
    return default


def _reach_for(monster_row: Any, size: str) -> int:
    """Large-and-up creatures usually threaten 10 ft; everyone else 5."""
    return 10 if size_squares(size) >= 2 else 5


def roster_for(tracker: Any, encounter_id: int, *,
               rules_lib: Any = None) -> list[tuple[int, int]]:
    """``(size_squares, speed_ft)`` for everyone in a fight — for sizing a board.

    Lives here rather than in ``vtt/`` because a Combatant row carries neither
    a size nor a speed: both come from the stat block, and the bridge is
    already the place that knows how to ask. Without ``rules_lib`` everyone is
    Medium and 30 ft, which is the right answer for a party and a safe floor
    for anything else.
    """
    out: list[tuple[int, int]] = []
    try:
        combatants = tracker.order(encounter_id)
    except Exception:
        return out
    for c in combatants:
        size, speed = "medium", 30
        if c.monster_slug and rules_lib is not None:
            try:
                mon = rules_lib.get_monster(c.monster_slug)
                if mon is not None:
                    size = _size_for(mon)
                    speed = _speed_for(mon)
            except Exception:
                pass
        out.append((size_squares(size), int(speed)))
    return out


def seat_encounter(vtt: VttEngine, map_id: int, encounter_id: int, *,
                   tracker: Any = None, rules_lib: Any = None,
                   portrait_lookup: Any = None) -> list:
    """Give every combatant in a fight a token (idempotent).

    ``rules_lib`` (a ``rules.RulesLibrary``) is used to look up a monster's size
    and speed; ``portrait_lookup(combatant) -> image_id`` supplies token art.
    Both are optional — without them tokens are medium, 30 ft, and drawn as
    coloured discs.
    """
    tracker = tracker or vtt.tracker
    if tracker is None:
        return []
    created = []
    existing = {t.combatant_id for t in vtt.tokens(map_id) if t.combatant_id}
    for c in tracker.order(encounter_id):
        if c.id in existing:
            continue
        is_pc = (c.kind == "pc")
        size, speed, reach = "medium", 30, 5
        if c.monster_slug and rules_lib is not None:
            try:
                mon = rules_lib.get_monster(c.monster_slug)
                if mon is not None:
                    size = _size_for(mon)
                    speed = _speed_for(mon)
                    reach = _reach_for(mon, size)
            except Exception:
                pass
        image_id = None
        if portrait_lookup is not None:
            try:
                image_id = portrait_lookup(c)
            except Exception:
                image_id = None
        tok = vtt.add_token(
            map_id, c.name,
            kind=(TokenKind.PC if is_pc else
                  TokenKind.NPC if c.kind == "npc" else TokenKind.MONSTER),
            # The tracker owns which side a creature is on (a conjured spirit is
            # a monster fighting for the party); reading `kind` here instead
            # would put the board and the combat engine on different answers.
            team=(getattr(c, "side", None)
                  or (Team.PARTY if is_pc else Team.FOE)),
            size=size, speed_ft=speed, reach_ft=reach,
            combatant_id=c.id, character_id=c.character_id,
            monster_slug=c.monster_slug, image_id=image_id,
        )
        if tok:
            created.append(tok)
    if created:
        sync_bands(vtt, map_id, tracker=tracker)
    return created


def sync_bands(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """Rewrite every combatant's spacing band from where its token now stands.

    This is what lets the untouched combat engine keep making correct reach
    calls while the players push miniatures around a grid.
    """
    tracker = tracker or vtt.tracker
    if tracker is None:
        return
    row = vtt.get_scene(map_id)
    if row is None:
        return
    toks = [t for t in vtt.tokens(map_id) if t.combatant_id]
    if not toks:
        return
    fps = {t.id: geo.footprint(t.x, t.y, size_squares(t.size)) for t in toks}
    for t in toks:
        foes = [o for o in toks if o.team != t.team and not o.defeated]
        if not foes:
            band = "near"
        else:
            nearest = min(foes, key=lambda o: geo.token_distance_ft(
                fps[t.id], fps[o.id], row.square_ft,
                dz_ft=vtt.height_gap_ft(row, t, o)))
            d = geo.token_distance_ft(fps[t.id], fps[nearest.id], row.square_ft,
                                      dz_ft=vtt.height_gap_ft(row, t, nearest))
            # "Engaged" means either creature can reach the other.
            engaged = d <= max(t.reach_ft or 5, nearest.reach_ft or 5)
            band = geo.band_for_distance(d, nearest.name if engaged else None)
        try:
            tracker.set_position(t.combatant_id, band)
        except Exception as e:
            print(f"[vtt.bridge] band sync failed for {t.name}: {e}")


def mirror_from_tracker(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """Pull tracker truth onto the board: who is down, who is prone/hidden."""
    tracker = tracker or vtt.tracker
    row = vtt.get_scene(map_id)
    if tracker is None or row is None or not row.encounter_id:
        return
    by_id = {c.id: c for c in tracker.order(row.encounter_id)}
    for t in vtt.tokens(map_id):
        c = by_id.get(t.combatant_id or -1)
        if c is None:
            continue
        conds = {str(x).lower() for x in (c.conditions or [])}
        changes = {}
        if bool(c.defeated) != bool(t.defeated):
            changes["defeated"] = bool(c.defeated)
        prone = "prone" in conds
        if prone != bool(t.prone):
            changes["prone"] = prone
        hidden = "invisible" in conds
        if hidden != bool(t.hidden) and t.team != Team.PARTY:
            changes["hidden"] = hidden
        if changes:
            vtt.update_token(t.id, **changes)


def apply_band_move(vtt: VttEngine, map_id: int, combatant_id: int,
                    band: Optional[str], *, tracker: Any = None) -> Optional[dict]:
    """Walk a token to match a band the *engine* decided ("melee with Gruk").

    Used when a fight advances without anyone touching the board — monster AI,
    or a player who typed "I charge the ogre" instead of clicking a square. The
    token takes a real path (so it can be blocked, and so it triggers the same
    opportunity attacks the engine already resolved through its own model).
    """
    if not band:
        return None
    tok = vtt.token_for_combatant(map_id, combatant_id)
    row = vtt.get_scene(map_id)
    if tok is None or row is None:
        return None
    grid = vtt.grid_of(row)
    band_l = band.strip().lower()

    # HEIGHT, when the square is otherwise as good. The engine thinks in bands
    # and the board turns a band into a square, and that translation used to be
    # decided by flat distance alone — so on a board built entirely out of
    # ledges and terraces, a monster archer would stand in the mud below them
    # forever. The DM prompt tells the LLM to take the high ground; this is the
    # half of the fight the LLM never touches.
    #
    # A step at a time: height only counts in the mover's favour up to one
    # LEDGE, so a terrace two tiers up is never CHOSEN for being high — it has
    # to be taken a tier per turn.
    #
    # That is a limit on the preference, not on the move. A band move is
    # already unbounded in distance (``free=True, enforce_speed=False`` below:
    # the engine charged this in its own coarse economy and the board must not
    # bill it twice), so a monster could always cross the whole board to reach
    # the right band, and it still can. Making band moves pay real feet is a
    # change to the engine's abstraction rather than to this translation, and
    # it is not made here.
    here_ft = vtt.token_height_ft(row, tok)

    def _gain(sq: tuple[int, int]) -> int:
        """Feet of height this square would win, 0 if it is a cliff or a drop."""
        got = int((row.elevation or {}).get(f"{sq[0]},{sq[1]}", 0) or 0) - here_ft
        return got if 0 < got <= 10 else 0

    target_sq = None
    m = _MELEE_RE.match(band_l)
    if m:
        other = vtt.find_token(map_id, m.group(1).strip())
        if other is None:
            return None
        # The nearest square that puts the mover inside its own reach.
        want = max(1, (tok.reach_ft or 5) // row.square_ft)
        best, best_d = None, 1 << 30
        for dx in range(-want - 1, want + 2):
            for dy in range(-want - 1, want + 2):
                cand = (other.x + dx, other.y + dy)
                if not grid.in_bounds(*cand):
                    continue
                d = geo.distance_squares((tok.x, tok.y), cand)
                if geo.token_distance_ft(
                        geo.footprint(cand[0], cand[1], size_squares(tok.size)),
                        geo.footprint(other.x, other.y, size_squares(other.size)),
                        row.square_ft) > (tok.reach_ft or 5):
                    continue
                # Closest, and of the equally close ones the highest: a
                # creature that has to close still picks the side of the step
                # it would rather be standing on.
                score = d * 4 - _gain(cand)
                if score < best_d:
                    best, best_d = cand, score
        target_sq = best
    elif band_l in ("near", "far"):
        foes = [t for t in vtt.tokens(map_id, include_defeated=False)
                if t.team != tok.team]
        if not foes:
            return None
        anchor = min(foes, key=lambda o: geo.distance_squares((tok.x, tok.y), (o.x, o.y)))
        want_ft = 15 if band_l == "near" else 60
        best, best_err = None, 1 << 30
        for x, y in grid.squares():
            if not grid.passable(x, y):
                continue
            d = geo.distance_ft((x, y), (anchor.x, anchor.y), row.square_ft)
            err = abs(d - want_ft) + geo.distance_squares((tok.x, tok.y), (x, y))
            # Anything holding a range band belongs UP. Weighted a square per
            # five feet gained, which is enough to beat a small error in the
            # band and not enough to send it across the board for a ledge.
            err -= _gain((x, y)) // 5
            if err < best_err:
                best, best_err = (x, y), err
        target_sq = best

    if target_sq is None:
        return None
    # ``free`` — the engine already charged this creature's movement in its own
    # economy; charging again on the board would double-bill the turn.
    return vtt.move_token(tok.id, target_sq[0], target_sq[1], free=True,
                          enforce_speed=False)


class BoardSpatial:
    """Exact distances for ``combat.CombatEngine``, taken off the board.

    The engine asks two questions — "how far apart are these two?" and "how far
    can this one reach?" — and falls back to its own band model whenever this
    answers ``None``. So attaching one of these upgrades reach checks from
    near/far to real feet without the engine knowing a grid exists, and
    detaching it puts the table straight back to theater of the mind.

        combat_engine.spatial = BoardSpatial(vtt, map_id)
    """

    def __init__(self, vtt: VttEngine, map_id: int):
        self.vtt = vtt
        self.map_id = map_id
        row = vtt.get_scene(map_id)
        self.square_ft = row.square_ft if row else 5
        self._tokens = {t.combatant_id: t for t in vtt.tokens(map_id)
                        if t.combatant_id}

    def _tok(self, c):
        """This creature's token, as it stands RIGHT NOW.

        The snapshot taken in ``__init__`` says which token belongs to which
        combatant, which never changes during a fight — and it also carried
        positions, which change constantly. A provider is asked mid-turn, and
        things move mid-turn: ``apply_band_move`` walks a creature, ``push``
        shoves one, ``jump_toward`` leaps one, and every reach check made after
        that was answered from where the creature used to be. So the row is
        re-read and only the MAPPING is cached.
        """
        tok = self._tokens.get(getattr(c, "id", None))
        if tok is None:
            return None
        return self.vtt.get_token(tok.id) or tok

    def distance_ft(self, a, b) -> Optional[int]:
        ta, tb = self._tok(a), self._tok(b)
        if ta is None or tb is None:
            return None
        # Height counts: this is what gates the combat engine's reach checks
        # and weapon ranges, so a flier overhead has to read as far away.
        row = self.vtt.get_scene(self.map_id)
        dz = self.vtt.height_gap_ft(row, ta, tb) if row else 0
        return geo.token_distance_ft(
            geo.footprint(ta.x, ta.y, size_squares(ta.size)),
            geo.footprint(tb.x, tb.y, size_squares(tb.size)),
            self.square_ft, dz_ft=dz)

    def reach_ft(self, c) -> int:
        t = self._tok(c)
        return int(t.reach_ft or 5) if t is not None else 5

    def size(self, c) -> Optional[str]:
        """This creature's size category, or None when the board has no token.
        Push is gated on it — "Large or smaller"."""
        t = self._tok(c)
        return t.size if t is not None else None

    def push(self, target, away_from, distance_ft: int) -> Optional[int]:
        """Forced movement for a Weapon Mastery, in real feet.

        The board already owns this: ``shove`` ignores the target's speed,
        provokes no opportunity attack and stops at the first obstacle, which
        is exactly what a Push is. Returning the distance actually travelled
        matters — a creature shoved into a wall goes as far as the wall.
        """
        tt, ta = self._tok(target), self._tok(away_from)
        if tt is None or ta is None:
            return None
        out = self.vtt.shove(tt.id, away_from=ta.name,
                             distance_ft=int(distance_ft))
        moved = out.get("moved_ft") if isinstance(out, dict) else None
        return int(moved) if moved is not None else None

    def slow(self, c, amount_ft: int) -> bool:
        """Take feet off a creature's Speed for the rest of the round.

        The board is where a speed reduction can actually be FELT: its movement
        budget is real feet (``speed_ft - moved_ft``), where the gridless band
        model has nothing finer than a whole move to take away.
        """
        t = self._tok(c)
        if t is None:
            return False
        self.vtt.update_token(
            t.id, speed_ft=max(0, int(t.speed_ft or 30) - int(amount_ft)))
        return True

    def cover(self, attacker, target) -> Optional[str]:
        ta, tb = self._tok(attacker), self._tok(target)
        if ta is None or tb is None:
            return None
        return self.vtt.cover_for(self.map_id, ta.name, tb.name)

    def underwater(self) -> bool:
        """Is this fight being fought in the water?

        The board already knew — a swim-medium board is one whose layout is
        only connected to a swimmer — but the combat engine had no way to ask,
        so the underwater rules lived as a sentence of prose in the arena
        catalogue telling the DM to remember them by hand.
        """
        row = self.vtt.get_scene(self.map_id)
        return bool(row) and self.vtt.board_mode(row) == "swim"

    def swims(self, c) -> bool:
        """Has this creature a swimming speed? Not: is it currently swimming."""
        t = self._tok(c)
        return t is not None and self.vtt.swim_speed_ft(t) > 0

    def squeezing(self, c) -> bool:
        """Is this creature forcing itself through too small a space?"""
        t = self._tok(c)
        return t is not None and bool(t.squeezing)

    def search(self, actor) -> Optional[dict]:
        """Take the Search action on the board, for a creature the engine runs.

        The engine knows a creature wants to look for something; only the board
        knows who is hidden, what they rolled and whether this searcher beat it.
        Same shape as every other board->combat channel: a callback, so the
        engine keeps knowing nothing about squares.
        """
        tok = self.vtt.token_for_combatant(self.map_id, getattr(actor, "id", 0))
        if tok is None:
            return None
        return self.vtt.search(self.map_id, tok.name)

    def _walk_costs_to(self, ta, tb):
        """Cost from every square to ``tb``'s square, for a creature like ``ta``.

        Grown from the TARGET outward rather than searched per candidate: terrain
        costs the same in both directions, so one Dijkstra answers "how far from
        here to it" for the whole board at once — and the alternative is a path
        search per square, of which the jump search wants dozens.
        """
        row = self.vtt.get_scene(self.map_id)
        lvl = int(ta.level or 0)
        grid = self.vtt.grid_of(row, lvl)
        blocked = self.vtt._occupied(self.map_id, exclude=ta.id, level=lvl,
                                     ignore_teams=(ta.team,) if ta.team else ())
        return geo.reachable_costs(
            grid, (tb.x, tb.y), max(int(ta.speed_ft or 30) * 4, 160),
            size=size_squares(ta.size), mode=ta.movement_mode, blocked=blocked,
            extra_cost=self.vtt._effect_cost_fn(self.map_id, ta.movement_mode, row),
            square_ft=max(1, int(row.square_ft or 5)))

    def move_to_band(self, actor, band: str) -> Optional[bool]:
        """Walk this creature to a square that MEANS that band, now.

        The engine changes a band and the board has to hear about it, or the
        rest of the turn is measured from where the creature used to be. That
        is not hypothetical: with a provider attached, a monster that closed to
        melee and swung had its swing checked against its ORIGINAL square and
        refused, every time. Fights on every archetype stopped resolving — the
        arena had been reporting them as fine only because it never attached a
        provider at all.

        The end-of-turn ``sync_after_turn`` still runs and still has work to do
        (cover, auras, bands the DM changed in narration); this is the same
        translation applied at the moment it actually matters.
        """
        cid = getattr(actor, "id", None)
        if cid is None or self._tok(actor) is None:
            return None
        return apply_band_move(self.vtt, self.map_id, cid, band,
                               tracker=self.vtt.tracker)

    def _closest_reachable(self, ta, tb, to_target) -> tuple[tuple[int, int], dict]:
        """The square this creature can WALK to that gets nearest the target.

        Shared by the run-up and by ``advance_toward``, because they are asking
        the same question. Ranks by walking cost to the target where a route
        exists and by straight-line distance where none does — a route always
        beats no route, and without that second case nothing on the far side of
        a chasm is comparable to anything at all.
        """
        row = self.vtt.get_scene(self.map_id)
        sq_ft = max(1, int(row.square_ft or 5)) if row else 5

        def score(sq) -> tuple[int, float]:
            got = to_target.get(sq)
            if got is not None:
                return (0, float(got))
            return (1, float(geo.distance_ft(sq, (tb.x, tb.y), sq_ft)))

        opts = self.vtt.movement_options(ta.id) or {}
        reach_sqs = {(s["x"], s["y"]): int(s["cost"])
                     for s in opts.get("squares") or []}
        reach_sqs[(ta.x, ta.y)] = 0
        best = min(reach_sqs, key=lambda s: (score(s), reach_sqs[s]))
        return best, reach_sqs

    def advance_toward(self, actor, target) -> Optional[int]:
        """Cover as much ground toward that creature as the movement allows.

        A move the engine cannot COMPLETE was refused outright, which is right
        for a band model — there is no half a band — and wrong on a board, where
        walking most of the way is exactly what a player does. Opposed spawn
        zones on a 30x22 board are ninety feet apart and a walking creature
        needs three turns to cross that, so every melee creature in the roster
        stood on its spawn square for the whole fight, refusing the same
        unreachable move forty times. Measured: 0 of 6 bouts resolved.

        Returns the feet covered, or None when there is no board answer.
        """
        ta, tb = self._tok(actor), self._tok(target)
        if ta is None or tb is None:
            return None
        row = self.vtt.get_scene(self.map_id)
        if row is None or not row.active or int(tb.level or 0) != int(ta.level or 0):
            return None
        step, _ = self._closest_reachable(ta, tb, self._walk_costs_to(ta, tb))
        if step == (ta.x, ta.y):
            return 0
        got = self.vtt.move_token(ta.id, step[0], step[1])
        if not got.get("ok"):
            return None
        sync_bands(self.vtt, self.map_id, tracker=self.vtt.tracker)
        return int(got.get("cost_ft") or 0)

    def gap_between(self, actor, target) -> Optional[bool]:
        """Is there something between these two that walking has to go ROUND?

        The cheap half of the jump question, and the only half the PLANNER needs:
        deciding to try a leap does not require knowing where it lands. True when
        there is no walking route at all — the chasm case — or when the route is
        more than twice the straight line, which is what a channel or a terrace
        face does to a path.

        **Twice, not half again.** At 1.5x this fired on a ruins board every
        turn, because broken walls make every path wander a little; the plan
        then replaced closing-and-swinging with a leap that mostly did not
        exist, and the turn went nowhere. A threshold that fires when there is
        nothing to jump is worse than one that misses a jump there was.
        """
        ta, tb = self._tok(actor), self._tok(target)
        if ta is None or tb is None or (ta.movement_mode or "walk") == "fly":
            return None
        row = self.vtt.get_scene(self.map_id)
        if row is None or not row.active or int(tb.level or 0) != int(ta.level or 0):
            return None
        walk = self._walk_costs_to(ta, tb).get((ta.x, ta.y))
        straight = geo.distance_ft((ta.x, ta.y), (tb.x, tb.y),
                                   max(1, int(row.square_ft or 5)))
        return walk is None or walk > straight * 2.0 + 10

    def jump_toward(self, actor, target) -> Optional[dict]:
        """Take a run at whatever is in the way, and leap it. Always commits.

        The board has been able to jump since ``VttEngine.jump`` went in, and
        nothing without a player behind it ever did — so a monster met a chasm, a
        ten-foot channel or a terrace face and walked round it, every time, on
        boards deliberately built to make that expensive.

        The engine cannot choose the square: it thinks in BANDS and has never
        known a square exists. So the whole decision lives here, like ``push``
        and ``search``, and the engine only ever says "jump toward that one".

        **The RUN-UP is the part that makes it work at all.** A standing jump
        clears half your Strength score — five feet for most creatures, which is
        one square, which lands you in the channel. The SRD's running jump needs
        ten feet of movement first, and a turn's plan begins with nobody having
        moved, so a leap decided from a standstill is always the useless one. So
        this walks to the take-off first: the reachable square that gets closest
        to the target on foot, which on a board with a gap in it IS the lip of
        the gap. That walk is real movement through ``move_token`` — it pays for
        difficult ground and provokes exactly as it always did — and it is worth
        making even if no jump turns out to be worth taking, because walking
        toward the target is what the creature would have done anyway.

        Scoring is FEET where a route exists and straight-line distance where
        none does, with a route always beating no route. That second case is the
        chasm, and it is the reason the method exists: nothing on the far side
        has a walking cost at all, so a cost comparison alone would never take
        the leap that is the only way across.
        """
        ta, tb = self._tok(actor), self._tok(target)
        if ta is None or tb is None or (ta.movement_mode or "walk") == "fly":
            return None
        row = self.vtt.get_scene(self.map_id)
        if row is None or not row.active:
            return None
        lvl = int(ta.level or 0)
        if int(tb.level or 0) != lvl:
            return None                      # a storey away is a stair, not a hop
        sq_ft = max(1, int(row.square_ft or 5))
        to_target = self._walk_costs_to(ta, tb)

        def score(sq) -> tuple[int, float]:
            """Lower is better. A square with a route always beats one without."""
            got = to_target.get(sq)
            if got is not None:
                return (0, float(got))
            return (1, float(geo.distance_ft(sq, (tb.x, tb.y), sq_ft)))

        # The take-off and the landing are ONE choice, not two. Walking as close
        # as possible and jumping from there is the obvious algorithm and it is
        # wrong every time: the run-up spends the entire movement budget, and a
        # jump costs its own distance in movement, so the creature arrives at
        # the lip of the channel with nothing left to cross it with. So the fan
        # of take-offs is scored WITH its landings, against a budget both share.
        here = (ta.x, ta.y)
        _, reach_sqs = self._closest_reachable(ta, tb, to_target)
        budget = max(0, int(ta.speed_ft or 30) - int(ta.moved_ft or 0))
        # Only the most promising take-offs are tried: the search is a fan of up
        # to thirty-two landings per square, and a whole reachable set is a
        # thousand probes for a decision worth a handful.
        offs = sorted(reach_sqs, key=lambda s: (score(s), reach_sqs[s]))[:8]

        best: Optional[tuple[tuple[int, float], tuple[int, int], dict]] = None
        base = min(score(s) for s in reach_sqs)
        for off in offs:
            cost = reach_sqs[off]
            left = budget - cost
            if left <= 0:
                continue
            reach = self.vtt.jump_reach_ft(ta.id, running=cost >= 10)
            span = max(0, min(int(reach.get("long_ft") or 0), left) // sq_ft)
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                for n in range(1, span + 1):
                    land = (off[0] + dx * n, off[1] + dy * n)
                    s = score(land)
                    # Must be a real improvement on the best WALK: a route where
                    # there was none, or a square's worth of feet saved. A
                    # monster that hops for no gain reads as a bug, not tactics.
                    if s[0] == base[0] and s[1] > base[1] - sq_ft:
                        continue
                    if s > base or (best is not None and s >= best[0]):
                        continue
                    probe = self.vtt.jump(ta.id, land[0], land[1], dry_run=True,
                                          frm=off, moved_ft=cost)
                    if not probe.get("ok"):
                        continue
                    best = (s, off, {"x": land[0], "y": land[1],
                                     "distance_ft": int(probe["distance_ft"])})

        # No leap worth taking — but walking toward the target is what this
        # creature would have done anyway, so it does that rather than nothing.
        target_sq = offs[0] if best is None else best[1]
        walked = 0
        if target_sq != here:
            got = self.vtt.move_token(ta.id, target_sq[0], target_sq[1])
            if got.get("ok"):
                walked = int(got.get("cost_ft") or 0)
            elif best is not None:
                best = None
        if best is None:
            if walked:
                sync_bands(self.vtt, self.map_id, tracker=self.vtt.tracker)
                return {"ok": True, "jumped": False, "walked_ft": walked}
            return None
        got = self.vtt.jump(ta.id, best[2]["x"], best[2]["y"])
        if not got.get("ok"):
            if walked:
                sync_bands(self.vtt, self.map_id, tracker=self.vtt.tracker)
                return {"ok": True, "jumped": False, "walked_ft": walked}
            return None
        got["jumped"] = True
        got["walked_ft"] = walked
        # The board just moved somebody, so the board says where everyone now
        # stands — otherwise the attack after the leap is checked against the
        # band the creature had before it.
        sync_bands(self.vtt, self.map_id, tracker=self.vtt.tracker)
        return got

    def can_see(self, a, b) -> Optional[bool]:
        """Can ``a`` perceive ``b``? ``None`` when the board can't say.

        The board knows something the combat engine cannot: light. Whether an
        attacker can see their target decides advantage in both directions, and
        without this the engine reads it off CONDITIONS alone — so a fight in
        an unlit crypt rolled exactly like a fight at noon. Answering ``None``
        (either creature isn't on the board) leaves the engine on its own
        model, which is the same contract ``distance_ft`` and ``cover`` keep.
        """
        ta, tb = self._tok(a), self._tok(b)
        if ta is None or tb is None:
            return None
        return self.vtt.can_see(self.map_id, ta.name, tb.name)


def sync_cover(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """Write every creature's cover **as seen from whoever's turn it is**.

    Cover is only ever a fact about a target and a particular attacker, so the
    single value the tracker carries per creature is pinned to the one attacker
    that matters right now: the creature acting. On Gruk's turn, everyone else's
    cover is their cover *from Gruk* — which is exactly what the engine needs
    when Gruk swings, and what the DM needs when deciding whether the shot is
    worth taking.

    The acting creature's own cover is measured from its nearest enemy instead,
    since the only attacks it faces on its own turn are reactions from whoever
    it is standing next to.

    A cover value the DM set by hand (``[[COMBAT: cover | X | half]]`` — for
    something the terrain can't know, like a creature hunkered behind a
    barricade) is kept as a floor, so recomputing never erases their ruling.
    """
    tracker = tracker or vtt.tracker
    row = vtt.get_scene(map_id)
    if tracker is None or row is None:
        return
    toks = [t for t in vtt.tokens(map_id, include_defeated=False) if t.combatant_id]
    if not toks:
        return
    actor = None
    if row.encounter_id:
        try:
            cur = tracker.current_combatant(row.encounter_id)
            actor = next((t for t in toks if t.combatant_id == (cur.id if cur else None)),
                         None)
        except Exception:
            actor = None
    overrides = ((row.notes or {}).get("cover_override") or {})

    for t in toks:
        try:
            if actor is not None and t.id != actor.id:
                cover = vtt.cover_for(map_id, actor.name, t.name)
            else:
                foes = [o for o in toks if o.team != t.team]
                if not foes:
                    continue
                nearest = min(foes, key=lambda o: geo.token_distance_ft(
                    geo.footprint(t.x, t.y, size_squares(t.size)),
                    geo.footprint(o.x, o.y, size_squares(o.size)), row.square_ft))
                cover = vtt.cover_for(map_id, nearest.name, t.name)
            manual = overrides.get(str(t.combatant_id))
            if manual and geo.COVER_ORDER.get(manual, 0) > geo.COVER_ORDER.get(cover, 0):
                cover = manual
            tracker.set_cover(t.combatant_id, cover)
        except Exception as e:
            print(f"[vtt.bridge] cover sync failed for {t.name}: {e}")


def board_band(vtt: VttEngine, map_id: int, tok, *, square_ft: int = 5) -> str:
    """The band the *board* implies for a token — the inverse of a band move."""
    foes = [o for o in vtt.tokens(map_id, include_defeated=False)
            if o.team != tok.team and o.kind not in (TokenKind.MARKER,)]
    if not foes:
        return "near"
    fp = geo.footprint(tok.x, tok.y, size_squares(tok.size))
    nearest = min(foes, key=lambda o: geo.token_distance_ft(
        fp, geo.footprint(o.x, o.y, size_squares(o.size)), square_ft))
    d = geo.token_distance_ft(
        fp, geo.footprint(nearest.x, nearest.y, size_squares(nearest.size)),
        square_ft)
    engaged = d <= max(tok.reach_ft or 5, nearest.reach_ft or 5)
    return geo.band_for_distance(d, nearest.name if engaged else None)


def _band_rank(band: Optional[str]) -> tuple[int, str]:
    """(rank, engaged-with) — rank 0 melee, 1 near, 2 far."""
    b = (band or "near").strip().lower()
    m = _MELEE_RE.match(b)
    if m:
        return 0, m.group(1).strip().lower()
    return (2, "") if b == "far" else (1, "")


def reconcile_bands(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> int:
    """Walk tokens to match bands the *engine* changed without touching the board.

    A monster's default AI closes to melee in the gridless model; nothing moved
    on the grid. Left alone, the next :func:`sync_bands` would simply overwrite
    that decision and the creature would teleport back to where its token stood.
    So before writing bands out, any combatant whose tracker band disagrees with
    its token's position gets its token moved to match the fiction.

    Returns how many tokens were repositioned.
    """
    tracker = tracker or vtt.tracker
    row = vtt.get_scene(map_id)
    if tracker is None or row is None or not row.encounter_id:
        return 0
    moved = 0
    try:
        combatants = {c.id: c for c in tracker.order(row.encounter_id)}
    except Exception:
        return 0
    for tok in vtt.tokens(map_id, include_defeated=False):
        c = combatants.get(tok.combatant_id or -1)
        if c is None or not c.position:
            continue
        want_rank, want_target = _band_rank(c.position)
        have_rank, have_target = _band_rank(
            board_band(vtt, map_id, tok, square_ft=row.square_ft))
        if want_rank == have_rank and want_target == have_target:
            continue
        if apply_band_move(vtt, map_id, c.id, c.position, tracker=tracker):
            moved += 1
    return moved


def sync_after_turn(vtt: VttEngine, map_id: int, *, tracker: Any = None) -> None:
    """One call to make the board agree with the tracker after a resolved turn."""
    mirror_from_tracker(vtt, map_id, tracker=tracker)
    # Fiction first (the engine may have moved someone by band), then write the
    # exact bands and cover back from the grid.
    reconcile_bands(vtt, map_id, tracker=tracker)
    sync_bands(vtt, map_id, tracker=tracker)
    sync_cover(vtt, map_id, tracker=tracker)
    vtt.recompute_auras(map_id)
