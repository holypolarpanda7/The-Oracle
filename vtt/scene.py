"""
``VttEngine`` — open a board, put people on it, and keep it honest.

This is the service layer the backend talks to. It owns the four VTT tables and
turns them into the two views everything else needs:

* :meth:`VttEngine.state` — the JSON the Activity overlay draws (grid, art,
  tokens, effects, fog, movement budget).
* :meth:`VttEngine.render` — a compact text board the DM prompt reads, so the
  narration knows who is behind the pillar and who is bleeding in the open.

Everything mechanical is validated here rather than trusted from the LLM:
movement is pathed with a real speed budget, spell areas are resolved to exact
squares with line of effect, cover comes from the PHB corner rule. What the
model gets to decide is *fiction* — that a wizard casts fireball centred on the
altar — never whether it was legal.

    v = VttEngine()
    v.create_tables()
    m = v.open_scene("guild:chan", kind="combat", archetype="cave",
                     name="The Sunken Shrine", encounter_id=enc.id)
    v.sync_from_encounter(m.id, enc.id)      # seat the fight on the board
    v.move_token(tok.id, 7, 4)               # pathed, costed, OA-checked
    v.add_effect(m.id, "Fireball", shape="sphere", x=9, y=5, radius_ft=20)
"""
from __future__ import annotations

import math
import os
import random
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from . import geometry as geo
from .art import (render_battlemap, render_debris, render_object,
                   layout_signature)
from .mapgen import GeneratedMap, archetype_for, generate_map
from .models import (
    EffectKind,
    MapEffect,
    MapEvent,
    MapToken,
    Shape,
    SceneKind,
    TacticalMap,
    Team,
    TokenKind,
    size_squares,
)
from .triggers import board_size_for
from .terrain import (APERTURES, FLOOR, VOID, Grid, aperture_axis,
                      object_stats, profile_height_ft, required_mode,
                      short_name, sprite_label, sprite_subject, tile)

Square = tuple[int, int]


def _ft_offset(ft) -> str:
    """A storey's height relative to the ground floor, signed.

    A level's ``base_ft`` was assumed to be a height ABOVE, and the DM board
    hard-coded the plus sign to match. It is really an offset, and a ship's hold
    proved it: eight feet BELOW the weather deck is a perfectly ordinary storey
    built out of the same machinery, and it printed as "+-8 ft".
    """
    n = int(ft or 0)
    if n > 0:
        return f"+{n} ft"
    if n < 0:
        return f"{n} ft"
    return "ground level"


def _default_engine(database_url: Optional[str] = None) -> Engine:
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        database_url = f"sqlite:///{backend_db}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)


#: Board sizes by scene kind — big enough to manoeuvre, small enough to read on
#: a phone. (width, height) in squares.
DEFAULT_SIZE: dict[str, tuple[int, int]] = {
    SceneKind.COMBAT: (24, 18),
    SceneKind.PUZZLE: (16, 14),
    SceneKind.CHASE: (34, 14),
    SceneKind.HAZARD: (20, 16),
    SceneKind.EXPLORE: (30, 24),
    SceneKind.SOCIAL: (16, 12),
}

def _landmarks_from(asked: Optional[str | Sequence[str]],
                    *, invent: bool = False) -> list[str]:
    """Catalogue slugs out of whatever a caller passed for ``landmarks``.

    A string is DM language and goes through the resolver; a list may be either
    slugs or phrases, and each element goes through the same door, so a caller
    never has to know which it is holding.

    ``invent`` lets a phrase the catalogue does not know become a one-square
    named feature (:func:`vtt.setpieces.named_feature`) instead of being
    dropped. Only ever true for a landmark the DM asked for OUTRIGHT — the same
    resolver is also fed the place text as a fallback, and a room described as
    "a smoky taproom" must not grow a statue of one.
    """
    from . import setpieces as _sp

    if not asked:
        return []
    parts = [asked] if isinstance(asked, str) else list(asked)
    out: list[str] = []
    for part in parts:
        found = _sp.landmark_for(str(part))
        if invent and _sp.describes_its_own(str(part), found):
            made = _sp.named_feature(str(part))
            if made is not None:
                found = [made.slug]
        for slug in found:
            if slug not in out:
                out.append(slug)
    return out


#: Team colours the overlay falls back to when a token has no portrait.
TEAM_COLORS = {Team.PARTY: "#4fa3ff", Team.FOE: "#ff5a5a", Team.NEUTRAL: "#d9b25a"}

#: (tile code, look) -> the catalogue swatch's image id, or None if undrawn.
#:
#: Process-level rather than per-engine because that is what the thing being
#: cached actually is: one swatch of dungeon flagstone serves every board in
#: every session forever. Cleared by a restart, which is also when a re-render
#: of the catalogue would be picked up.
_MATERIAL_IDS: dict[tuple[str, str], Optional[int]] = {}


class VttEngine:
    def __init__(self, engine: Optional[Engine] = None,
                 database_url: Optional[str] = None,
                 image_store: Any = None,
                 tracker: Any = None,
                 linked: Any = None):
        self.engine = engine or _default_engine(database_url)
        self.image_store = image_store
        # A ``combat.CombatTracker``; optional so the VTT can be used standalone
        # (puzzles, exploration) without a fight in progress.
        self.tracker = tracker
        # ``linked(session_id, a_ref, b_ref) -> bool``: an optional predicate
        # saying two creatures perceive each other REGARDLESS of what the board
        # says. The board still computes sight and cover honestly; this is a
        # deliberate override on top, for features that link creatures together
        # (see combat/bonds.py). A callback rather than an import so the VTT
        # keeps knowing nothing about any particular feature.
        self.linked = linked
        # Light maps, keyed (map_id, revision). Per-instance because two
        # engines can be pointed at different databases holding the same id.
        self._light_cache: dict = {}

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine)
        self._add_missing_columns()

    #: Columns added to ``vtt_map`` after boards already existed in the wild.
    #:
    #: ``create_all`` creates missing TABLES and nothing else, so a new field on
    #: an existing database is a column that is simply not there — and every
    #: query naming it fails, which on a live board means the tactical layer
    #: stops working rather than degrading. Same ad-hoc migration the world
    #: graph and the rules ingest already use.
    _LATE_COLUMNS: tuple[tuple[str, str], ...] = (
        ("iso_image_id", "INTEGER"),
        ("iso_art_status", "VARCHAR"),
        ("skins", "JSON"),
        ("board_style", "VARCHAR"),
    )

    def _add_missing_columns(self) -> None:
        try:
            with self.engine.begin() as conn:
                have = {r[1] for r in conn.exec_driver_sql(
                    'PRAGMA table_info("vtt_map")')}
                if not have:
                    return                      # fresh table; create_all did it
                for col, ddl in self._LATE_COLUMNS:
                    if col not in have:
                        conn.exec_driver_sql(
                            f'ALTER TABLE "vtt_map" ADD COLUMN {col} {ddl}')
        except Exception as e:                  # never block a board on this
            print(f"[vtt] column migration skipped: {e}")

    # ================================================================ scenes

    def open_scene(self, session_id: str, *, kind: str = SceneKind.COMBAT,
                   name: Optional[str] = None, archetype: Optional[str] = None,
                   place_slug: Optional[str] = None,
                   place_hint: Optional[str] = None,
                   width: Optional[int] = None, height: Optional[int] = None,
                   seed: Optional[int] = None, encounter_id: Optional[int] = None,
                   biome: Optional[str] = None, lighting: Optional[str] = None,
                   fog: bool = False, render_art: bool = True,
                   reuse_place: bool = True,
                   creatures: Optional[list] = None,
                   board_scale: Optional[str] = None,
                   longest_range_ft: int = 0,
                   landmarks: Optional[str | Sequence[str]] = None,
                   rooms: Optional[Sequence[str]] = None,
                   style: str = "",
                   auto_close: bool = True) -> TacticalMap:
        """Open a tactical board for a session, closing any board already out.

        ``archetype`` may be a generator name or loose DM language ("a smoky
        taproom") — :func:`vtt.mapgen.archetype_for` maps it. When
        ``reuse_place`` is on and this place already had a board, its layout and
        art come back instead of being regenerated, so a room the party fought
        in last week looks the same today.

        ``landmarks`` is the same kind of input one level down: slugs, or loose
        DM language ("a stepped ziggurat"), which
        :func:`vtt.setpieces.landmark_for` maps. Named nothing, the place text
        is read for one — a DM who describes a fountain in the square has said
        so whether or not they knew there was a parameter for it.

        ``rooms`` names compartments the layout should call its own — a
        bastion airship carries the facilities its owner built, and nothing
        else on the board could know what they are. The hull still decides how
        many of them there is room for and where they go.

        ``style`` asks for a whole-board material where the archetype offers a
        real choice — a skyship is timber, brass-and-steam or grown, and all
        three are right. Left empty the seed decides, which is correct for a
        vessel nobody has described and wrong for one somebody built.

        ``auto_close`` marks a board the *system* put out (a fight started), so
        the system may take it away again when the reason passes. A board the DM
        opened by hand is theirs until they close it — nothing tidies it up
        underneath them.
        """
        kind = kind if kind in SceneKind.ALL else SceneKind.COMBAT
        arch = archetype_for(archetype or place_hint or "", default="open")
        # A landmark the DM ASKED for may be one it described rather than one
        # from the catalogue; the place text, read as a fallback, may not.
        marks = _landmarks_from(landmarks, invent=True)
        if not marks:
            marks = _landmarks_from(archetype or place_hint or "")
        # The scene KIND sets the floor; the fight standing on it decides the
        # rest. Explicit width/height still win outright — a caller who names a
        # size has said something the roster can't.
        base = DEFAULT_SIZE.get(kind, (24, 18))
        auto = board_size_for(base, archetype=arch, creatures=creatures,
                              scale=board_scale,
                              longest_range_ft=int(longest_range_ft or 0),
                              landmarks=marks)
        w, h = int(width or auto[0]), int(height or auto[1])

        prior = self._prior_place_map(place_slug, arch, w, h) if (
            reuse_place and place_slug) else None
        seed = int(seed if seed is not None else (
            prior.seed if prior else random.randint(1, 2**31 - 1)))

        gen = generate_map(arch, width=w, height=h, seed=seed,
                           lighting=lighting, biome=biome or place_hint or "",
                           landmarks=marks, rooms=tuple(rooms or ()),
                           style=style or "")

        self.close_scene(session_id=session_id)

        row = TacticalMap(
            session_id=session_id, encounter_id=encounter_id,
            place_slug=place_slug,
            name=(name or (place_hint or gen.description or "Tactical Scene"))[:120],
            kind=kind, archetype=arch, biome=biome,
            width=gen.width, height=gen.height, square_ft=5,
            terrain=gen.grid.to_rows(), elevation=gen.elevation or None,
            doors=gen.doors or None, lighting=gen.lighting,
            skins=gen.skins or None, board_style=gen.style or "",
            setpieces=[dict(p) for p in gen.setpieces] or None,
            levels=[dict(l) for l in gen.levels] or None,
            fog=self._blank_fog(gen.width, gen.height) if fog else None,
            seed=seed, active=True, revision=1,
            notes={"description": gen.description,
                   "auto_close": bool(auto_close),
                   # The medium this board is fought in (walk|swim|fly) — spawn
                   # placement and default token movement follow it.
                   "mode": gen.mode,
                   "spawn_party": [list(s) for s in gen.spawn_party[:60]],
                   "spawn_foes": [list(s) for s in gen.spawn_foes[:60]],
                   # Named compartments. In `notes` rather than a column of
                   # their own because a room is not a rule — its walls and its
                   # doorway are already tiles by the time this is written, and
                   # what is left is what the place is CALLED.
                   "rooms": [dict(r) for r in gen.rooms]},
        )
        # Reuse the earlier render for a place we've already painted.
        if prior is not None and prior.background_image_id:
            row.background_image_id = prior.background_image_id
            row.art_status = "ready"
            row.art_prompt = prior.art_prompt
        with Session(self.engine) as s:
            s.add(row)
            s.commit()
            s.refresh(row)
            map_id = row.id

        self._log(map_id, session_id, "open", summary=f"{row.name} ({arch})",
                  payload={"archetype": arch, "seed": seed,
                           "size": [gen.width, gen.height], "kind": kind})

        # Ways up that the LAYOUT built — a watchtower's ladder, a ship's
        # companionway. Through `add_stairs` rather than written straight onto
        # the row, so a generated connector is checked and recorded exactly like
        # one a DM hangs by hand, and `take_stairs` is still the only way across.
        for st in gen.stairs:
            try:
                self.add_stairs(
                    map_id, int(st.get("level", 0)),
                    int(st["x"]), int(st["y"]),
                    to_level=int(st.get("to_level", 1)),
                    to_x=int(st["to_x"]), to_y=int(st["to_y"]),
                    kind=str(st.get("kind") or "stairs"))
            except Exception as e:
                print(f"[vtt] generated stair skipped: {e}")

        # Ambient features the generator suggested (campfire light, hazards).
        for eff in gen.effects:
            try:
                self.add_effect(
                    map_id, eff.get("name", "feature"),
                    kind=eff.get("kind", EffectKind.MARKER),
                    shape=eff.get("shape", Shape.SPHERE),
                    x=int(eff.get("x", 0)), y=int(eff.get("y", 0)),
                    radius_ft=int(eff.get("radius_ft", 0) or 0),
                    color=eff.get("color"),
                    squares=eff.get("squares"),
                    permanent=True,
                )
            except Exception as e:
                print(f"[vtt] ambient effect failed: {e}")

        if render_art and row.background_image_id is None:
            self.render_art(map_id, gen)
        return self.get_scene(map_id)  # type: ignore[return-value]

    def _prior_place_map(self, place_slug: Optional[str], archetype: str,
                         w: int, h: int) -> Optional[TacticalMap]:
        if not place_slug:
            return None
        with Session(self.engine) as s:
            return s.exec(
                select(TacticalMap)
                .where(TacticalMap.place_slug == place_slug,
                       TacticalMap.archetype == archetype,
                       TacticalMap.width == w, TacticalMap.height == h)
                .order_by(TacticalMap.created_at.desc())  # type: ignore[attr-defined]
            ).first()

    def render_art(self, map_id: int, gen: Optional[GeneratedMap] = None,
                   *, extra: str = "", conditions: str = "") -> Optional[int]:
        """Render the battlemap background (blocking; call it in a task).

        ``extra`` is the ground texture of the world-graph place this board
        depicts — the caller's job to look up, because ``vtt`` deliberately
        knows nothing about the world graph (the arena drives these same
        generators with no graph at all). Passing it is what makes the
        battlemap floor match the establishing shot of the same location.

        Failure is normal and silent-ish: with no GPU the board just shows tiles.
        """
        row = self.get_scene(map_id)
        if row is None:
            return None
        if gen is None:
            gen = self.regenerate(row)
        # The signature is taken from the CURRENT grid, so the cache key always
        # matches what the picture was actually drawn from. Pinning it to the
        # pristine layout looked like a way to survive battle damage, but two
        # tables that painted different furniture into the same generated room
        # would then have shared one picture.
        #
        # Damage doesn't re-render anyway: nothing calls this on a break — the
        # wreckage path draws a small sprite instead (see render_debris).
        self._set_fields(map_id, art_status="pending")
        cn, cn_strength = "", 0.8
        try:
            from game_config import get_config
            _img = get_config().imagery
            cn = getattr(_img, "map_controlnet", "") or ""
            cn_strength = float(getattr(_img, "map_controlnet_strength", 0.8))
        except Exception as e:
            print(f"[vtt] controlnet config unavailable: {e}")
        art = render_battlemap(
            gen, store=self.image_store, name=row.name, biome=row.biome,
            lighting=row.lighting, extra=extra, conditions=conditions,
            controlnet=cn or None, controlnet_strength=cn_strength)
        self._set_fields(
            map_id,
            background_image_id=art.image_id,
            art_status=("ready" if art.image_id else "offline"),
            art_prompt=(art.caption or None),
        )
        return art.image_id

    def regenerate(self, row: TacticalMap) -> GeneratedMap:
        """Rebuild the generator output for a stored map (same seed = same board)."""
        gen = generate_map(row.archetype, width=row.width, height=row.height,
                           seed=row.seed, lighting=row.lighting,
                           biome=row.biome or "")
        # The stored terrain wins — a DM may have edited it after generation.
        gen.grid = self.grid_of(row)
        # ...and so do the stored skins. Regenerating from the seed produces
        # them again identically, but a board whose materials were chosen at
        # open time and then edited must be REPAINTED as it stands, not as the
        # generator would build it today.
        if row.skins:
            gen.skins = dict(row.skins)
        if row.board_style:
            gen.style = row.board_style
        return gen

    def active_scene(self, session_id: str) -> Optional[TacticalMap]:
        with Session(self.engine) as s:
            return s.exec(
                select(TacticalMap).where(
                    TacticalMap.session_id == session_id,
                    TacticalMap.active == True,  # noqa: E712
                ).order_by(TacticalMap.created_at.desc())  # type: ignore[attr-defined]
            ).first()

    def get_scene(self, map_id: int) -> Optional[TacticalMap]:
        with Session(self.engine) as s:
            return s.get(TacticalMap, map_id)

    def scene_for_encounter(self, encounter_id: int) -> Optional[TacticalMap]:
        with Session(self.engine) as s:
            return s.exec(
                select(TacticalMap).where(
                    TacticalMap.encounter_id == encounter_id,
                    TacticalMap.active == True,  # noqa: E712
                )
            ).first()

    def update_scene_encounter(self, map_id: int, encounter_id: Optional[int]) -> None:
        """Re-point a board at a (new) fight — a second wave in the same room."""
        self._set_fields(map_id, encounter_id=encounter_id)

    def close_scene(self, map_id: Optional[int] = None, *,
                    session_id: Optional[str] = None) -> Optional[TacticalMap]:
        """Put the board away. Tokens and effects are kept for the replay log."""
        closed: Optional[TacticalMap] = None
        with Session(self.engine) as s:
            rows: Iterable[TacticalMap]
            if map_id is not None:
                row = s.get(TacticalMap, map_id)
                rows = [row] if row else []
            else:
                rows = s.exec(select(TacticalMap).where(
                    TacticalMap.session_id == session_id,
                    TacticalMap.active == True,  # noqa: E712
                )).all()
            for row in rows:
                row.active = False
                row.revision += 1
                row.updated_at = _now()
                s.add(row)
                closed = row
            s.commit()
            if closed:
                s.refresh(closed)
        if closed and closed.id:
            self._log(closed.id, closed.session_id, "close", summary=closed.name)
        return closed

    # ---- terrain -----------------------------------------------------------

    # ================================================================ levels

    def levels_of(self, row: TacticalMap) -> list[dict]:
        """Every floor of this board, level 0 first. Always at least one."""
        base = {"name": "Ground", "base_ft": 0, "terrain": row.terrain or [],
                "stairs": []}
        return [base, *[dict(l) for l in (row.levels or [])]]

    def grid_of(self, row: TacticalMap, level: int = 0) -> Grid:
        """The tile grid of one floor. Level 0 is the board's own terrain.

        Defaulted rather than required so that every caller written before
        upper floors existed keeps meaning what it meant: a single-storey board
        has exactly one grid and this is it.
        """
        if not level or not row.levels:
            return Grid.from_rows(row.terrain or [])
        lv = self.levels_of(row)
        idx = max(0, min(int(level), len(lv) - 1))
        return Grid.from_rows(lv[idx].get("terrain") or row.terrain or [])

    def level_base_ft(self, row: TacticalMap, level: int) -> int:
        """How far above the ground floor this level's floor sits."""
        lv = self.levels_of(row)
        idx = max(0, min(int(level or 0), len(lv) - 1))
        return int(lv[idx].get("base_ft") or 0)

    def add_level(self, map_id: int, *, name: str = "Upper", base_ft: int = 15,
                  terrain: Optional[list] = None,
                  solid: bool = False) -> dict:
        """Add a floor above this board. Returns its index.

        A new level starts as ALL VOID — a floor that isn't there yet — because
        that is the honest default: the gallery is the railed strip you build,
        and everywhere you don't build is open to the hall below. ``solid``
        floors the whole footprint instead, for a storey rather than a balcony.
        """
        row = self.get_scene(map_id)
        if row is None:
            return {"ok": False, "reason": "no board is out"}
        rows = terrain or ([FLOOR * row.width for _ in range(row.height)]
                           if solid else [VOID * row.width for _ in range(row.height)])
        levels = [dict(l) for l in (row.levels or [])]
        levels.append({"name": name, "base_ft": int(base_ft),
                       "terrain": rows, "stairs": []})
        self._set_fields(map_id, levels=levels)
        return {"ok": True, "level": len(levels), "name": name,
                "detail": f"{name} added {base_ft} ft above the floor."}

    def add_stairs(self, map_id: int, level: int, x: int, y: int, *,
                   to_level: int, to_x: int, to_y: int,
                   kind: str = "stairs") -> dict:
        """Link a square on one floor to a square on another, both ways.

        Levels are otherwise sealed from each other: you cannot walk up. A
        connector is the only way between them, which is what makes an upper
        floor a place you have to REACH rather than a second set of squares
        everyone can stand on at will.
        """
        row = self.get_scene(map_id)
        if row is None:
            return {"ok": False, "reason": "no board is out"}
        levels = [dict(l) for l in (row.levels or [])]
        n_levels = len(levels) + 1
        if not (0 <= level < n_levels and 0 <= to_level < n_levels):
            return {"ok": False, "reason": "no such level on this board"}

        def _put(lv: int, ax: int, ay: int, blv: int, bx: int, by: int) -> None:
            entry = {"x": int(ax), "y": int(ay), "to": int(blv),
                     "tx": int(bx), "ty": int(by), "kind": kind}
            if lv == 0:
                ground.append(entry)
            else:
                levels[lv - 1].setdefault("stairs", []).append(entry)

        ground: list = list((row.notes or {}).get("stairs") or [])
        _put(level, x, y, to_level, to_x, to_y)
        _put(to_level, to_x, to_y, level, x, y)
        notes = dict(row.notes or {})
        notes["stairs"] = ground
        self._set_fields(map_id, levels=levels, notes=notes)
        return {"ok": True,
                "detail": (f"{kind} joins level {level} at {x},{y} to "
                           f"level {to_level} at {to_x},{to_y}.")}

    def stairs_on(self, row: TacticalMap, level: int) -> list[dict]:
        """Connectors leaving this floor."""
        if not level:
            return list((row.notes or {}).get("stairs") or [])
        lv = self.levels_of(row)
        idx = max(0, min(int(level), len(lv) - 1))
        return list(lv[idx].get("stairs") or [])

    def take_stairs(self, map_id: int, ref: str) -> dict:
        """Use the connector under this creature's feet. Costs nothing extra.

        5e charges movement for the SQUARES you cross, and a staircase already
        occupies squares — charging again for changing floor would tax the
        same feet twice.
        """
        t = self.find_token(map_id, ref)
        row = self.get_scene(map_id)
        if not (t and row):
            return {"ok": False, "reason": "no such creature on this board"}
        here = next((s for s in self.stairs_on(row, int(t.level or 0))
                     if int(s.get("x", -1)) == t.x and int(s.get("y", -1)) == t.y),
                    None)
        if here is None:
            return {"ok": False,
                    "reason": (f"{t.name} isn't standing on any stair, ladder or "
                               f"opening between floors")}
        dest = (int(here["tx"]), int(here["ty"]))
        to_level = int(here["to"])
        self.update_token(t.id, level=to_level)
        self._place(t.id, dest)
        carried = self._rider_of(t)
        if carried is not None:
            self.update_token(carried.id, level=to_level)
            self._place(carried.id, dest)
        self._bump(map_id)
        names = [l.get("name") for l in self.levels_of(row)]
        self._log(map_id, row.session_id, "move", actor=t.name,
                  summary=f"{t.name} takes the {here.get('kind', 'stairs')}")
        return {"ok": True, "level": to_level, "x": dest[0], "y": dest[1],
                "detail": (f"{t.name} takes the {here.get('kind', 'stairs')} to "
                           f"{names[to_level] if to_level < len(names) else 'the next floor'} "
                           f"and comes out at {dest[0]},{dest[1]}.")}

    def grid(self, map_id: int) -> Grid:
        row = self.get_scene(map_id)
        return self.grid_of(row) if row else Grid.blank(1, 1)

    def set_terrain(self, map_id: int, squares: Iterable[Square], code: str) -> int:
        """Paint tiles — a collapsing wall, a bridge that burns away."""
        row = self.get_scene(map_id)
        if row is None:
            return 0
        g = self.grid_of(row)
        n = 0
        for x, y in squares:
            if g.in_bounds(x, y):
                g.set(x, y, code)
                n += 1
        if n:
            self._set_fields(map_id, terrain=g.to_rows())
            self._log(map_id, row.session_id, "terrain",
                      summary=f"{n} square(s) -> {tile(code).name}",
                      payload={"code": code})
        return n

    def set_cover_override(self, map_id: int, combatant_id: int,
                           cover: Optional[str]) -> None:
        """Remember a cover ruling the DM made by hand.

        The board can only see what the terrain grants. When the DM says a
        creature has cover for a reason the grid doesn't know — hunkered behind
        a barricade, half-buried in the rubble, shooting from a murder hole —
        that ruling is kept here and used as a floor when cover is recomputed
        each turn, so the DM's call never gets quietly overwritten.
        """
        row = self.get_scene(map_id)
        if row is None:
            return
        notes = dict(row.notes or {})
        overrides = dict(notes.get("cover_override") or {})
        if cover and cover != "none":
            overrides[str(combatant_id)] = cover
        else:
            overrides.pop(str(combatant_id), None)
        notes["cover_override"] = overrides
        self._set_fields(map_id, notes=notes)

    def set_elevation(self, map_id: int, squares: Iterable[Square], feet: int) -> int:
        """Raise (or level, with 0) squares. Climbing them costs the extra feet."""
        row = self.get_scene(map_id)
        if row is None:
            return 0
        elev = dict(row.elevation or {})
        n = 0
        for x, y in squares:
            key = f"{x},{y}"
            if feet:
                elev[key] = int(feet)
            else:
                elev.pop(key, None)
            n += 1
        if n:
            self._set_fields(map_id, elevation=elev or None)
            self._log(map_id, row.session_id, "terrain",
                      summary=f"{n} square(s) set to {feet} ft elevation",
                      payload={"feet": feet})
        return n

    def set_door(self, map_id: int, x: int, y: int, state: str) -> bool:
        """Open/close/lock a door — the grid code follows the state."""
        row = self.get_scene(map_id)
        if row is None:
            return False
        doors = [dict(d) for d in (row.doors or [])]
        found = next((d for d in doors if int(d.get("x", -1)) == x
                      and int(d.get("y", -1)) == y), None)
        if found is None:
            found = {"x": x, "y": y, "name": "door", "dc": None}
            doors.append(found)
        found["state"] = state
        g = self.grid_of(row)
        g.set(x, y, "/" if state == "open" else "+")
        self._set_fields(map_id, doors=doors, terrain=g.to_rows())
        self._log(map_id, row.session_id, "door", summary=f"door at {x},{y} {state}")
        return True

    # ----- breakable furniture -------------------------------------------

    def object_at(self, map_id: int, x: int, y: int) -> Optional[dict]:
        """The breakable thing in a square, with any damage it has taken.

        Lazily derived: a square nobody has hit carries no stored state, so it
        answers from the tile's defaults. That keeps a fresh board's JSON empty
        and means terrain painted later is breakable without anyone
        initialising it.
        """
        row = self.get_scene(map_id)
        if row is None:
            return None
        g = self.grid_of(row)
        if not g.in_bounds(x, y):
            return None
        base = object_stats(g.get(x, y))
        if base is None:
            return None
        stored = (row.objects or {}).get(f"{x},{y}")
        if isinstance(stored, dict):
            return {**base, **stored}
        return base

    def damage_object(self, map_id: int, x: int, y: int, amount: int, *,
                      damage_type: str = "") -> dict:
        """Hit a breakable square. At 0 it breaks, and the terrain changes.

        The terrain change is the point: cover, sight and movement all read the
        tile, so a toppled pillar stops granting cover and a smashed door stops
        blocking the way without anything else being told. That is the same
        reason the art is generated from the grid — one truth, consulted.
        """
        row = self.get_scene(map_id)
        if row is None:
            return {"ok": False, "reason": "no board is out"}
        obj = self.object_at(map_id, x, y)
        if obj is None:
            g = self.grid_of(row)
            what = g.tile_at(x, y).name if g.in_bounds(x, y) else "nothing"
            return {"ok": False,
                    "reason": f"there is nothing breakable at {x},{y} ({what})"}

        dtype = (damage_type or "").strip().lower()
        amount = max(0, int(amount))
        note = ""
        if dtype and dtype in [d.lower() for d in obj.get("immune", [])]:
            return {"ok": False, "immune": True, "hp": obj["hp"],
                    "reason": f"the {obj['name']} is immune to {dtype} damage"}
        if dtype and dtype in [d.lower() for d in obj.get("resists", [])]:
            amount //= 2
            note = f" (resistant to {dtype})"

        hp = max(0, int(obj["hp"]) - amount)
        objects = dict(row.objects or {})
        entry = {k: obj[k] for k in ("hp_max", "ac", "name", "material")
                 if k in obj}
        entry["hp"] = hp
        objects[f"{x},{y}"] = entry

        if hp > 0:
            self._set_fields(map_id, objects=objects)
            self._log(map_id, row.session_id, "terrain",
                      summary=f"{obj['name']} at {x},{y} takes {amount}{note} "
                              f"({hp}/{obj['hp_max']})")
            return {"ok": True, "hp": hp, "hp_max": obj["hp_max"],
                    "broken": False, "name": obj["name"], "ac": obj["ac"],
                    "detail": (f"The {obj['name']} takes {amount}{note} — "
                               f"{hp}/{obj['hp_max']}.")}

        # Broken: the square becomes what it leaves behind, and its damage
        # record goes with it (the rubble is not a damaged pillar).
        objects.pop(f"{x},{y}", None)
        g = self.grid_of(row)
        becomes = obj.get("becomes", ",")
        g.set(x, y, becomes)
        self._set_fields(map_id, objects=objects or None, terrain=g.to_rows())
        # A door that is smashed is a door that is open.
        if becomes == "/":
            doors = [dict(d) for d in (row.doors or [])]
            found = next((d for d in doors if int(d.get("x", -1)) == x
                          and int(d.get("y", -1)) == y), None)
            if found is None:
                found = {"x": x, "y": y, "name": "door", "dc": None}
                doors.append(found)
            found["state"] = "broken"
            self._set_fields(map_id, doors=doors)
        self._bump(map_id)
        self.recompute_auras(map_id)
        # Note the wreckage. The SPRITE is drawn later, off the reply path —
        # this only records that there is something to draw, so the board is
        # correct immediately whether or not a GPU ever gets to it.
        #
        # Nothing is recorded when a break leaves ordinary GROUND: there is no
        # wreckage to paint, and asking the model for "open floor, the remains
        # of a destroyed thing" produces a picture of nothing. A smashed door
        # is NOT that case — it leaves an open doorway with its wreckage still
        # hanging in the frame, which is worth seeing.
        if becomes not in (".", "g", "s", "="):
            deb = dict(row.debris or {})
            deb[f"{x},{y}"] = {"code": becomes, "was": obj["name"],
                               "material": obj.get("material", ""),
                               "image_id": None}
            self._set_fields(map_id, debris=deb)
        self._log(map_id, row.session_id, "terrain",
                  summary=f"{obj['name']} at {x},{y} is destroyed",
                  payload={"x": x, "y": y, "becomes": becomes})
        return {"ok": True, "hp": 0, "hp_max": obj["hp_max"], "broken": True,
                "name": obj["name"], "becomes": tile(becomes).name,
                "ac": obj["ac"],
                "detail": (f"The {obj['name']} comes apart, leaving "
                           f"{tile(becomes).name}.")}

    def render_debris(self, map_id: int, *, conditions: str = "") -> int:
        """Draw sprites for wreckage that hasn't got one yet. Returns how many.

        Called off the reply path like the battlemap render. Sprites are tiny
        (one square) and SHARED by what-broke-into-what plus the board's look,
        so the first shattered pillar pays for every shattered pillar after it.
        """
        row = self.get_scene(map_id)
        if row is None or not row.debris:
            return 0
        deb = dict(row.debris)
        made = 0
        ctx = ", ".join(p for p in ((row.biome or ""), conditions) if p)
        for key, entry in deb.items():
            if not isinstance(entry, dict) or entry.get("image_id"):
                continue
            img = render_debris(entry.get("code", ","), store=self.image_store,
                                material=entry.get("material", ""),
                                was=entry.get("was", ""), context=ctx)
            if img:
                deb[key] = {**entry, "image_id": img}
                made += 1
        if made:
            self._set_fields(map_id, debris=deb)
            self._bump(map_id)
        return made

    def objects_for(self, map_id: int) -> list[dict]:
        """Discrete objects on the board, with their sprite when one exists.

        Read from the TERRAIN each time rather than stored: the grid is already
        the truth about what stands where, and a second list would be a second
        truth to keep in step. A square that has broken is no longer its object
        — it is whatever it became — so this needs no clearing either.
        """
        row = self.get_scene(map_id)
        if row is None:
            return []
        g = self.grid_of(row)
        sprites = dict(row.object_art or {})
        out = []
        for x, y in g.squares():
            code = g.get(x, y)
            subject = sprite_subject(code)
            if not subject:
                continue
            out.append({"x": x, "y": y, "code": code,
                        "name": tile(code).name,
                        "label": sprite_label(code),
                        # A door belongs to the wall it interrupts, so it is
                        # drawn as a panel lying along that wall rather than a
                        # picture filling the square. Which way the wall runs
                        # is read off the grid here, once, for every view.
                        "axis": (aperture_axis(g, x, y)
                                 if code in APERTURES else ""),
                        "image_id": sprites.get(tile(code).name)})
        return out

    def render_iso_art(self, map_id: int, gen: Optional[GeneratedMap] = None,
                       *, extra: str = "", conditions: str = "") -> Optional[int]:
        """Paint the isometric view of this board (blocking; run it in a task).

        The counterpart to :meth:`render_art`, and deliberately separate: that
        one is the top-down picture a Discord table looks at, this one is the
        painted layer the Activity lays over its geometry. A board can have
        either, both or neither, and neither is required for play.
        """
        from .art import render_iso_board

        row = self.get_scene(map_id)
        if row is None:
            return None
        if gen is None:
            gen = self.regenerate(row)
        cn, strength = "", 0.55
        union, seg_cn, seg_str, regional = "", "", 0.45, False
        try:
            from game_config import get_config
            _img = get_config().imagery
            cn = getattr(_img, "isoboard_controlnet", "") or ""
            strength = float(getattr(_img, "isoboard_controlnet_strength", 0.55))
            union = getattr(_img, "isoboard_controlnet_union_type", "") or ""
            seg_cn = getattr(_img, "isoboard_seg_controlnet", "") or ""
            seg_str = float(getattr(_img, "isoboard_seg_strength", 0.45))
            regional = bool(getattr(_img, "isoboard_regional_prompt", False))
        except Exception as e:
            print(f"[vtt] isoboard controlnet config unavailable: {e}")
        if not cn:
            # No depth model, no painting. Refused rather than faked — see
            # render_iso_board.
            self._set_fields(map_id, iso_art_status="offline")
            return None
        self._set_fields(map_id, iso_art_status="pending")
        art = render_iso_board(
            gen, store=self.image_store, name=row.name, biome=row.biome,
            lighting=row.lighting, extra=extra, conditions=conditions,
            controlnet=cn, controlnet_strength=strength,
            controlnet_union_type=union,
            seg_controlnet=seg_cn, seg_strength=seg_str, regional=regional)
        self._set_fields(map_id, iso_image_id=art.image_id,
                         iso_art_status=("ready" if art.image_id else "offline"))
        return art.image_id

    def decor_for(self, map_id: int) -> list[dict]:
        """Scenery on this board — drawn, and mechanically inert.

        Derived from the layout and the seed rather than stored, the same way
        ``objects_for`` reads the grid: the room already determines it, and a
        second list would be a second thing to keep in step.
        """
        from .decor import decor_for as _decor
        from .terrain import tile_height_ft

        row = self.get_scene(map_id)
        if row is None:
            return []
        rows = self.grid_of(row).to_rows()
        return _decor(rows, seed=row.seed or 0,
                      standing=lambda c: tile_height_ft(c) > 0,
                      archetype=row.archetype or "")

    def setpieces_for(self, map_id: int) -> list[dict]:
        """Landmarks on this board, ready for either renderer.

        The row stores the DECISION — which piece, where, which way round — and
        everything else is read back from the catalogue here: footprint, height,
        the mesh URL and the scale measured off that mesh. So correcting an
        entry fixes boards that already exist, and a piece dropped from the
        catalogue disappears instead of raising.
        """
        from . import setpieces as sp

        row = self.get_scene(map_id)
        if row is None or not row.setpieces:
            return []
        out: list[dict] = []
        for rec in row.setpieces:
            if not isinstance(rec, dict):
                continue
            slug = str(rec.get("slug") or "")
            # The stored name rebuilds a piece the DM invented in an earlier
            # process — see setpieces.piece.
            if sp.piece(slug, str(rec.get("name") or "")) is None:
                continue
            out.append(sp.Placed(slug=slug, x=int(rec.get("x") or 0),
                                 y=int(rec.get("y") or 0),
                                 yaw=int(rec.get("yaw") or 0)).instance())
        return out

    def last_move(self, map_id: int) -> Optional[dict]:
        """The most recent walk on this board: ``{id, token_id, path}``.

        For the CLIENT to animate along, and it has to come from here because
        the route is a rules answer: ``move_token`` paths around walls, through
        the door rather than the corner, and charges for the ground it crosses.
        A viewer interpolating a straight line between two squares draws a
        creature strolling through masonry — which was tolerable when walls were
        flat shading and is not, now that they stand up.

        Read off the append-only event log, which already records exactly this
        for every move including monsters', so nothing new has to be stored.
        """
        with Session(self.engine) as s:
            row = s.exec(
                select(MapEvent)
                .where(MapEvent.map_id == map_id, MapEvent.kind == "move")
                .order_by(MapEvent.id.desc())  # type: ignore[attr-defined]
            ).first()
        if row is None or not isinstance(row.payload, dict):
            return None
        path = row.payload.get("path") or []
        if len(path) < 2:
            return None
        return {"id": row.id, "token_id": row.payload.get("token_id"),
                "path": [[int(p[0]), int(p[1])] for p in path]}

    def skins_for(self, map_id: int) -> dict:
        """What this board is MADE of: ``{"codes": {...}, "squares": {...}}``.

        ``codes`` is derived from the archetype — a reef's rock is coral, every
        square of it, and nothing has to write that down three hundred times.
        ``squares`` holds only the exceptions the generator built. Both are pure
        looks: no rule reads a skin, and the renderers use them for material and
        silhouette alone. See :mod:`vtt.skins`.
        """
        from . import skins as _skins

        row = self.get_scene(map_id)
        if row is None:
            return {"codes": {}, "squares": {}}
        return {"codes": _skins.skins_for(row.archetype or "",
                                          style=row.board_style or ""),
                "squares": dict(row.skins or {})}

    def shells_for(self, map_id: int) -> list[dict]:
        """Every vessel SHELL on this board — one traced hull per body.

        A hull is bigger than a square, so it cannot be drawn a square at a
        time: joining the corners farthest from the middle needs the outline as
        a loop. It is traced HERE, once, and both renderers draw the answer
        rather than working one out — which is the only way to add geometry
        this shape without giving the depth map and the browser two chances to
        disagree. See :mod:`vtt.hull`.
        """
        from . import hull as _hull
        from . import skins as _skins

        row = self.get_scene(map_id)
        if row is None:
            return []
        codes = _skins.skins_for(row.archetype or "", style=row.board_style or "")
        squares = dict(row.skins or {})
        if not any(_skins.body_of(n) for n in
                   list(codes.values()) + list(squares.values())):
            return []                      # not a vessel; nothing to trace
        return _hull.shells(
            list(row.terrain or []),
            lambda c, x, z: _skins.skin_at(c, x, z, codes=codes, squares=squares),
            row.elevation or {})

    def materials_for(self, map_id: int) -> dict[str, int]:
        """The surface swatch for every tile code on this board, {code: id}.

        Looked up rather than stored, which is the one place this deliberately
        departs from ``object_art``. An object sprite is drawn for a board and
        recorded on it; a material belongs to the CATALOGUE — every dungeon
        floor in every session is the same swatch — so a per-board copy would
        be a second truth that goes stale the moment ``MATERIAL_REV`` is bumped
        and the old pictures are pruned out from under it.

        The lookup is memoised per process, so a board with eight tile kinds
        costs eight queries once and nothing thereafter.
        """
        row = self.get_scene(map_id)
        if row is None or self.image_store is None:
            return {}
        from imagery.models import ImageKind, context_key, slugify

        from .art import NO_MATERIAL, board_look, material_look, material_ref

        from . import skins as _skins

        look = board_look(row.biome or "", row.archetype or "")
        sk = self.skins_for(map_id)
        code_skins, square_skins = sk["codes"], sk["squares"]
        out: dict[str, int] = {}
        for lvl in range(len(self.levels_of(row))):
            g = self.grid_of(row, lvl)
            # Keyed by (code, skin) rather than code alone: a board can carry a
            # palisade AND canvas tents, both of them '#', and one swatch
            # between them would make the tents log walls. The client builds the
            # same key from the skins it is shipped.
            for x, y in g.squares():
                code = g.get(x, y)
                if code in NO_MATERIAL:
                    continue
                skin = _skins.skin_at(code, x, y, codes=code_skins,
                                      squares=square_skins) if lvl == 0 else \
                    str(code_skins.get(code) or "")
                slot = f"{code}@{skin}" if skin else code
                if slot in out:
                    continue
                # A look-agnostic surface is filed once, under "any" — lava is
                # lava wherever it is, and asking for a dungeon's lava would
                # miss a bucket that already holds the answer.
                bucket = material_look(code, skin) or look
                key = (slot, bucket)
                if key not in _MATERIAL_IDS:
                    found = self.image_store.list_for(
                        ImageKind.MATERIAL, slugify(material_ref(code, skin)),
                        context_key(bucket))
                    _MATERIAL_IDS[key] = found[0]["image_id"] if found else None
                got = _MATERIAL_IDS[key]
                if got:
                    out[slot] = got
        return out

    def render_objects(self, map_id: int, *, conditions: str = "") -> int:
        """Draw sprites for the object kinds on this board. Returns how many.

        Keyed by KIND, not by square: every pillar in the room is one picture,
        so a board with eight of them costs one render.
        """
        row = self.get_scene(map_id)
        if row is None:
            return 0
        g = self.grid_of(row)
        have = dict(row.object_art or {})
        ctx = ", ".join(p for p in ((row.biome or ""), conditions) if p)
        made = 0
        for code in {g.get(x, y) for x, y in g.squares()}:
            if not sprite_subject(code):
                continue
            name = tile(code).name
            if have.get(name):
                continue
            img = render_object(code, store=self.image_store, context=ctx)
            if img:
                have[name] = img
                made += 1
        if made:
            self._set_fields(map_id, object_art=have)
            self._bump(map_id)
        return made

    def debris_for(self, map_id: int) -> list[dict]:
        """Wreckage on this board, for the client to paint over the base art."""
        row = self.get_scene(map_id)
        out = []
        for key, entry in (row.debris or {}).items() if row else []:
            try:
                x, y = (int(v) for v in str(key).split(","))
            except ValueError:
                continue
            if isinstance(entry, dict):
                # Say what it WAS, not what it is. "rubble" on a square is the
                # complaint that debris appears from nowhere; "broken pillar"
                # is the answer, and it costs a word.
                was = short_name(str(entry.get("was") or "").strip())
                out.append({"x": x, "y": y,
                            "label": f"broken {was}" if was else "wreckage",
                            **entry})
        return out

    def breakables(self, map_id: int) -> list[dict]:
        """Every breakable square on the board, damaged or not."""
        row = self.get_scene(map_id)
        if row is None:
            return []
        g = self.grid_of(row)
        out = []
        for x, y in g.squares():
            obj = self.object_at(map_id, x, y)
            if obj:
                out.append({**obj, "x": x, "y": y})
        return out

    # ================================================================ tokens

    def add_token(self, map_id: int, name: str, *, kind: str = TokenKind.MONSTER,
                  team: str = Team.FOE, x: Optional[int] = None,
                  y: Optional[int] = None, size: str = "medium",
                  speed_ft: int = 30, reach_ft: int = 5,
                  combatant_id: Optional[int] = None,
                  character_id: Optional[int] = None,
                  monster_slug: Optional[str] = None,
                  image_id: Optional[int] = None, hidden: bool = False,
                  color: Optional[str] = None,
                  movement_mode: Optional[str] = None,
                  label: Optional[str] = None,
                  senses: Optional[dict] = None,
                  swim_speed_ft: Optional[int] = None,
                  elevation_ft: int = 0) -> Optional[MapToken]:
        """Put a creature (or an object) on the board.

        With no coordinates the token is dropped in its side's spawn zone, in
        the nearest legal square that nobody else is standing in.

        ``movement_mode`` defaults to the board's own medium: everything on an
        underwater board swims, everything on a sky board flies. A creature
        that moves differently from the board it stands on is the exception and
        says so explicitly.
        """
        row = self.get_scene(map_id)
        if row is None:
            return None
        movement_mode = movement_mode or self.board_mode(row)
        g = self.grid_of(row)
        n = size_squares(size)
        occupied = self._occupied(map_id)

        if x is None or y is None:
            spot = self._spawn_square(row, g, team, occupied, n)
            if spot is None:
                return None
            x, y = spot
        else:
            spot = geo.nearest_free(g, (int(x), int(y)), size=n,
                                    mode=movement_mode, blocked=occupied)
            if spot is None:
                return None
            x, y = spot

        tok = MapToken(
            map_id=map_id, name=name[:80], kind=kind, team=team,
            x=int(x), y=int(y), size=size, speed_ft=int(speed_ft),
            reach_ft=int(reach_ft), combatant_id=combatant_id,
            character_id=character_id, monster_slug=monster_slug,
            image_id=image_id, hidden=hidden, movement_mode=movement_mode,
            senses=(dict(senses) if senses else None),
            swim_speed_ft=swim_speed_ft,
            color=color or TEAM_COLORS.get(team), label=label,
            elevation_ft=int(elevation_ft),
        )
        with Session(self.engine) as s:
            s.add(tok)
            s.commit()
            s.refresh(tok)
        self._bump(map_id)
        self._log(map_id, row.session_id, "spawn", actor=name,
                  summary=f"{name} at {x},{y}",
                  payload={"token_id": tok.id, "x": x, "y": y, "team": team})
        return tok

    def sync_from_encounter(self, map_id: int, encounter_id: int, *,
                            rules_lib: Any = None,
                            portrait_lookup: Any = None) -> list[MapToken]:
        """Seat every combatant in a fight on the board (see :mod:`vtt.bridge`)."""
        from .bridge import seat_encounter
        return seat_encounter(self, map_id, encounter_id, tracker=self.tracker,
                              rules_lib=rules_lib, portrait_lookup=portrait_lookup)

    def get_token(self, token_id: int) -> Optional[MapToken]:
        with Session(self.engine) as s:
            return s.get(MapToken, token_id)

    def tokens(self, map_id: int, *, include_defeated: bool = True) -> list[MapToken]:
        with Session(self.engine) as s:
            rows = list(s.exec(select(MapToken).where(MapToken.map_id == map_id)).all())
        if not include_defeated:
            rows = [t for t in rows if not t.defeated]
        return sorted(rows, key=lambda t: (t.team != Team.PARTY, t.id or 0))

    def find_token(self, map_id: int, ref: str) -> Optional[MapToken]:
        """Resolve a token by name (exact, then prefix, then substring)."""
        ref_l = (ref or "").strip().lower()
        if not ref_l:
            return None
        rows = self.tokens(map_id)
        for t in rows:
            if t.name.lower() == ref_l:
                return t
        for t in rows:
            if t.name.lower().startswith(ref_l):
                return t
        for t in rows:
            if ref_l in t.name.lower():
                return t
        return None

    def token_for_combatant(self, map_id: int, combatant_id: int) -> Optional[MapToken]:
        with Session(self.engine) as s:
            return s.exec(select(MapToken).where(
                MapToken.map_id == map_id,
                MapToken.combatant_id == combatant_id)).first()

    def update_token(self, token_id: int, **fields) -> Optional[MapToken]:
        allowed = {"name", "team", "kind", "size", "speed_ft", "reach_ft",
                   "hidden", "prone", "defeated", "image_id", "color", "label",
                   "notes", "elevation_ft", "facing_deg", "movement_mode",
                   "moved_ft", "combatant_id", "character_id",
                   "restrained", "grappled_by", "senses",
                   "stealth_dc", "found_by", "swim_speed_ft",
                   "mounted_on", "squeezing", "level"}
        with Session(self.engine) as s:
            tok = s.get(MapToken, token_id)
            if not tok:
                return None
            for k, v in fields.items():
                if k in allowed:
                    setattr(tok, k, v)
            tok.updated_at = _now()
            s.add(tok)
            s.commit()
            s.refresh(tok)
            map_id = tok.map_id
        self._bump(map_id)
        return tok

    def remove_token(self, token_id: int) -> bool:
        with Session(self.engine) as s:
            tok = s.get(MapToken, token_id)
            if not tok:
                return False
            map_id, name = tok.map_id, tok.name
            s.delete(tok)
            s.commit()
        self._bump(map_id)
        self._log(map_id, None, "remove", actor=name, summary=f"{name} leaves the board")
        return True

    def _occupied(self, map_id: int, *, exclude: Optional[int] = None,
                  ignore_teams: Iterable[str] = (),
                  level: Optional[int] = None) -> set[Square]:
        """Every square a *blocking* token stands in.

        Defeated creatures and markers don't block; allies technically don't
        block passage in 5e but do block *stopping*, which is what this set is
        used for on placement.
        """
        out: set[Square] = set()
        skip = set(ignore_teams)
        for t in self.tokens(map_id):
            if t.id == exclude or t.defeated or t.kind == TokenKind.MARKER:
                continue
            if t.team in skip:
                continue
            # Floors don't share squares. Someone standing under the gallery
            # and someone standing on it are at the same x,y and are not in
            # each other's way at all.
            if level is not None and int(t.level or 0) != int(level):
                continue
            out.update(geo.footprint(t.x, t.y, size_squares(t.size)))
        return out

    @staticmethod
    def board_mode(row: Optional[TacticalMap]) -> str:
        """The medium this board is fought in: walk (ground), swim, or fly.
        Boards generated before the field existed are ground boards."""
        mode = ((row.notes or {}) if row is not None else {}).get("mode")
        return mode if mode in ("walk", "swim", "fly") else "walk"

    def _spawn_square(self, row: TacticalMap, g: Grid, team: str,
                      occupied: set[Square], n: int) -> Optional[Square]:
        notes = row.notes or {}
        mode = self.board_mode(row)
        key = "spawn_party" if team == Team.PARTY else "spawn_foes"
        zone = [tuple(s) for s in (notes.get(key) or [])]
        for sq in zone:
            if geo._fits(g, sq, n, mode=mode, blocked=occupied):  # type: ignore[arg-type]
                return sq  # type: ignore[return-value]
        # Zone full or unusable — fall back to anywhere legal, farthest from
        # the other side so the two groups don't start interleaved.
        others = [t for t in self.tokens(row.id) if t.team != team and not t.defeated]
        best: Optional[Square] = None
        best_score = -1.0
        for x, y in g.squares():
            if not geo._fits(g, (x, y), n, mode=mode, blocked=occupied):
                continue
            if not others:
                return (x, y)
            score = min(geo.distance_squares((x, y), (o.x, o.y)) for o in others)
            if score > best_score:
                best, best_score = (x, y), score
        return best

    # ---- movement ----------------------------------------------------------

    def movement_options(self, token_id: int, budget_ft: Optional[int] = None,
                         *, dash: bool = False) -> dict:
        """Every square this token could reach with its remaining movement.

        Feeds the overlay's blue "where can I go" wash and its path preview.
        ``dash`` adds the creature's speed again, for previewing the Dash action.
        """
        tok = self.get_token(token_id)
        if not tok:
            return {}
        row = self.get_scene(tok.map_id)
        if row is None:
            return {}
        # A creature reaches across ITS OWN storey. Costing the wash against the
        # ground floor put the archer on the gallery a walk through the hall's
        # furniture, and `move_token` — which has always been level-aware —
        # then refused the move the wash had just offered.
        lvl = int(tok.level or 0)
        g = self.grid_of(row, lvl)
        budget = int(budget_ft if budget_ft is not None
                     else max(0, tok.speed_ft * (2 if dash else 1) - tok.moved_ft))
        blocked = self._occupied(tok.map_id, exclude=token_id, level=lvl,
                                 ignore_teams=(tok.team,) if tok.team else ())
        hard = self._occupied(tok.map_id, exclude=token_id, level=lvl)
        costs = geo.reachable_costs(
            g, (tok.x, tok.y), budget, size=size_squares(tok.size),
            mode=tok.movement_mode, blocked=blocked,
            extra_cost=self._effect_cost_fn(tok.map_id, tok.movement_mode, row),
            square_ft=row.square_ft)
        return {
            "token_id": token_id,
            "budget_ft": budget,
            "level": lvl,
            "squares": [{"x": x, "y": y, "cost": c} for (x, y), c in costs.items()
                        if (x, y) not in hard or (x, y) == (tok.x, tok.y)],
            "threatened": [{"x": x, "y": y} for x, y in
                           sorted(self.threatened_squares(tok.map_id, tok))],
        }

    def threatened_squares(self, map_id: int, tok: MapToken) -> set[Square]:
        """Every square where leaving would provoke, for this creature.

        The opportunity-attack warning already exists, but it only lands after
        the path preview round-trips — you learn the route provokes once the
        pointer has settled on the far end of it. A creature deciding where to
        go needs to see the threatened ground the way it sees the walls: before
        choosing, not after. Same rule the engine applies in ``_opportunity``,
        asked of the ground instead of a path.
        """
        out: set[Square] = set()
        row = self.get_scene(map_id)
        if row is None:
            return out
        lvl = int(tok.level or 0)
        g = self.grid_of(row, lvl)
        square_ft = max(1, int(row.square_ft or 5))
        for other in self.tokens(map_id):
            # The same exclusions _opportunity makes, for the same reasons: a
            # corpse, a marker and a crate take no reactions, and an ally's
            # reach is not a threat.
            if other.id == tok.id or other.defeated or other.team == tok.team:
                continue
            if other.kind in (TokenKind.MARKER, TokenKind.OBJECT):
                continue
            if int(other.level or 0) != lvl:
                continue
            reach = other.reach_ft or 5
            # Height alone can put the mover out of reach — the wyvern rule
            # from _opportunity. This is measured against the MOVER's height,
            # which is the same everywhere on its own storey.
            if self.height_gap_ft(row, other, tok) > reach:
                continue
            reach_sq = max(1, (reach + square_ft - 1) // square_ft)
            for sx, sy in geo.footprint(other.x, other.y, size_squares(other.size)):
                for dy in range(-reach_sq, reach_sq + 1):
                    for dx in range(-reach_sq, reach_sq + 1):
                        sq = (sx + dx, sy + dy)
                        if g.in_bounds(*sq):
                            out.add(sq)
        return out

    def path_preview(self, token_id: int, x: int, y: int) -> dict:
        """The route a token would take to a square, and what it would cost."""
        tok = self.get_token(token_id)
        if not tok:
            return {"ok": False, "reason": "no such token"}
        row = self.get_scene(tok.map_id)
        if row is None:
            return {"ok": False, "reason": "no board"}
        lvl = int(tok.level or 0)
        g = self.grid_of(row, lvl)
        blocked = self._occupied(tok.map_id, exclude=token_id, level=lvl,
                                 ignore_teams=(tok.team,) if tok.team else ())
        path, cost = geo.find_path(
            g, (tok.x, tok.y), (int(x), int(y)), size=size_squares(tok.size),
            mode=tok.movement_mode, blocked=blocked,
            extra_cost=self._effect_cost_fn(tok.map_id, tok.movement_mode, row),
            square_ft=row.square_ft)
        if not path:
            return {"ok": False, "reason": "no route to that square"}
        remaining = max(0, tok.speed_ft - tok.moved_ft)
        return {"ok": True, "path": [list(p) for p in path], "cost_ft": cost,
                "level": lvl,
                "remaining_ft": remaining, "within_budget": cost <= remaining,
                "opportunity": [t["name"] for t in
                                self._opportunity(tok, path, row)]}

    def move_token(self, token_id: int, x: int, y: int, *, teleport: bool = False,
                   enforce_speed: bool = True, free: bool = False,
                   bonus_ft: int = 0) -> dict:
        """Move a token, pathing around walls and charging for the ground.

        Returns a result dict the caller narrates:
        ``{ok, path, cost_ft, remaining_ft, opportunity[], hazards[], entered[]}``.
        Rejections come back with ``ok=False`` and a player-facing ``reason`` —
        the same contract the combat engine uses, so an illegal move is a
        correction, not a silently-applied cheat.

        ``bonus_ft`` widens the budget for this move alone (a Dash adds the
        creature's speed); ``free`` doesn't charge the board at all, for moves
        the combat engine has already paid for in its own economy.
        """
        tok = self.get_token(token_id)
        if not tok:
            return {"ok": False, "reason": "no such token"}
        row = self.get_scene(tok.map_id)
        if row is None or not row.active:
            return {"ok": False, "reason": "no board is out"}
        lvl = int(tok.level or 0)
        g = self.grid_of(row, lvl)
        n = size_squares(tok.size)
        dest = (int(x), int(y))
        hard_blocked = self._occupied(tok.map_id, exclude=token_id, level=lvl)
        soft_blocked = self._occupied(tok.map_id, exclude=token_id, level=lvl,
                                      ignore_teams=(tok.team,) if tok.team else ())

        if not g.in_bounds(*dest):
            return {"ok": False, "reason": "that square is off the map"}
        # Checked before the terrain, because it is the more fundamental
        # refusal: a rider has no movement of their own, and telling them the
        # destination is a crate answers a question they weren't asking.
        if tok.mounted_on and not teleport:
            return {"ok": False, "mounted_on": tok.mounted_on,
                    "reason": (f"{tok.name} is riding {tok.mounted_on} — move "
                               f"{tok.mounted_on} instead, or get down first "
                               f"([[VTT: dismount | {tok.name}]])")}
        squeezing = False
        if not geo._fits(g, dest, n, mode=tok.movement_mode, blocked=hard_blocked):
            # A creature can force itself into a space one size category
            # smaller. Checked BEFORE the refusal below, because "can't stand
            # there" is the wrong answer for a corridor a Large creature could
            # get down and shoulder through — it just costs, and hurts.
            if n > 1 and geo._fits(g, dest, n - 1, mode=tok.movement_mode,
                                   blocked=hard_blocked):
                squeezing = True
            else:
                # Say WHY, and say what would fix it. A bare "blocked" makes the
                # narration guess, and it guesses wrong: water and a wall are not
                # the same refusal, and one of them has a remedy.
                need = required_mode(g.get(*dest))
                if need and need != tok.movement_mode:
                    verb = "swim" if need == "swim" else "fly"
                    return {"ok": False, "needs_mode": need,
                            "reason": (f"{g.tile_at(*dest).name} — {tok.name} would "
                                       f"have to {verb} to be there "
                                       f"([[VTT: token | {tok.name} | {need}]])")}
                return {"ok": False,
                        "reason": (f"{tok.name} can't stand there — "
                                   f"{g.tile_at(*dest).name}")}

        # Held fast means Speed 0, and Speed 0 is a movement rule — so it is
        # enforced here rather than left to the DM to remember. A teleport still
        # works: a grapple stops you WALKING away, not blinking out of it. Being
        # shoved works too, which is what ``shove`` is for.
        if enforce_speed and not free and not teleport:
            if tok.restrained:
                return {"ok": False, "reason": f"{tok.name} is restrained — "
                                               f"their Speed is 0"}
            if tok.grappled_by:
                return {"ok": False,
                        "reason": (f"{tok.name} is grappled by {tok.grappled_by} "
                                   f"— their Speed is 0 until they break free")}

        if teleport:
            path, cost = [(tok.x, tok.y), dest], 0
        else:
            # Squeezing paths as the smaller creature it is making itself
            # into — pathing at full size would report "no way through" for
            # the very corridor it is shouldering into.
            path, cost = geo.find_path(
                g, (tok.x, tok.y), dest,
                size=(n - 1 if squeezing else n), mode=tok.movement_mode,
                blocked=soft_blocked,
                extra_cost=self._effect_cost_fn(tok.map_id, tok.movement_mode, row),
                square_ft=row.square_ft)
            if not path and n > 1 and not squeezing:
                # The destination fits; something on the WAY doesn't. That is
                # the ordinary case — a Large creature standing in a hall,
                # crossing to another hall through one narrow door — and
                # checking only the destination would report "no way through"
                # for a gap it can plainly shoulder into.
                path, cost = geo.find_path(
                    g, (tok.x, tok.y), dest, size=n - 1,
                    mode=tok.movement_mode, blocked=soft_blocked,
                    extra_cost=self._effect_cost_fn(tok.map_id,
                                                    tok.movement_mode, row),
                    square_ft=row.square_ft)
                squeezing = bool(path)
            if not path:
                return {"ok": False,
                        "reason": f"there's no way through to that square"}
            # Crawling costs an extra foot for every foot — so, twice.
            crawling = tok.prone and tok.movement_mode == "walk"
            if crawling:
                cost *= 2
            # And so does squeezing. They stack, because they are two separate
            # extra feet and a Large creature crawling through a gap is having
            # a genuinely terrible turn.
            if squeezing:
                cost *= 2
            # Dragging a captive halves the hauler's speed.
            dragged = self._captives_of(tok)
            budget = tok.speed_ft + max(0, int(bonus_ft))
            if dragged:
                budget //= 2
            remaining = max(0, budget - tok.moved_ft)
            if enforce_speed and not free and cost > remaining:
                why = (f"that's {cost} ft"
                       + (" (crawling costs double)" if crawling else "")
                       + (" (squeezing costs double)" if squeezing else "")
                       + f" and {tok.name} has {remaining} ft of movement left"
                       + (f" while hauling {', '.join(d.name for d in dragged)}"
                          if dragged else ""))
                return {"ok": False, "cost_ft": cost, "remaining_ft": remaining,
                        "reason": why}

        oa = [] if teleport else self._opportunity(tok, path, row)
        hazards = self._hazards_on(row, g, path[1:] if len(path) > 1 else [])
        entered = self._effects_at(tok.map_id, dest, n)

        # A square that DEMANDS a medium puts the creature in it. Only ever
        # into, never back out: adopting a medium can unblock a creature, and
        # dropping one could strand a flier that landed on a ledge. Swimming or
        # flying over ordinary ground behaves like walking anyway (see
        # Grid.cost), so the one-way rule costs nothing.
        adopted = required_mode(g.get(*dest))
        with Session(self.engine) as s:
            live = s.get(MapToken, token_id)
            if not live:
                return {"ok": False, "reason": "no such token"}
            live.x, live.y = dest
            if adopted and live.movement_mode != adopted:
                live.movement_mode = adopted
            if not free and not teleport:
                live.moved_ft = min(live.speed_ft * 4, live.moved_ft + cost)
            live.updated_at = _now()
            s.add(live)
            s.commit()
            s.refresh(live)
        self._bump(tok.map_id)
        self.recompute_auras(tok.map_id)
        if row.fog:
            self.reveal_from_party(tok.map_id)

        self._log(tok.map_id, row.session_id, "move", actor=tok.name,
                  summary=f"{tok.name} -> {dest[0]},{dest[1]} ({cost} ft)",
                  payload={"token_id": token_id, "path": [list(p) for p in path],
                           "cost_ft": cost})

        remaining = max(0, tok.speed_ft + max(0, int(bonus_ft))
                        - (tok.moved_ft + (0 if free or teleport else cost)))
        # Remembered, not recomputed: the combat engine asks whether this
        # creature is squeezing when it rolls an attack, which is a different
        # moment from the one that decided it.
        if bool(tok.squeezing) != squeezing:
            self.update_token(tok.id, squeezing=squeezing)
        out = {"ok": True, "path": [list(p) for p in path], "cost_ft": cost,
               "x": dest[0], "y": dest[1], "remaining_ft": remaining,
               "opportunity": oa, "hazards": hazards,
               "entered": [e["name"] for e in entered]}
        if adopted and tok.movement_mode != adopted:
            out["mode"] = adopted
        # Anyone this creature has hold of comes along, and anyone holding IT
        # either follows or loses their grip (checked from the new position).
        towed = self._tow_captives(tok, dest, row, g)
        if towed:
            out["dragged"] = towed
        # A rider goes exactly where their mount goes — same square, no save,
        # no separate step. This is not the captive-dragging above: a captive
        # is hauled to a square NEXT to its hauler, a rider is IN the saddle.
        carried = self._rider_of(tok)
        if carried is not None:
            self._place(carried.id, dest)
            out["carried"] = carried.name
        broke = self._break_far_grapples(tok.map_id, row)
        if broke:
            out["grapples_broken"] = broke
        # Walking into the open ends hiding, and the board is the only thing
        # that can notice. Re-asking eligibility from the NEW square is the
        # whole check: if there is still cover or darkness enough to have hidden
        # here, the creature stays hidden; if it just stepped into a lit
        # corridor in plain view of the guard, it does not.
        if tok.hidden and not self.hide_eligibility(tok.map_id, tok.name)["ok"]:
            self.unhide(tok.map_id, tok.name, "stepped into the open")
            out["hiding_broken"] = True
        # Stepping off a ledge is a fact the DM should narrate (and charge for);
        # the board reports the drop rather than silently applying damage.
        drop = self._drop_ft(row, (tok.x, tok.y), dest)
        if drop >= 10 and tok.movement_mode != "fly":
            out["fall_ft"] = drop
        return out

    def _strength_of(self, t: MapToken) -> int:
        """A creature's Strength score, best effort — the senses precedent.

        The rules tables are a separate lifecycle from the board, so this
        degrades rather than refuses: an unknown creature is a 10, which is a
        ten-foot running jump and the plainest answer there is.
        """
        try:
            if t.monster_slug:
                from rules.query import RulesLibrary
                m = RulesLibrary(engine=self.engine).get_monster(t.monster_slug)
                if m is not None and m.strength:
                    return int(m.strength)
            if t.character_id:
                from sqlmodel import text as _t
                with Session(self.engine) as s:
                    row = s.exec(_t("SELECT stats FROM character WHERE id = :i")
                                 .bindparams(i=int(t.character_id))).first()
                if row and row[0]:
                    import json
                    stats = row[0] if isinstance(row[0], dict) else json.loads(row[0])
                    got = stats.get("strength") or stats.get("STR")
                    if got:
                        return int(got)
        except Exception as e:
            print(f"[vtt] strength lookup failed for {t.name}: {e}")
        return 10

    def jump_reach_ft(self, token_id: int, *, running: bool = True) -> dict:
        """How far and how high this creature can jump, in feet.

        The SRD's numbers, which are unusually concrete: a running long jump
        clears your STRENGTH SCORE in feet, a standing one half that; a running
        high jump is 3 + your Strength modifier, a standing one half. Running
        means ten feet of movement first, which the board checks rather than
        assumes.
        """
        t = self.get_token(token_id)
        if t is None:
            return {"long_ft": 0, "high_ft": 0}
        score = self._strength_of(t)
        mod = (score - 10) // 2
        long_ft = score if running else score // 2
        high_ft = max(1, 3 + mod) if running else max(1, (3 + mod) // 2)
        return {"long_ft": int(long_ft), "high_ft": int(high_ft),
                "strength": score, "running": bool(running)}

    def jump(self, token_id: int, x: int, y: int, *,
             enforce_speed: bool = True, dry_run: bool = False,
             frm: Optional[Square] = None,
             moved_ft: Optional[int] = None) -> dict:
        """Leap a gap in a straight line. The one way to cross what you cannot walk.

        Boards grew chasms, channels ten feet deep, ledges and stacked
        terraces, and there was no rule for going OVER any of it — a creature
        could climb (a foot per foot) or walk round, and that is not what a
        player does when a channel is in the way.

        The squares CROSSED are not checked, which is the whole point of a
        jump: what matters is the landing. The landing square must be somewhere
        this creature could stand, empty, and no higher above the take-off than
        a high jump reaches — a ten-foot ledge is a CLIMB, not a hop, and
        pretending otherwise would quietly delete the climb rule. Landing lower
        is legal and reports the drop exactly as stepping off does.

        Costs movement equal to the distance jumped, because a jump is movement
        (it is not a free way to cross difficult ground).

        ``frm`` and ``moved_ft`` ask the question from a square the creature is
        not standing on yet, and are only legal with ``dry_run``. That is what a
        planner needs: whether a leap is worth taking depends on where you take
        off from and how much of a run you had, so choosing one means asking
        about a fan of take-offs BEFORE walking to any of them. Answering it by
        re-deriving the rules on the planner's side is how two copies of a rule
        start disagreeing.
        """
        t = self.get_token(token_id)
        if t is None:
            return {"ok": False, "reason": "no such token"}
        row = self.get_scene(t.map_id)
        if row is None or not row.active:
            return {"ok": False, "reason": "no board is out"}
        if (t.movement_mode or "walk") == "fly":
            return {"ok": False, "reason": f"{t.name} is flying — no need to jump"}
        if not self.grid_of(row, int(t.level or 0)).in_bounds(x, y):
            return {"ok": False, "reason": "off the board"}

        ox, oy = (frm if (frm and dry_run) else (t.x, t.y))
        dx, dy = x - ox, y - oy
        if dx == 0 and dy == 0:
            return {"ok": False, "reason": "already there"}
        # A jump is a straight line: along a row, a column or a true diagonal.
        if dx and dy and abs(dx) != abs(dy):
            return {"ok": False,
                    "reason": f"{t.name} can only jump in a straight line"}

        moved = int(moved_ft if (moved_ft is not None and dry_run)
                    else (t.moved_ft or 0))
        running = moved >= 10
        reach = self.jump_reach_ft(token_id, running=running)
        dist_ft = geo.distance_ft((ox, oy), (x, y), row.square_ft)
        if dist_ft > reach["long_ft"]:
            how = "running" if running else "standing"
            return {"ok": False,
                    "reason": (f"{t.name} can clear {reach['long_ft']} ft with a "
                               f"{how} jump, and that is {dist_ft} ft"
                               + ("" if running else
                                  " — 10 ft of movement first makes it a running jump"))}

        rise = self._height_at(row, (x, y)) - self._height_at(row, (ox, oy))
        if rise > reach["high_ft"]:
            return {"ok": False,
                    "reason": (f"{t.name} can jump {reach['high_ft']} ft up and that "
                               f"landing is {rise} ft higher — it has to be climbed")}

        grid = self.grid_of(row, int(t.level or 0))
        mode = t.movement_mode or self.board_mode(row)
        if not grid.passable(x, y, mode=mode):
            return {"ok": False,
                    "reason": f"{t.name} cannot land on {x},{y}"}
        if (x, y) in self._occupied(t.map_id, exclude=t.id,
                                    level=int(t.level or 0)):
            return {"ok": False, "reason": f"{x},{y} is occupied"}
        if enforce_speed and dist_ft > max(0, int(t.speed_ft or 30) - moved):
            return {"ok": False,
                    "reason": (f"{t.name} has {max(0, int(t.speed_ft or 30) - moved)} ft "
                               f"of movement left and the jump is {dist_ft} ft")}

        # `dry_run` runs every check above and lands nobody. Anything CHOOSING
        # a jump has to be able to ask whether one is legal before committing —
        # the monster planner tries a fan of landings and takes the best — and
        # the alternative is a second copy of these rules somewhere that would
        # drift from them.
        if dry_run:
            return {"ok": True, "x": x, "y": y, "distance_ft": dist_ft,
                    "running": running, "cleared": reach["long_ft"],
                    "dry_run": True}

        self._place(t.id, (x, y))
        self.update_token(t.id, moved_ft=moved + dist_ft)
        out = {"ok": True, "x": x, "y": y, "distance_ft": dist_ft,
               "running": running, "cleared": reach["long_ft"],
               "detail": (f"{t.name} {'takes a run and clears' if running else 'jumps'} "
                          f"{dist_ft} ft to {x},{y}")}
        drop = -rise
        if drop >= 10:
            out["fall_ft"] = drop
        self._log(t.map_id, row.session_id, "jump", actor=t.name,
                  summary=out["detail"],
                  payload={"token_id": token_id, "to": [x, y], "ft": dist_ft})
        self._bump(t.map_id)
        return out

    def blink(self, token_id: int, x: int, y: int, *,
              self_range_ft: int = 10, ally_range_ft: int = 5,
              ally_within_ft: int = 30, cost_fraction: float = 0.5,
              require_link: bool = True) -> dict:
        """A short teleport, either near yourself or beside a LINKED creature.

        The shape a "step through your own maps" feature wants: hop a little
        way on your own, or a long way if a bonded ally is standing where you
        want to be. Distinct from ``move_token(teleport=True)`` in three ways —
        it is range-checked against real board distance, it CHARGES movement
        (a teleport that costs nothing is a different feature), and its long
        form is gated on the link rather than on line of sight.

        Returns the move result, or ``{"ok": False, "reason": ...}``.
        """
        tok = self.get_token(token_id)
        if not tok:
            return {"ok": False, "reason": "no such token"}
        row = self.get_scene(tok.map_id)
        if row is None or not row.active:
            return {"ok": False, "reason": "no board is out"}
        if tok.speed_ft <= 0:
            return {"ok": False, "reason": f"{tok.name} has no speed to spend"}

        dest = (int(x), int(y))
        sq = row.square_ft or 5
        near_self = geo.distance_ft((tok.x, tok.y), dest, square_ft=sq)
        anchor = None
        if near_self > self_range_ft:
            # Look for a linked creature standing beside the destination, close
            # enough to this one to step through.
            for other in self.tokens(tok.map_id, include_defeated=False):
                if other.id == token_id:
                    continue
                if geo.distance_ft((other.x, other.y), dest, square_ft=sq) > ally_range_ft:
                    continue
                if geo.distance_ft((tok.x, tok.y), (other.x, other.y),
                                   square_ft=sq) > ally_within_ft:
                    continue
                if require_link and not self._is_linked(tok.map_id, tok.name, other.name):
                    continue
                anchor = other
                break
            if anchor is None:
                return {"ok": False,
                        "reason": (f"that's {near_self} ft — {tok.name} can step "
                                   f"{self_range_ft} ft alone, or beside a linked "
                                   f"ally within {ally_within_ft} ft")}

        cost = max(sq, int((tok.speed_ft * max(0.0, cost_fraction)) // sq * sq))
        remaining = max(0, tok.speed_ft - tok.moved_ft)
        if cost > remaining:
            return {"ok": False, "cost_ft": cost, "remaining_ft": remaining,
                    "reason": (f"stepping through costs {cost} ft and "
                               f"{tok.name} has {remaining} ft left")}

        res = self.move_token(token_id, dest[0], dest[1], teleport=True)
        if not res.get("ok"):
            return res
        # move_token charges nothing for a teleport, so the cost is applied here
        # — this one is paid for out of the creature's movement.
        with Session(self.engine) as s:
            live = s.get(MapToken, token_id)
            if live:
                live.moved_ft = min(live.speed_ft * 4, live.moved_ft + cost)
                s.add(live)
                s.commit()
        res.update({"cost_ft": cost,
                    "remaining_ft": max(0, tok.speed_ft - tok.moved_ft - cost),
                    "through": anchor.name if anchor else None})
        self._log(tok.map_id, row.session_id, "move", actor=tok.name,
                  summary=(f"{tok.name} steps through to {dest[0]},{dest[1]}"
                           + (f" beside {anchor.name}" if anchor else "")),
                  payload={"token_id": token_id, "blink": True,
                           "through": anchor.name if anchor else None})
        return res

    # ----- grappling, going prone, and changing places -------------------

    def _captives_of(self, tok: MapToken) -> list[MapToken]:
        """Everyone this creature currently has hold of."""
        name = (tok.name or "").strip().lower()
        if not name:
            return []
        return [t for t in self.tokens(tok.map_id, include_defeated=False)
                if t.id != tok.id
                and (t.grappled_by or "").strip().lower() == name]

    def _place(self, token_id: int, sq: Square) -> None:
        """Set a token's square directly, bypassing the movement rules.

        For the handful of things that ARE placements rather than movement —
        swapping, mounting, being thrown from the saddle. update_token refuses
        x/y precisely so this cannot happen by accident, so every caller of
        this is a deliberate exception and should say why.
        """
        with Session(self.engine) as s:
            live = s.get(MapToken, token_id)
            if live:
                live.x, live.y = int(sq[0]), int(sq[1])
                live.updated_at = _now()
                s.add(live)
                s.commit()

    def _tow_captives(self, tok: MapToken, dest: Square, row: TacticalMap,
                      g: Grid) -> list[str]:
        """Drag anyone this creature holds into its wake. Returns their names.

        Each captive is put in the nearest free square adjacent to where the
        hauler ended up — the leash is short, and a captive left behind would
        silently break the grapple the mover was trying to keep.
        """
        towed: list[str] = []
        for cap in self._captives_of(tok):
            n = size_squares(cap.size)
            blocked = self._occupied(tok.map_id, exclude=cap.id)
            spot = None
            ring = [(dest[0] + dx, dest[1] + dy)
                    for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    if (dx or dy)]
            for sq in sorted(ring,
                             key=lambda s: geo.distance_squares((cap.x, cap.y), s)):
                if g.in_bounds(*sq) and geo._fits(g, sq, n, mode=cap.movement_mode,
                                                  blocked=blocked):
                    spot = sq
                    break
            if spot is None:
                continue
            with Session(self.engine) as s:
                live = s.get(MapToken, cap.id)
                if live:
                    live.x, live.y = spot
                    live.updated_at = _now()
                    s.add(live)
                    s.commit()
            towed.append(cap.name)
        if towed:
            self._bump(tok.map_id)
        return towed

    def _break_far_grapples(self, map_id: int, row: TacticalMap) -> list[str]:
        """A grapple ends when the pair are no longer within reach."""
        toks = {t.id: t for t in self.tokens(map_id)}
        by_name = {(t.name or "").strip().lower(): t for t in toks.values()}
        broken: list[str] = []
        for t in list(toks.values()):
            holder = by_name.get((t.grappled_by or "").strip().lower()) \
                if t.grappled_by else None
            if not t.grappled_by:
                continue
            if holder is None or holder.defeated:
                self.update_token(t.id, grappled_by=None)
                broken.append(t.name)
                continue
            gap = geo.token_distance_ft(
                geo.footprint(holder.x, holder.y, size_squares(holder.size)),
                geo.footprint(t.x, t.y, size_squares(t.size)),
                square_ft=row.square_ft or 5,
                dz_ft=self.height_gap_ft(row, holder, t))
            if gap > max(5, holder.reach_ft):
                self.update_token(t.id, grappled_by=None)
                broken.append(t.name)
        return broken

    # =============================================================== mounts

    #: Creature sizes in order, so "at least one size larger" is arithmetic.
    _SIZE_ORDER = ("tiny", "small", "medium", "large", "huge", "gargantuan")

    def _rider_of(self, mount: MapToken) -> Optional[MapToken]:
        """Who is on this mount, if anyone."""
        want = (mount.name or "").strip().lower()
        for t in self.tokens(mount.map_id):
            if (t.mounted_on or "").strip().lower() == want:
                return t
        return None

    def mount(self, map_id: int, rider_ref: str, mount_ref: str) -> dict:
        """Get on. Costs half the rider's Speed, and needs a big enough animal.

        A mount must be at least one size larger than its rider — you do not
        ride a wolf — and the two then share the mount's space, which is why
        the rider keeps no position of its own. Getting on costs half your
        Speed, so a mount in the middle of a fight is a real decision rather
        than a free repositioning.
        """
        r = self.find_token(map_id, rider_ref)
        m = self.find_token(map_id, mount_ref)
        row = self.get_scene(map_id)
        if not (r and m and row):
            return {"ok": False, "reason": "no such creature on this board"}
        if r.id == m.id:
            return {"ok": False, "reason": "a creature can't ride itself"}
        if r.mounted_on:
            return {"ok": False, "reason": f"{r.name} is already mounted"}
        existing = self._rider_of(m)
        if existing is not None:
            return {"ok": False,
                    "reason": f"{m.name} is already carrying {existing.name}"}
        order = self._SIZE_ORDER
        try:
            if order.index((m.size or "medium").lower()) <= \
                    order.index((r.size or "medium").lower()):
                return {"ok": False,
                        "reason": (f"{m.name} is {m.size} — a mount has to be at "
                                   f"least one size larger than its rider, and "
                                   f"{r.name} is {r.size}")}
        except ValueError:
            pass
        gap = geo.token_distance_ft(
            geo.footprint(r.x, r.y, size_squares(r.size)),
            geo.footprint(m.x, m.y, size_squares(m.size)),
            square_ft=row.square_ft or 5,
            dz_ft=self.height_gap_ft(row, r, m))
        if gap > 5:
            return {"ok": False,
                    "reason": f"{m.name} is {gap} ft away — step alongside first"}
        if r.grappled_by or r.restrained:
            return {"ok": False,
                    "reason": f"{r.name} is held fast and can't climb up"}
        cost = max(5, int(r.speed_ft or 30) // 2)
        self.update_token(r.id, mounted_on=m.name, prone=False,
                          moved_ft=int(r.moved_ft or 0) + cost)
        # Rider and mount share the mount's space, so there is no second
        # position to keep in step. Written to the row directly because
        # update_token refuses x/y on purpose — nothing may sidestep the
        # movement rules by editing a position.
        self._place(r.id, (m.x, m.y))
        self._bump(map_id)
        self._log(map_id, row.session_id, "condition", actor=r.name,
                  summary=f"{r.name} mounts {m.name}")
        return {"ok": True, "cost_ft": cost,
                "detail": (f"{r.name} swings up onto {m.name} ({cost} ft of "
                           f"movement). They move as one — direct {m.name}.")}

    def dismount(self, map_id: int, rider_ref: str, *, forced: str = "") -> dict:
        """Get off. Half Speed by choice; free and PRONE when you're thrown."""
        r = self.find_token(map_id, rider_ref)
        row = self.get_scene(map_id)
        if not (r and row):
            return {"ok": False, "reason": "no such creature on this board"}
        if not r.mounted_on:
            return {"ok": False, "reason": f"{r.name} isn't mounted"}
        m = self.find_token(map_id, r.mounted_on)
        g = self.grid_of(row)
        n = size_squares(r.size)
        blocked = self._occupied(map_id, exclude=r.id)
        spot = (r.x, r.y)
        if m is not None:
            ring = [(m.x + dx, m.y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                    if (dx or dy)]
            spot = next((sq for sq in ring
                         if g.in_bounds(*sq)
                         and geo._fits(g, sq, n, mode=r.movement_mode,
                                       blocked=blocked)), (r.x, r.y))
        fields: dict = {"mounted_on": None}
        if forced:
            fields["prone"] = True
        else:
            fields["moved_ft"] = int(r.moved_ft or 0) + max(5, int(r.speed_ft or 30) // 2)
        self.update_token(r.id, **fields)
        self._place(r.id, spot)
        self._bump(map_id)
        self._log(map_id, row.session_id, "condition", actor=r.name,
                  summary=f"{r.name} dismounts" + (f" ({forced})" if forced else ""))
        if forced:
            return {"ok": True, "thrown": True,
                    "detail": (f"{r.name} is thrown from "
                               f"{m.name if m else 'the saddle'} ({forced}) and "
                               f"lands prone at {spot[0]},{spot[1]}.")}
        return {"ok": True,
                "detail": (f"{r.name} drops from "
                           f"{m.name if m else 'the saddle'} at "
                           f"{spot[0]},{spot[1]} (half Speed).")}

    def _stay_in_saddle(self, mount: MapToken, reason: str, *,
                        dc: int = 10, rng=None) -> Optional[dict]:
        """A rider makes a DC 10 Dex save when their mount is moved against its
        will, or when either of them is knocked down. Failure means the ground.

        Rolled here rather than left to the narration for the same reason the
        Stealth check is: the board is what knows the mount was shoved.
        """
        r = self._rider_of(mount)
        if r is None:
            return None
        from dice.mechanics import saving_throw
        # A flat d20: the board doesn't hold ability scores, and inventing a
        # modifier would be worse than admitting there isn't one. The DM can
        # re-roll with the rider's real Dex if it matters.
        save = saving_throw(0, dc=dc,
                            label=f"{r.name} stays in the saddle (Dex)", rng=rng)
        if save.success:
            return {"rider": r.name, "stayed": True, "roll": save.total,
                    "detail": (f"{r.name} keeps their seat as {mount.name} "
                               f"{reason} (Dex {save.total} vs DC {dc}).")}
        thrown = self.dismount(mount.map_id, r.name, forced=reason)
        return {"rider": r.name, "stayed": False, "roll": save.total,
                "detail": (f"{r.name} is thrown as {mount.name} {reason} "
                           f"(Dex {save.total} vs DC {dc}). ")
                          + str(thrown.get("detail", ""))}

    def grapple(self, map_id: int, grappler_ref: str, target_ref: str) -> dict:
        """One creature takes hold of another. Both must be within reach."""
        a = self.find_token(map_id, grappler_ref)
        b = self.find_token(map_id, target_ref)
        row = self.get_scene(map_id)
        if not (a and b and row):
            return {"ok": False, "reason": "no such creature on this board"}
        if a.id == b.id:
            return {"ok": False, "reason": "a creature can't grapple itself"}
        gap = geo.token_distance_ft(
            geo.footprint(a.x, a.y, size_squares(a.size)),
            geo.footprint(b.x, b.y, size_squares(b.size)),
            square_ft=row.square_ft or 5,
            dz_ft=self.height_gap_ft(row, a, b))
        if gap > max(5, a.reach_ft):
            return {"ok": False, "reason": (f"{b.name} is {gap} ft away — out of "
                                            f"{a.name}'s reach")}
        self.update_token(b.id, grappled_by=a.name)
        self._log(map_id, row.session_id, "condition", actor=a.name,
                  summary=f"{a.name} grapples {b.name}")
        return {"ok": True, "detail": f"{a.name} has hold of {b.name} — "
                                      f"{b.name}'s Speed is 0."}

    def release(self, map_id: int, target_ref: str) -> dict:
        """Let a creature go (they broke free, or the grappler let go)."""
        b = self.find_token(map_id, target_ref)
        if not b:
            return {"ok": False, "reason": "no such creature"}
        if not b.grappled_by:
            return {"ok": False, "reason": f"{b.name} isn't being held"}
        who = b.grappled_by
        self.update_token(b.id, grappled_by=None)
        return {"ok": True, "detail": f"{b.name} breaks free of {who}."}

    def set_restrained(self, map_id: int, ref: str, on: bool = True) -> dict:
        b = self.find_token(map_id, ref)
        if not b:
            return {"ok": False, "reason": "no such creature"}
        self.update_token(b.id, restrained=bool(on))
        return {"ok": True,
                "detail": (f"{b.name} is restrained — Speed 0." if on
                           else f"{b.name} is no longer restrained.")}

    def go_prone(self, map_id: int, ref: str) -> dict:
        b = self.find_token(map_id, ref)
        if not b:
            return {"ok": False, "reason": "no such creature"}
        # Going down while mounted means going down OFF the mount. Rolled
        # rather than assumed, because 5e gives you the save either way round:
        # the mount falling and the rider falling are the same question.
        if b.mounted_on:
            thrown = self.dismount(map_id, b.name, forced="knocked from the saddle")
            return {"ok": True, "dismounted": True,
                    "detail": str(thrown.get("detail",
                                             f"{b.name} drops prone."))}
        self.update_token(b.id, prone=True)
        saved = self._stay_in_saddle(b, "goes down under them")
        out = {"ok": True, "detail": f"{b.name} drops prone."}
        if saved:
            out["saddle_check"] = saved
            out["detail"] += " " + saved["detail"]
        return out

    def stand_up(self, map_id: int, ref: str) -> dict:
        """Get up. Costs half the creature's speed, and can fail for want of it."""
        b = self.find_token(map_id, ref)
        if not b:
            return {"ok": False, "reason": "no such creature"}
        if not b.prone:
            return {"ok": False, "reason": f"{b.name} is already on their feet"}
        cost = b.speed_ft // 2
        remaining = max(0, b.speed_ft - b.moved_ft)
        if cost > remaining:
            return {"ok": False, "cost_ft": cost, "remaining_ft": remaining,
                    "reason": (f"standing costs {cost} ft and {b.name} has "
                               f"{remaining} ft left")}
        self.update_token(b.id, prone=False,
                          moved_ft=min(b.speed_ft * 4, b.moved_ft + cost))
        return {"ok": True, "cost_ft": cost,
                "detail": f"{b.name} gets up ({cost} ft of movement)."}

    def swap(self, map_id: int, a_ref: str, b_ref: str) -> dict:
        """Two creatures change places — neither is moving of its own accord.

        Used by features that let allies trade positions. Both footprints must
        fit where the other stood, which is why this isn't two moves: a Large
        creature and a Medium one can't always simply exchange.
        """
        a = self.find_token(map_id, a_ref)
        b = self.find_token(map_id, b_ref)
        row = self.get_scene(map_id)
        if not (a and b and row):
            return {"ok": False, "reason": "no such creature on this board"}
        if a.id == b.id:
            return {"ok": False, "reason": "those are the same creature"}
        g = self.grid_of(row)
        na, nb = size_squares(a.size), size_squares(b.size)
        blocked_a = self._occupied(map_id, exclude=a.id)
        blocked_b = self._occupied(map_id, exclude=b.id)
        # Each must fit the OTHER's square, ignoring the other's own footprint.
        for sq in geo.footprint(b.x, b.y, nb):
            blocked_a.discard(sq)
        for sq in geo.footprint(a.x, a.y, na):
            blocked_b.discard(sq)
        if not geo._fits(g, (b.x, b.y), na, mode=a.movement_mode, blocked=blocked_a):
            return {"ok": False, "reason": f"{a.name} doesn't fit where {b.name} stands"}
        if not geo._fits(g, (a.x, a.y), nb, mode=b.movement_mode, blocked=blocked_b):
            return {"ok": False, "reason": f"{b.name} doesn't fit where {a.name} stands"}
        ax, ay, bx, by = a.x, a.y, b.x, b.y
        # Written straight through: ``update_token`` deliberately refuses x/y so
        # nothing can sidestep the movement rules by editing a position, which
        # means a placement change has to go to the row itself.
        with Session(self.engine) as s:
            la, lb = s.get(MapToken, a.id), s.get(MapToken, b.id)
            if not (la and lb):
                return {"ok": False, "reason": "no such creature"}
            la.x, la.y, lb.x, lb.y = bx, by, ax, ay
            la.updated_at = lb.updated_at = _now()
            s.add(la)
            s.add(lb)
            s.commit()
        self._bump(map_id)
        self.recompute_auras(map_id)
        self._log(map_id, row.session_id, "move", actor=a.name,
                  summary=f"{a.name} and {b.name} change places")
        return {"ok": True, "detail": f"{a.name} and {b.name} change places.",
                "a": [bx, by], "b": [ax, ay]}

    def shove(self, token_id: int, *, away_from: Optional[str] = None,
              toward: Optional[str] = None, to_square: Optional[Square] = None,
              distance_ft: int = 10) -> dict:
        """Forced movement: shunt a creature in a straight line.

        This is NOT the creature moving, and the difference matters in three
        ways the board has to get right:

        * it ignores the target's own speed entirely — a shove works on
          something that has already used its movement, or has none;
        * it provokes **no** opportunity attacks, because the target isn't
          choosing to go;
        * it travels in a straight line and STOPS at the first thing in the
          way, rather than pathing around obstacles the way ``move_token``
          politely does.

        Direction comes from ``away_from`` / ``toward`` (another creature) or
        ``to_square``. Reports where it stopped, what stopped it, and any drop
        it was shoved over — a push off a ledge is a fall, and the DM should
        hear about it rather than the board silently applying damage.
        """
        tok = self.get_token(token_id)
        if not tok:
            return {"ok": False, "reason": "no such token"}
        row = self.get_scene(tok.map_id)
        if row is None or not row.active:
            return {"ok": False, "reason": "no board is out"}

        anchor: Optional[Square] = None
        if to_square is not None:
            anchor = (int(to_square[0]), int(to_square[1]))
            pull = True
        else:
            ref = toward or away_from
            other = self.find_token(tok.map_id, ref) if ref else None
            if other is None:
                return {"ok": False,
                        "reason": "say what to push away from, or pull toward"}
            anchor, pull = (other.x, other.y), toward is not None
        dx, dy = anchor[0] - tok.x, anchor[1] - tok.y
        if not pull:
            dx, dy = -dx, -dy
        if dx == 0 and dy == 0:
            return {"ok": False, "reason": "there's no direction to push in"}
        # One step per square along the dominant axis, diagonals included.
        norm = max(abs(dx), abs(dy))
        sx, sy = round(dx / norm), round(dy / norm)

        sq_ft = row.square_ft or 5
        steps = max(0, int(distance_ft) // sq_ft)
        g = self.grid_of(row)
        n = size_squares(tok.size)
        blocked = self._occupied(tok.map_id, exclude=token_id)
        start = (tok.x, tok.y)
        cur = start
        stopped_by = ""
        for _ in range(steps):
            nxt = (cur[0] + sx, cur[1] + sy)
            if not g.in_bounds(*nxt):
                stopped_by = "the edge of the map"
                break
            if not geo._fits(g, nxt, n, mode=tok.movement_mode, blocked=blocked):
                who = next((t.name for t in self.tokens(tok.map_id, include_defeated=False)
                            if t.id != token_id
                            and nxt in geo.footprint(t.x, t.y, size_squares(t.size))), "")
                stopped_by = who or "the wall"
                break
            cur = nxt

        moved_ft = max(abs(cur[0] - start[0]), abs(cur[1] - start[1])) * sq_ft
        if cur != start:
            with Session(self.engine) as s:
                live = s.get(MapToken, token_id)
                if live:
                    live.x, live.y = cur
                    live.updated_at = _now()
                    s.add(live)
                    s.commit()
            self._bump(tok.map_id)
            self.recompute_auras(tok.map_id)
            if row.fog:
                self.reveal_from_party(tok.map_id)

        verb = "pulled" if pull else "pushed"
        detail = f"{tok.name} is {verb} {moved_ft} ft"
        if stopped_by:
            detail += f", stopping against {stopped_by}"
        out = {"ok": True, "x": cur[0], "y": cur[1], "moved_ft": moved_ft,
               "stopped_by": stopped_by, "hit_something": bool(stopped_by),
               "detail": detail}
        drop = self._drop_ft(row, start, cur)
        if drop >= 10 and tok.movement_mode != "fly":
            out["fall_ft"] = drop
        # The squares crossed can matter (shoved through a wall of fire).
        out["entered"] = [e["name"] for e in self._effects_at(tok.map_id, cur, n)]
        # Being flung out of someone's reach breaks their hold on you — and
        # equally, hauls a captive of yours out of yours.
        broke = self._break_far_grapples(tok.map_id, row)
        if broke:
            out["grapples_broken"] = broke
        # A shoved mount takes its rider with it — and then the rider finds out
        # whether they kept their seat. This is the case the rule is FOR:
        # "an effect moves your mount against its will while you're on it".
        carried = self._rider_of(tok)
        if carried is not None:
            self._place(carried.id, cur)
            out["carried"] = carried.name
            saved = self._stay_in_saddle(tok, "is flung aside")
            if saved:
                out["saddle_check"] = saved
                out["detail"] = detail + ". " + saved["detail"]
        self._log(tok.map_id, row.session_id, "move", actor=tok.name,
                  summary=detail,
                  payload={"token_id": token_id, "forced": True,
                           "from": list(start), "to": list(cur)})
        return out

    def start_turn(self, map_id: int, token_id: Optional[int] = None,
                   combatant_id: Optional[int] = None) -> Optional[MapToken]:
        """Reset a token's spent movement at the start of its turn."""
        tok = None
        if token_id is not None:
            tok = self.get_token(token_id)
        elif combatant_id is not None:
            tok = self.token_for_combatant(map_id, combatant_id)
        if tok is None:
            return None
        return self.update_token(tok.id, moved_ft=0)

    def _opportunity(self, tok: MapToken, path: list[Square],
                     row: TacticalMap) -> list[dict]:
        """Enemies whose reach this path leaves (Disengage is the caller's call)."""
        threats: dict[int, tuple[list[Square], int]] = {}
        names: dict[int, str] = {}
        for other in self.tokens(tok.map_id):
            if other.id == tok.id or other.defeated or other.team == tok.team:
                continue
            if other.kind in (TokenKind.MARKER, TokenKind.OBJECT):
                continue
            reach = other.reach_ft or 5
            # Height alone can put a mover out of reach: a wyvern sixty feet up
            # flies over a spearman without ever entering his threatened space,
            # and the path search below is flat, so it would say otherwise.
            if self.height_gap_ft(row, other, tok) > reach:
                continue
            threats[other.id] = (
                geo.footprint(other.x, other.y, size_squares(other.size)),
                reach)
            names[other.id] = other.name
        ids = geo.opportunity_triggers(
            path, threats, mover_size=size_squares(tok.size),
            square_ft=row.square_ft)
        return [{"token_id": i, "name": names.get(i, "?")} for i in ids]

    @staticmethod
    def _elevation_summary(row: TacticalMap) -> str:
        """What high ground this floor has, for the DM board.

        Counted rather than mapped: a list of raised squares is unreadable, and
        what a DM needs is that the high ground EXISTS, how high it is, and what
        it costs — the rest they can read off the grid and the tokens.
        """
        elev = row.elevation or {}
        if not elev:
            return ""
        tally: dict[int, int] = {}
        for ft in elev.values():
            n = int(ft or 0)
            if n:
                tally[n] = tally.get(n, 0) + 1
        if not tally:
            return ""
        up = ", ".join(f"{n} squares at {ft:+d} ft"
                       for ft, n in sorted(tally.items(), reverse=True))
        return ("ground height: " + up +
                " — climbing costs an extra foot per foot climbed, and stepping"
                " off 10 ft or more is a fall. Height counts toward every"
                " distance, and shooting down into a lower square is worth"
                " cover to whoever is up there")

    @staticmethod
    def _height_at(row: TacticalMap, sq: Square) -> int:
        return int((row.elevation or {}).get(f"{sq[0]},{sq[1]}", 0) or 0)

    @staticmethod
    def token_height_ft(row: TacticalMap, tok: MapToken) -> int:
        """How high off the board a creature is, in feet.

        Its own ``elevation_ft`` when it has one (a flier holding station, a
        creature on a rope), otherwise the ground it is standing on. One helper
        so distance, reach and areas can't disagree about how high something is.
        """
        if tok is None:
            return 0
        # The floor they are standing on, plus anything they are doing above
        # it. Every distance, reach, spell area and cover check on this board
        # already folds height in, so putting the level here is what makes an
        # archer on the gallery 15 ft away instead of standing on your head.
        base = 0
        if int(getattr(tok, "level", 0) or 0):
            lv = (row.levels or [])
            idx = int(tok.level) - 1
            if 0 <= idx < len(lv):
                base = int((lv[idx] or {}).get("base_ft") or 0)
        own = int(tok.elevation_ft or 0)
        return base + (own if own else VttEngine._height_at(row, (tok.x, tok.y)))

    @staticmethod
    def height_gap_ft(row: TacticalMap, a: MapToken, b: MapToken) -> int:
        """Vertical separation between two creatures."""
        return abs(VttEngine.token_height_ft(row, a) - VttEngine.token_height_ft(row, b))

    def _drop_ft(self, row: TacticalMap, frm: Square, to: Square) -> int:
        """How far down a step goes (0 when level or climbing)."""
        return max(0, self._height_at(row, frm) - self._height_at(row, to))

    def _hazards_on(self, row: TacticalMap, g: Grid,
                    squares: Iterable[Square]) -> list[dict]:
        """Dangerous ground crossed by a move (tiles + hazard effects)."""
        out: list[dict] = []
        seen: set[str] = set()
        for sq in squares:
            if g.hazard_at(*sq):
                t = g.tile_at(*sq)
                if t.name not in seen:
                    seen.add(t.name)
                    out.append({"name": t.name, "x": sq[0], "y": sq[1]})
        for eff in self.effects(row.id):
            if not (eff.damage or eff.save_dc):
                continue
            eff_sq = {tuple(p) for p in (eff.squares or [])}
            hit = next((s for s in squares if s in eff_sq), None)
            if hit and eff.name not in seen:
                seen.add(eff.name)
                out.append({"name": eff.name, "x": hit[0], "y": hit[1],
                            "damage": eff.damage, "save_dc": eff.save_dc,
                            "save_ability": eff.save_ability})
        return out

    def _effect_cost_fn(self, map_id: int, mode: str, row=None):
        """Extra feet a step costs beyond the tile itself.

        Two sources: difficult-terrain effects laid on the ground (grease, webs,
        spike growth), and climbing — going UP a ledge costs an extra foot per
        foot climbed, as the SRD has it. Coming back down is free (and may be a
        fall, which :meth:`move_token` reports).
        """
        if mode == "fly":
            return None
        rough: set[Square] = set()
        if mode != "swim":
            for eff in self.effects(map_id):
                if eff.difficult_terrain:
                    rough.update(tuple(p) for p in (eff.squares or []))
        row = row or self.get_scene(map_id)
        elev: dict = dict((row.elevation or {}) if row else {})
        if not rough and not elev:
            return None

        def _height(sq: Square) -> int:
            return int(elev.get(f"{sq[0]},{sq[1]}", 0) or 0)

        def cost(frm: Square, to: Square) -> int:
            extra = 5 if to in rough else 0
            climb = _height(to) - _height(frm)
            return extra + (climb if climb > 0 else 0)

        return cost

    # =============================================================== effects

    def add_effect(self, map_id: int, name: str, *, kind: str = EffectKind.AREA,
                   shape: str = Shape.SPHERE, x: int = 0, y: int = 0,
                   radius_ft: int = 0, length_ft: int = 0, width_ft: int = 5,
                   direction_deg: float = 0.0,
                   squares: Optional[Iterable[Square]] = None,
                   duration_rounds: Optional[int] = None,
                   source_token_id: Optional[int] = None,
                   concentration: bool = False,
                   difficult_terrain: bool = False, blocks_sight: bool = False,
                   blocks_movement: bool = False, obscured: Optional[str] = None,
                   silences: bool = False,
                   level: int = 0,
                   damage: Optional[str] = None, save_ability: Optional[str] = None,
                   save_dc: Optional[int] = None, trigger: Optional[str] = None,
                   color: Optional[str] = None, opacity: float = 0.35,
                   icon: Optional[str] = None, visible_to: str = "all",
                   permanent: bool = False,
                   respect_walls: bool = True) -> Optional[MapEffect]:
        """Drop an overlay on the board and resolve its exact squares.

        Templates are resolved here (never by the model): a 20-ft-radius sphere
        centred on ``(x, y)`` becomes the concrete list of squares it covers,
        clipped by line of effect so it doesn't spill through a wall.
        """
        row = self.get_scene(map_id)
        if row is None:
            return None
        g = self.grid_of(row)
        origin_size = 1
        if source_token_id and shape == Shape.EMANATION:
            src = self.get_token(source_token_id)
            if src:
                x, y = src.x, src.y
                origin_size = size_squares(src.size)

        sq = geo.area_squares(
            shape, (int(x), int(y)), radius_ft=radius_ft, length_ft=length_ft,
            width_ft=width_ft, direction_deg=direction_deg,
            square_ft=row.square_ft, origin_size=origin_size, grid=g,
            respect_walls=respect_walls,
            path=[tuple(p) for p in (squares or [])] or None,
        )
        current_round = self._round(row)
        eff = MapEffect(
            map_id=map_id, name=name[:80], kind=kind, shape=shape,
            origin_x=int(x), origin_y=int(y), radius_ft=int(radius_ft),
            length_ft=int(length_ft), width_ft=int(width_ft),
            direction_deg=int(direction_deg), squares=[list(p) for p in sq],
            difficult_terrain=difficult_terrain, blocks_sight=blocks_sight,
            blocks_movement=blocks_movement, obscured=obscured,
            silences=bool(silences),
            level=int(level or 0),
            damage=damage, save_ability=save_ability, save_dc=save_dc,
            trigger=trigger, color=color or _effect_color(kind, name),
            opacity=opacity, icon=icon, source_token_id=source_token_id,
            concentration=concentration, created_round=current_round,
            expires_round=(None if permanent or not duration_rounds
                           else current_round + int(duration_rounds)),
            visible_to=visible_to, active=True,
        )
        with Session(self.engine) as s:
            s.add(eff)
            s.commit()
            s.refresh(eff)
        self._bump(map_id)
        self._log(map_id, row.session_id, "effect", summary=f"{name} ({shape})",
                  payload={"effect_id": eff.id, "squares": len(sq)})
        return eff

    def effects(self, map_id: int, *, active_only: bool = True) -> list[MapEffect]:
        with Session(self.engine) as s:
            rows = list(s.exec(select(MapEffect).where(
                MapEffect.map_id == map_id)).all())
        return [e for e in rows if e.active] if active_only else rows

    def remove_effect(self, effect_id: int) -> bool:
        with Session(self.engine) as s:
            eff = s.get(MapEffect, effect_id)
            if not eff:
                return False
            eff.active = False
            eff.updated_at = _now()
            s.add(eff)
            s.commit()
            map_id, name = eff.map_id, eff.name
        self._bump(map_id)
        self._log(map_id, None, "effect_end", summary=f"{name} ends")
        return True

    def find_effect(self, map_id: int, ref: str) -> Optional[MapEffect]:
        ref_l = (ref or "").strip().lower()
        for e in self.effects(map_id):
            if e.name.lower() == ref_l:
                return e
        for e in self.effects(map_id):
            if ref_l and ref_l in e.name.lower():
                return e
        return None

    def advance_round(self, map_id: int, round_no: Optional[int] = None) -> list[str]:
        """Expire timed effects and re-anchor auras. Returns end-of-effect notes."""
        row = self.get_scene(map_id)
        if row is None:
            return []
        rnd = int(round_no if round_no is not None else self._round(row))
        notes: list[str] = []
        with Session(self.engine) as s:
            for eff in s.exec(select(MapEffect).where(
                    MapEffect.map_id == map_id,
                    MapEffect.active == True)).all():  # noqa: E712
                if eff.expires_round is not None and rnd >= eff.expires_round:
                    eff.active = False
                    eff.updated_at = _now()
                    s.add(eff)
                    notes.append(f"{eff.name} fades")
            s.commit()
        self.recompute_auras(map_id)
        if notes:
            self._bump(map_id)
        return notes

    def recompute_auras(self, map_id: int) -> None:
        """Auras and light follow their source token around the board."""
        row = self.get_scene(map_id)
        if row is None:
            return
        g = self.grid_of(row)
        with Session(self.engine) as s:
            for eff in s.exec(select(MapEffect).where(
                    MapEffect.map_id == map_id,
                    MapEffect.active == True)).all():  # noqa: E712
                if not eff.source_token_id or eff.kind not in (EffectKind.AURA,
                                                               EffectKind.LIGHT):
                    continue
                src = s.get(MapToken, eff.source_token_id)
                if src is None:
                    eff.active = False
                    s.add(eff)
                    continue
                if (eff.origin_x, eff.origin_y) == (src.x, src.y):
                    continue
                eff.origin_x, eff.origin_y = src.x, src.y
                sq = geo.area_squares(
                    eff.shape or Shape.EMANATION, (src.x, src.y),
                    radius_ft=eff.radius_ft, length_ft=eff.length_ft,
                    width_ft=eff.width_ft, direction_deg=eff.direction_deg,
                    square_ft=row.square_ft, origin_size=size_squares(src.size),
                    grid=g, respect_walls=(eff.kind == EffectKind.LIGHT))
                eff.squares = [list(p) for p in sq]
                eff.updated_at = _now()
                s.add(eff)
            s.commit()

    def _effects_at(self, map_id: int, sq: Square, n: int = 1) -> list[dict]:
        cells = set(geo.footprint(sq[0], sq[1], n))
        out = []
        for eff in self.effects(map_id):
            if cells & {tuple(p) for p in (eff.squares or [])}:
                out.append({"id": eff.id, "name": eff.name, "kind": eff.kind,
                            "damage": eff.damage, "save_dc": eff.save_dc,
                            "save_ability": eff.save_ability})
        return out

    def tokens_in_effect(self, effect_id: int) -> list[MapToken]:
        with Session(self.engine) as s:
            eff = s.get(MapEffect, effect_id)
        if eff is None:
            return []
        cells = {tuple(p) for p in (eff.squares or [])}
        row = self.get_scene(eff.map_id)
        # A template's squares are flat, so a creature far above or below the
        # effect stands in one of them and is caught by a fireball it should
        # have been well clear of. Its own vertical reach is its radius/length.
        span = max(int(eff.radius_ft or 0), int(eff.length_ft or 0)) or None
        origin_h = self._height_at(row, (eff.origin_x, eff.origin_y)) if row else 0
        out = []
        for t in self.tokens(eff.map_id, include_defeated=False):
            if not (cells & set(geo.footprint(t.x, t.y, size_squares(t.size)))):
                continue
            if span and row is not None:
                if abs(self.token_height_ft(row, t) - origin_h) > span:
                    continue
            out.append(t)
        return out

    def tokens_in_area(self, map_id: int, shape: str, x: int, y: int, *,
                       radius_ft: int = 0, length_ft: int = 0, width_ft: int = 5,
                       direction_deg: float = 0.0,
                       respect_walls: bool = True) -> list[MapToken]:
        """Who a template would catch — the check before the DM commits to it."""
        row = self.get_scene(map_id)
        if row is None:
            return []
        g = self.grid_of(row)
        cells = set(geo.area_squares(
            shape, (int(x), int(y)), radius_ft=radius_ft, length_ft=length_ft,
            width_ft=width_ft, direction_deg=direction_deg,
            square_ft=row.square_ft, grid=g, respect_walls=respect_walls))
        return [t for t in self.tokens(map_id, include_defeated=False)
                if cells & set(geo.footprint(t.x, t.y, size_squares(t.size)))]

    # =============================================================== queries

    def measure(self, map_id: int, a_ref: str, b_ref: str) -> Optional[int]:
        a, b = self.find_token(map_id, a_ref), self.find_token(map_id, b_ref)
        row = self.get_scene(map_id)
        if not (a and b and row):
            return None
        return geo.token_distance_ft(
            geo.footprint(a.x, a.y, size_squares(a.size)),
            geo.footprint(b.x, b.y, size_squares(b.size)),
            row.square_ft, dz_ft=self.height_gap_ft(row, a, b))

    def _is_linked(self, map_id: int, a_ref: str, b_ref: str) -> bool:
        """Do these two perceive each other regardless of the board? Best effort."""
        if self.linked is None:
            return False
        row = self.get_scene(map_id)
        if row is None:
            return False
        try:
            return bool(self.linked(row.session_id, a_ref, b_ref))
        except Exception as e:  # noqa: BLE001 — a link check must not break a turn
            print(f"[vtt] link check failed: {e}")
            return False

    def cover_for(self, map_id: int, attacker_ref: str, target_ref: str) -> str:
        """Cover the target has from this attacker, creatures included."""
        a, b = self.find_token(map_id, attacker_ref), self.find_token(map_id, target_ref)
        row = self.get_scene(map_id)
        if not (a and b and row):
            return "none"
        # Linked creatures always have a clean line to each other. The cover
        # below is computed correctly and then deliberately set aside — that is
        # what the feature granting the link is FOR.
        if self._is_linked(map_id, attacker_ref, target_ref):
            return "none"
        g = self.grid_of(row)
        # A creature in the way is half cover (DMG optional rule, widely used).
        obstacles: dict[Square, str] = {}
        for t in self.tokens(map_id, include_defeated=False):
            if t.id in (a.id, b.id) or t.kind == TokenKind.MARKER:
                continue
            for sq in geo.footprint(t.x, t.y, size_squares(t.size)):
                obstacles[sq] = "half"
        for eff in self.effects(map_id):
            if eff.blocks_sight:
                for p in (eff.squares or []):
                    obstacles[tuple(p)] = "three-quarters"  # type: ignore[index]
        # How tall the target presents, and how far the attacker is above them.
        # Together these are what let a rogue lie flat behind a crate and be
        # genuinely concealed — and what stops the same rogue hiding from an
        # archer on the gallery, who is shooting down over it.
        return geo.cover_between(
            g, (a.x, a.y), (b.x, b.y),
            attacker_size=size_squares(a.size), target_size=size_squares(b.size),
            obstacles=obstacles,
            target_height_ft=profile_height_ft(b.size, bool(b.prone)),
            attacker_height_advantage_ft=max(
                0, self.token_height_ft(row, a) - self.token_height_ft(row, b)))

    # ================================================================ light

    def light_map(self, map_id: int, level: int = 0) -> list[str]:
        """Light level per square: ``b`` bright, ``d`` dim, ``x`` dark.

        The board's ``lighting`` is the AMBIENT level — what the room is like
        with nobody in it. Light EFFECTS (a torch, a hearth, a *Light* cantrip)
        raise it locally, and obscuring effects (a fog cloud) lower it. Both
        already existed on ``MapEffect``; ``obscured`` had never been read by
        anything, which is why a fog cloud blocked nothing.

        A source lights its ``radius_ft`` brightly and twice that dimly — the
        5e convention already written down in ``survival.light._SOURCES``
        (torch: 20 bright, 40 dim). Light is cast as FIELD OF VIEW, not as a
        circle: a torch on the far side of a wall does not light this room.
        """
        row = self.get_scene(map_id)
        if row is None:
            return []
        # Recomputed only when the board changes: a torch's reach is a
        # field-of-view calculation (light doesn't cross walls either), and
        # sight() wants the map once per square per token.
        key = (map_id, row.revision, int(level or 0))
        hit = self._light_cache.get(key)
        if hit is not None:
            return hit

        from survival.light import brighter, darker

        ambient = row.lighting if row.lighting in ("bright", "dim", "dark") else "bright"
        g = self.grid_of(row, int(level or 0))
        lit = [[ambient] * row.width for _ in range(row.height)]

        # Only this floor's sources. A torch on the gallery does not light the
        # hall beneath it — there is a floor in the way, which is the same
        # thing that stops the two seeing each other.
        effs = [e for e in self.effects(map_id)
                if int(getattr(e, "level", 0) or 0) == int(level or 0)]
        for e in effs:
            if e.kind != "light" or e.radius_ft <= 0:
                continue
            bright_r, dim_r = int(e.radius_ft), int(e.radius_ft) * 2
            for sx, sy in geo.visible_squares(g, (e.origin_x, e.origin_y), dim_r,
                                              square_ft=row.square_ft):
                if not (0 <= sy < row.height and 0 <= sx < row.width):
                    continue
                d = geo.distance_ft((e.origin_x, e.origin_y), (sx, sy),
                                    square_ft=row.square_ft)
                near = "bright" if d <= bright_r else "dim"
                lit[sy][sx] = brighter(lit[sy][sx], near)
        # Obscurement last: a fog cloud is heavy obscurement however bright the
        # daylight behind it, so it must not be outvoted by a light source.
        for e in effs:
            if not e.obscured:
                continue
            floor = "dark" if e.obscured == "heavy" else "dim"
            for sq in (e.squares or []):
                sx, sy = int(sq[0]), int(sq[1])
                if 0 <= sy < row.height and 0 <= sx < row.width:
                    lit[sy][sx] = darker(lit[sy][sx], floor)

        code = {"bright": "b", "dim": "d", "dark": "x"}
        out = ["".join(code[c] for c in r) for r in lit]
        if len(self._light_cache) > 8:     # one board, a few floors: keep it small
            self._light_cache.clear()
        self._light_cache[key] = out
        return out

    def light_at(self, map_id: int, x: int, y: int, level: int = 0) -> str:
        """``bright`` | ``dim`` | ``dark`` for one square of one floor."""
        rows = self.light_map(map_id, int(level or 0))
        if not (0 <= y < len(rows) and 0 <= x < len(rows[y])):
            return "bright"
        return {"b": "bright", "d": "dim", "x": "dark"}[rows[y][x]]

    def silenced_at(self, map_id: int, x: int, y: int, level: int = 0) -> bool:
        """Is this square inside a zone where no sound can be made?

        The spatial half of the Verbal-component rule, and the same shape as
        the lighting one: the board owns WHERE, `survival` and the casting gate
        own what it means. Per floor, because a Silence on the gallery does not
        quiet the hall.
        """
        for e in self.effects(map_id):
            if not getattr(e, "silences", False):
                continue
            if int(e.level or 0) != int(level or 0):
                continue
            if [int(x), int(y)] in [list(p) for p in (e.squares or [])]:
                return True
        return False

    def token_silenced(self, map_id: int, ref: str) -> bool:
        """Whether a named creature is standing in silence (False if not here)."""
        t = self.find_token(map_id, ref)
        if t is None:
            return False
        return self.silenced_at(map_id, t.x, t.y, int(t.level or 0))

    def token_senses(self, t: MapToken) -> dict:
        """How this creature perceives, in feet. Looked up if never recorded.

        A token added before anyone thought about vision — or by a caller that
        doesn't know the stat block — still deserves its darkvision. So an
        empty column is resolved from the bestiary or the character's species
        the first time it's asked for, rather than silently meaning "human".
        """
        recorded = t.senses if isinstance(t.senses, dict) else None
        if recorded is not None:
            return {k: int(v) for k, v in recorded.items() if int(v or 0) > 0}
        found = self._lookup_senses(t)
        # Recorded even when empty, so a miss is not re-looked-up every frame.
        try:
            self.update_token(t.id, senses=found)
        except Exception:
            pass
        return found

    def swim_speed_ft(self, t: MapToken) -> int:
        """This creature's swimming speed in feet, 0 if it hasn't got one.

        Deliberately NOT ``movement_mode``. On an underwater board every token
        is moving by swimming, including the dwarf in plate who is drowning in
        it — and the underwater combat rules turn on which of them actually has
        a swimming speed. Looked up from the stat block once and recorded, the
        same way senses are.
        """
        if t.swim_speed_ft is not None:
            return int(t.swim_speed_ft or 0)
        found = 0
        try:
            if t.monster_slug:
                from rules.query import RulesLibrary
                m = RulesLibrary(engine=self.engine).get_monster(t.monster_slug)
                raw = (m.speed if m is not None else None) or {}
                if isinstance(raw, dict):
                    digits = "".join(c for c in str(raw.get("swim", "")) if c.isdigit())
                    found = int(digits) if digits else 0
        except Exception as e:
            print(f"[vtt] swim speed lookup failed for {t.name}: {e}")
        try:
            self.update_token(t.id, swim_speed_ft=found)
        except Exception:
            pass
        return found

    def _lookup_senses(self, t: MapToken) -> dict:
        """Best effort: the bestiary for a monster, the species for a PC.

        Deliberately forgiving. The rules tables are a separate lifecycle from
        the board (they can be absent in a bare checkout, or mid-reingest), and
        a board that refuses to open because it couldn't find out whether a
        goblin has darkvision would be a much worse bug than a goblin without
        it. Everything here degrades to plain sight.
        """
        from survival.light import parse_senses
        try:
            if t.monster_slug:
                from rules.query import RulesLibrary
                m = RulesLibrary(engine=self.engine).get_monster(t.monster_slug)
                if m is not None and isinstance(m.senses, dict):
                    return parse_senses(m.senses)
            if t.character_id:
                from sqlalchemy import text as _text
                from rules.models import Race
                out: dict = {}
                with Session(self.engine) as s:
                    got = s.exec(_text(
                        'SELECT race, tags FROM "character" WHERE id = :i'
                    ).bindparams(i=t.character_id)).first()
                    if got and got[0]:
                        want = str(got[0]).strip().lower()
                        for r in s.exec(select(Race)).all():
                            if want in (str(r.name).lower(),
                                        str(r.index_slug or "").lower()):
                                if getattr(r, "darkvision", False):
                                    out["darkvision"] = 60
                                break
                    # A sense a FEAT granted rides on the sheet as a
                    # `sense: devils_sight 120 ft` tag. The board must not have
                    # to know what an invocation is — or be able to read the
                    # owned-book catalogue — so the character row carries the
                    # answer and this parses it with everyone else's parser.
                    # The better range wins: Devil's Sight does not cancel the
                    # darkvision a tiefling was already born with.
                    if got and len(got) > 1 and got[1]:
                        raw = got[1]
                        if isinstance(raw, str):
                            import json as _json
                            try:
                                raw = _json.loads(raw)
                            except Exception:
                                raw = []
                        for tag in (raw or []):
                            if not isinstance(tag, str) or ":" not in tag:
                                continue
                            pfx, _, val = tag.partition(":")
                            if pfx.strip().lower() != "sense":
                                continue
                            for k, v in parse_senses({"raw": val.strip()}).items():
                                out[k] = max(int(out.get(k, 0)), int(v))
                return out
        except Exception as e:
            print(f"[vtt] senses lookup failed for {t.name}: {e}")
        return {}

    # ============================================================== seeing

    def vision(self, map_id: int, a_ref: str, b_ref: str, *,
               ignore_hidden: bool = False) -> dict:
        """Can ``a`` perceive ``b``? THE answer, for every caller.

        Line of sight is necessary and never sufficient: a clear line through a
        pitch-dark room shows you nothing, and 5e says so — an unseen target is
        attacked at disadvantage and attacks back with advantage. Before this,
        the board answered geometry alone, so two creatures in an unlit crypt
        saw each other perfectly and ``lighting`` was decoration.

        Returns ``{sees, via, obscured, note}``; ``via`` names what carried it,
        because "you hear it moving, you can't see it" is a different sentence
        from "you see it plainly".
        """
        a, b = self.find_token(map_id, a_ref), self.find_token(map_id, b_ref)
        row = self.get_scene(map_id)
        if not (a and b and row):
            return {"sees": False, "via": "", "obscured": "", "note": "not on the board"}
        # A creature LINK overrules the board on purpose (combat/bonds.py).
        if self._is_linked(map_id, a_ref, b_ref):
            return {"sees": True, "via": "bond", "obscured": "",
                    "note": "seen through the bond"}
        # Hiding, and only for those it fools. Checked before the geometry
        # because a successful hide beats a clear line of sight — that is what
        # it is FOR. ``ignore_hidden`` exists for the one caller that must ask
        # the question without the answer folded in: deciding whether hiding is
        # possible in the first place.
        hiding = (b.hidden and not ignore_hidden
                  and a.name not in list(b.found_by or []))

        # Different floors: there is a ceiling between them unless somebody is
        # standing under a hole. This is the ONE genuinely new rule an upper
        # level brings — everything else (distance, reach, cover, spell areas)
        # already folded height in and needed nothing. A void square on the
        # upper grid is that hole: the open middle of a galleried hall.
        if int(a.level or 0) != int(b.level or 0):
            upper, lower = ((a, b) if int(a.level or 0) > int(b.level or 0)
                            else (b, a))
            ug = self.grid_of(row, int(upper.level or 0))
            open_above = any(
                ug.get(sx, sy) == VOID
                for sx, sy in geo.bresenham((lower.x, lower.y), (upper.x, upper.y))
                if ug.in_bounds(sx, sy))
            if not open_above:
                return {"sees": False, "via": "", "obscured": "heavy",
                        "note": (f"{b.name} is on another floor, with a "
                                 f"ceiling in between")}

        g = self.grid_of(row, int(a.level or 0))
        blockers = {tuple(p) for eff in self.effects(map_id) if eff.blocks_sight
                    for p in (eff.squares or [])}
        clear = geo.has_line_of_sight(
            g, (a.x, a.y), (b.x, b.y),
            a_size=size_squares(a.size), b_size=size_squares(b.size),
            blocker=lambda x, y: g.blocks_sight(x, y) or (x, y) in blockers)

        senses = self.token_senses(a)
        dist = geo.token_distance_ft(
            geo.footprint(a.x, a.y, size_squares(a.size)),
            geo.footprint(b.x, b.y, size_squares(b.size)),
            row.square_ft, dz_ft=self.height_gap_ft(row, a, b))
        # Senses that don't use light don't care about hiding either: you can
        # hold still behind a rock, but you cannot stop making vibrations.
        unsighted = max(int(senses.get("blindsight", 0)),
                        int(senses.get("tremorsense", 0)) if _grounded(b) else 0)
        if not clear:
            # Blindsight and tremorsense don't need a line — but they do need
            # the range, and a wall still stops tremors through open air.
            if unsighted >= dist:
                return {"sees": True, "via": "blindsight", "obscured": "",
                        "note": "perceived through the obstruction"}
            return {"sees": False, "via": "", "obscured": "heavy",
                    "note": "no line of sight"}
        if hiding and unsighted < dist:
            return {"sees": False, "via": "", "obscured": "heavy",
                    "note": (f"{b.name} is hidden (Stealth "
                             f"{int(b.stealth_dc or 15)}) — Search to find them")}
        # Total cover is not a modifier, it is a wall: 5e says such a target
        # "can't be targeted directly". Almost everything granting it already
        # blocks sight, so this rarely fires — but a creature lying flat behind
        # a crate is completely concealed by something you CAN see over when
        # standing, and that is the whole point of the height rule.
        if unsighted < dist and self.cover_for(map_id, a_ref, b_ref) == "total":
            return {"sees": False, "via": "", "obscured": "heavy",
                    "note": f"{b.name} is completely concealed — total cover"}

        eff_obscured = ""
        for e in self.effects(map_id):
            if e.obscured and [b.x, b.y] in [list(p) for p in (e.squares or [])]:
                eff_obscured = "heavy" if e.obscured == "heavy" else "light"
                break
        from survival.light import perceives
        return perceives(self.light_at(map_id, b.x, b.y, int(b.level or 0)),
                         dist, senses,
                         obscured=eff_obscured, grounded=_grounded(b))

    def can_see(self, map_id: int, a_ref: str, b_ref: str) -> bool:
        return bool(self.vision(map_id, a_ref, b_ref).get("sees"))

    # ============================================================= targeting

    def targets_for(self, map_id: int, actor_ref: str, *,
                    range_ft: Optional[int] = None,
                    needs_sight: bool = True,
                    include_self: bool = False,
                    include_allies: bool = True,
                    include_defeated: bool = False) -> dict:
        """Who this creature may legally target, and WHY the rest are refused.

        "A creature you can see within range" is the coupling between the
        senses layer and the spell layer, and until now nothing could check it:
        :meth:`vision` has always been the complete answer to the first half
        and ``range_ft`` to the second, but no caller ever asked them together
        on a player's behalf. The board is the only thing that knows both.

        Every creature on the board comes back — the illegal ones carry a
        ``reason``, because a target the player can see greyed out with a
        stated cause is information, and one silently missing from the list is
        a bug they cannot tell from a rule. Same doctrine as a refused move
        naming the remedy.

        ``range_ft`` of None means "no range limit" (a touch spell's caller
        passes the reach); ``needs_sight`` off is for the handful of effects
        that don't require seeing the target.
        """
        actor = self.find_token(map_id, actor_ref)
        row = self.get_scene(map_id)
        if actor is None or row is None:
            return {"ok": False, "reason": "not on the board", "targets": []}
        out: list[dict] = []
        a_fp = geo.footprint(actor.x, actor.y, size_squares(actor.size))
        for t in self.tokens(map_id):
            if t.kind == TokenKind.MARKER:
                continue
            if t.id == actor.id and not include_self:
                continue
            if t.defeated and not include_defeated:
                continue
            if not include_allies and t.id != actor.id and t.team == actor.team:
                continue
            dist = geo.token_distance_ft(
                a_fp, geo.footprint(t.x, t.y, size_squares(t.size)),
                row.square_ft, dz_ft=self.height_gap_ft(row, actor, t))
            cover = ("none" if t.id == actor.id
                     else self.cover_for(map_id, actor.name, t.name))
            legal, reason = True, ""
            if t.defeated:
                # Only reachable with include_defeated: a caller asking about
                # a downed creature wants to hear that it is down, not to have
                # it vanish from the answer.
                legal, reason = False, f"{t.name} is already down"
            elif t.id != actor.id:
                # Range first: it is the cheaper refusal and the one the player
                # can fix by walking, so it is the more useful thing to say.
                if range_ft is not None and dist > int(range_ft):
                    legal = False
                    reason = f"out of range — {int(dist)} ft away, reaches {int(range_ft)} ft"
                elif needs_sight:
                    v = self.vision(map_id, actor.name, t.name)
                    if not v.get("sees"):
                        legal = False
                        reason = v.get("note") or "can't be seen from here"
                # Total cover is not a modifier, it is a wall — 5e says such a
                # target "can't be targeted directly", so it is refused even
                # for the effects that don't need sight.
                if legal and cover == "total":
                    legal = False
                    reason = f"{t.name} is behind total cover"
            out.append({
                "token_id": t.id, "name": t.name, "team": t.team,
                "kind": t.kind, "x": t.x, "y": t.y, "level": int(t.level or 0),
                "squares": size_squares(t.size),
                "distance_ft": int(dist), "cover": cover,
                "legal": legal, "reason": reason,
            })
        out.sort(key=lambda d: (not d["legal"], d["distance_ft"], d["name"]))
        return {"ok": True, "actor": actor.name, "actor_token_id": actor.id,
                "range_ft": range_ft, "needs_sight": needs_sight,
                "targets": out}

    def area_preview(self, map_id: int, actor_ref: str, x: int, y: int, *,
                     shape: str = "sphere", radius_ft: int = 0,
                     length_ft: int = 0, width_ft: int = 5,
                     range_ft: Optional[int] = None) -> dict:
        """Where a template dropped on (x, y) would actually land, and who it catches.

        `geo.area_squares` has clipped templates by line of effect since the
        board was built, and no player has ever been able to aim one — the DM
        placed every fireball by narrating it. This is that same call made
        answerable before the spell is spent: the squares, the creatures
        standing in them, and whether the origin is even a legal place to put it.
        """
        actor = self.find_token(map_id, actor_ref)
        row = self.get_scene(map_id)
        if actor is None or row is None:
            return {"ok": False, "reason": "not on the board", "squares": []}
        lvl = int(actor.level or 0)
        g = self.grid_of(row, lvl)
        origin = (int(x), int(y))
        if not g.in_bounds(*origin):
            return {"ok": False, "reason": "that square is off the map",
                    "squares": []}
        a_fp = geo.footprint(actor.x, actor.y, size_squares(actor.size))
        dist = geo.token_distance_ft(a_fp, [origin], row.square_ft)
        ok, reason = True, ""
        if range_ft is not None and dist > int(range_ft):
            ok = False
            reason = (f"out of range — that point is {int(dist)} ft away, "
                      f"the spell reaches {int(range_ft)} ft")
        elif not geo.line_of_effect(g, (actor.x, actor.y), origin):
            # Line of EFFECT, not of sight: you may aim into darkness, but not
            # through a wall. The template itself is clipped the same way below.
            ok = False
            reason = "no line of effect to that point — something solid is in the way"
        # A cone or a line is thrown in a direction; a sphere is dropped on a
        # point. Both are aimed by the same click, so the direction is derived
        # from it rather than asked for separately.
        direction = math.degrees(math.atan2(origin[1] - actor.y,
                                            origin[0] - actor.x))
        squares = geo.area_squares(
            shape, origin if shape not in ("cone", "line") else (actor.x, actor.y),
            radius_ft=radius_ft, length_ft=length_ft, width_ft=width_ft,
            direction_deg=direction, square_ft=row.square_ft,
            origin_size=size_squares(actor.size) if shape in ("cone", "line") else 1,
            grid=g, respect_walls=True)
        hit = {tuple(s) for s in squares}
        caught: list[dict] = []
        for t in self.tokens(map_id):
            if t.defeated or t.kind == TokenKind.MARKER:
                continue
            if int(t.level or 0) != lvl:
                continue
            if any(sq in hit for sq in
                   geo.footprint(t.x, t.y, size_squares(t.size))):
                caught.append({"token_id": t.id, "name": t.name,
                               "team": t.team, "x": t.x, "y": t.y})
        return {"ok": ok, "reason": reason, "shape": shape,
                "origin": [origin[0], origin[1]], "level": lvl,
                "distance_ft": int(dist),
                "squares": [[s[0], s[1]] for s in squares],
                "caught": caught}

    # =============================================================== hiding

    #: What a creature must have between it and an observer before it may try
    #: to hide from them. Half cover is not enough — you can be shot at behind
    #: a low wall, which means you can be seen behind it.
    _HIDING_COVER = ("three-quarters", "total")

    def hide_eligibility(self, map_id: int, ref: str) -> dict:
        """May this creature attempt to hide, and from whom? ``{ok, blocked_by}``.

        You cannot hide from someone looking straight at you. So every living
        enemy is asked separately, and each must either not perceive this
        creature at all — darkness, a fog cloud, a wall — or be looking at it
        through three-quarters cover or better. An enemy with blindsight is
        handled without a special case: it perceives you, so it blocks the
        attempt, which is exactly right.

        Dim light deliberately does NOT qualify. Lightly obscured costs an
        observer a Perception check; it does not stop them seeing you.
        """
        me = self.find_token(map_id, ref)
        if me is None:
            return {"ok": False, "reason": f"{ref} is not on the board",
                    "blocked_by": []}
        blocked = []
        for other in self.tokens(map_id, include_defeated=False):
            if other.id == me.id or other.team == me.team:
                continue
            seen = self.vision(map_id, other.name, me.name, ignore_hidden=True)
            if not seen.get("sees"):
                continue
            # Cover is protection from being SEEN. A creature perceiving you by
            # blindsight or tremorsense is not looking at you, so putting a
            # portcullis between you changes nothing for it — and neither does
            # putting out the lights.
            if seen.get("via") in ("blindsight", "tremorsense", "truesight"):
                blocked.append(f"{other.name} ({seen['via']})")
                continue
            if self.cover_for(map_id, other.name, me.name) in self._HIDING_COVER:
                continue
            blocked.append(other.name)
        if blocked:
            return {"ok": False, "blocked_by": blocked,
                    "reason": (f"{me.name} is in plain view of "
                               f"{', '.join(blocked)} — no cover, no darkness, "
                               f"nowhere to hide")}
        return {"ok": True, "blocked_by": [],
                "reason": f"{me.name} is out of sight and may attempt to hide"}

    def hide(self, map_id: int, ref: str, *, bonus: int = 0,
             advantage: bool = False, disadvantage: bool = False,
             dc: int = 15, rng=None) -> dict:
        """Take the Hide action. The CODE rolls it; the result is remembered.

        2024 rules: a DC 15 Dexterity (Stealth) check, and the result is the
        number anyone searching for you has to beat. An enemy whose passive
        Perception already equals or beats the roll finds you at once — they
        were not fooled, and making them spend a Search action to notice
        something they could not have missed is the wrong answer.
        """
        me = self.find_token(map_id, ref)
        if me is None:
            return {"ok": False, "reason": f"{ref} is not on the board"}
        elig = self.hide_eligibility(map_id, ref)
        if not elig["ok"]:
            return {"ok": False, **elig}

        from dice.mechanics import ability_check
        roll = ability_check(bonus, dc=dc, advantage=advantage,
                             disadvantage=disadvantage,
                             label=f"Stealth ({me.name})", rng=rng)
        if not roll.success:
            self.update_token(me.id, hidden=False, stealth_dc=None, found_by=[])
            return {"ok": False, "roll": roll.total, "dc": dc,
                    "detail": roll.detail,
                    "reason": f"{me.name} fails to go unseen ({roll.total} vs DC {dc})"}

        # Anyone who could not have missed it never has to look.
        from survival.light import passive_perception
        obvious = []
        for other in self.tokens(map_id, include_defeated=False):
            if other.team == me.team or other.id == me.id:
                continue
            if not self.vision(map_id, other.name, me.name,
                               ignore_hidden=True).get("sees"):
                continue
            if passive_perception(self.token_senses(other)) >= roll.total:
                obvious.append(other.name)

        self.update_token(me.id, hidden=True, stealth_dc=roll.total,
                          found_by=obvious)
        self._bump(map_id)
        row = self.get_scene(map_id)
        self._log(map_id, row.session_id if row else None, "hide",
                  summary=f"{me.name} hides (Stealth {roll.total})")
        note = (f" — but {', '.join(obvious)} notice anyway"
                if obvious else "")
        return {"ok": True, "roll": roll.total, "dc": dc, "detail": roll.detail,
                "stealth_dc": roll.total, "found_by": obvious,
                "detail_text": (f"{me.name} slips out of sight "
                                f"(Stealth {roll.total}){note}.")}

    def search(self, map_id: int, ref: str, *, bonus: int = 0,
               advantage: bool = False, disadvantage: bool = False,
               rng=None) -> dict:
        """Take the Search action: Perception against every hider's own roll.

        Finding someone is personal. A success adds this searcher to that
        creature's ``found_by`` and nobody else's — the guard who spotted you
        can see you while the rest of the room still cannot, which is the whole
        reason hiding is tracked per observer rather than as one flag.
        """
        me = self.find_token(map_id, ref)
        if me is None:
            return {"ok": False, "reason": f"{ref} is not on the board"}
        from dice.mechanics import ability_check
        found, missed = [], []
        for other in self.tokens(map_id, include_defeated=False):
            if other.team == me.team or not other.hidden:
                continue
            if me.name in list(other.found_by or []):
                found.append(other.name)
                continue
            dc = int(other.stealth_dc or 15)
            roll = ability_check(bonus, dc=dc, advantage=advantage,
                                 disadvantage=disadvantage,
                                 label=f"Perception ({me.name})", rng=rng)
            if roll.success:
                self.update_token(other.id,
                                  found_by=[*list(other.found_by or []), me.name])
                found.append(other.name)
            else:
                missed.append(other.name)
        if found or missed:
            self._bump(map_id)
        return {"ok": True, "found": found, "missed": missed,
                "detail_text": (
                    (f"{me.name} finds {', '.join(found)}. " if found else "")
                    + (f"{me.name} cannot make out {', '.join(missed)}."
                       if missed else "")
                    or f"{me.name} searches, and there is nobody hiding.")}

    def unhide(self, map_id: int, ref: str, reason: str = "") -> dict:
        """Stop being hidden. Attacking, shouting or casting aloud all do this.

        Kept as its own verb rather than an ``update_token`` call, because the
        board has to forget the Stealth roll and everyone who had found it at
        the same moment — leaving a stale DC behind would make the NEXT hide
        cheaper than it should be.
        """
        me = self.find_token(map_id, ref)
        if me is None:
            return {"ok": False, "reason": f"{ref} is not on the board"}
        was = bool(me.hidden)
        self.update_token(me.id, hidden=False, stealth_dc=None, found_by=[])
        if was:
            self._bump(map_id)
        return {"ok": True, "was_hidden": was,
                "detail_text": (f"{me.name} is no longer hidden"
                                + (f" ({reason})" if reason else "") + "."
                                if was else f"{me.name} was not hidden.")}

    # =================================================================== fog

    @staticmethod
    def _blank_fog(w: int, h: int) -> list[str]:
        return ["0" * w for _ in range(h)]

    def fog_of(self, row: TacticalMap, level: int = 0) -> Optional[list]:
        """This floor's memory of itself, or None when the board has no fog.

        Stored the same way terrain is — level 0 on the row, upper floors in
        ``levels`` — because it is the same KIND of fact and splitting it any
        other way would put two answers to "what does this storey look like"
        in two different places. Walking the hall must not light the gallery.
        """
        if not int(level or 0):
            return row.fog or None
        lv = self.levels_of(row)
        idx = max(0, min(int(level), len(lv) - 1))
        got = lv[idx].get("fog")
        if got:
            return got
        # An upper floor inherits the board's fogged-ness, not its memory: if
        # the ground is fogged this one starts unexplored, not revealed.
        return (self._blank_fog(row.width, row.height) if row.fog else None)

    def _set_fog(self, map_id: int, level: int, rows: list) -> None:
        if not int(level or 0):
            self._set_fields(map_id, fog=rows)
            return
        row = self.get_scene(map_id)
        if row is None:
            return
        levels = [dict(l) for l in (row.levels or [])]
        idx = int(level) - 1
        if 0 <= idx < len(levels):
            levels[idx]["fog"] = rows
            self._set_fields(map_id, levels=levels)

    def reveal(self, map_id: int, x: int, y: int, radius_ft: int = 30,
               level: int = 0) -> int:
        """Reveal what can be seen from a square. Returns squares newly lit."""
        row = self.get_scene(map_id)
        if row is None:
            return 0
        fog_rows = self.fog_of(row, level)
        if not fog_rows:
            return 0
        g = self.grid_of(row, int(level or 0))
        fog = [list(r) for r in fog_rows]
        n = 0
        for sx, sy in geo.visible_squares(g, (int(x), int(y)), radius_ft,
                                          square_ft=row.square_ft):
            if 0 <= sy < len(fog) and 0 <= sx < len(fog[sy]) and fog[sy][sx] == "0":
                fog[sy][sx] = "1"
                n += 1
        if n:
            self._set_fog(map_id, int(level or 0), ["".join(r) for r in fog])
        return n

    def reveal_from_party(self, map_id: int, radius_ft: Optional[int] = None) -> int:
        """Re-light the board from where the party is standing."""
        row = self.get_scene(map_id)
        if row is None or not row.fog:
            return 0
        default_r = {"bright": 120, "dim": 60, "dark": 30}.get(row.lighting or "bright", 90)
        r = int(radius_ft or default_r)
        total = 0
        for t in self.tokens(map_id, include_defeated=False):
            if t.team != Team.PARTY:
                continue
            # Each of them lights the floor they are actually standing on.
            total += self.reveal(map_id, t.x, t.y, r, level=int(t.level or 0))
        return total

    def clear_fog(self, map_id: int) -> None:
        row = self.get_scene(map_id)
        levels = [{k: v for k, v in dict(l).items() if k != "fog"}
                  for l in ((row.levels or []) if row else [])]
        self._set_fields(map_id, fog=None, levels=levels or None)

    def sight(self, map_id: int, *, team: str = Team.PARTY,
              radius_ft: Optional[int] = None,
              level: int = 0) -> Optional[list[str]]:
        """What ``team`` can see RIGHT NOW, in the same shape as ``fog``.

        Fog is MEMORY: it records everywhere the party has ever been able to
        see and never dims again, so a board with fog on shows a room they
        walked out of as brightly as the one they are standing in. That is the
        right answer for "have we been here", and the wrong one for "can we see
        the thing that just moved" — and the difference is precisely what a
        door is for. Closing one behind you should put the room you left back
        into the dark without unlearning it.

        So this is the second tier, recomputed each frame from real line of
        sight (which reads ``blocks_sight`` off the tile, so a closed door
        blocks and an open one doesn't, for free). ``None`` when the board has
        no fog — with no memory there is nothing for live sight to be a tier
        above, and every square is simply visible.
        """
        row = self.get_scene(map_id)
        if row is None or not self.fog_of(row, int(level or 0)):
            return None
        # Per floor: this is what the party can see OF THIS STOREY, and the
        # only creatures who can see any of it are the ones standing on it.
        lv = int(level or 0)
        g = self.grid_of(row, lv)
        light = self.light_map(map_id, lv)
        levels = {"b": "bright", "d": "dim", "x": "dark"}
        lit = [["0"] * row.width for _ in range(row.height)]

        from survival.light import perceives

        for t in self.tokens(map_id, include_defeated=False):
            if t.team != team or int(t.level or 0) != lv:
                continue
            senses = self.token_senses(t)
            # How far to even look. Line of sight has no range in the rules —
            # what limits you is light and your own senses — so the scan reach
            # is the largest of the board, this creature's special senses, and
            # an ordinary horizon. Squares are then kept or dropped one at a
            # time by whether this creature could actually make anything out
            # there, which is what makes a carried torch worth carrying.
            reach = int(radius_ft or max(
                120, *(int(v or 0) for v in senses.values()) if senses else (120,)))
            for sx, sy in geo.visible_squares(g, (t.x, t.y), reach,
                                              square_ft=row.square_ft,
                                              origin_size=size_squares(t.size)):
                if not (0 <= sy < row.height and 0 <= sx < row.width):
                    continue
                if lit[sy][sx] == "1":
                    continue
                d = geo.distance_ft((t.x, t.y), (sx, sy), square_ft=row.square_ft)
                level = levels.get(light[sy][sx], "bright") if light else "bright"
                if perceives(level, d, senses)["sees"]:
                    lit[sy][sx] = "1"
        return ["".join(r_) for r_ in lit]

    # ================================================================= views

    @staticmethod
    def _visible_to_team(t: MapToken, toks: list, viewer_team: str) -> bool:
        """Should this token appear on ``viewer_team``'s board at all?

        Hiding is tracked per observer, so the answer is too: a hidden foe
        shows up once ANYONE on this side has found them, and stays off the
        board for a party that has all failed to. The old rule was a blunt
        "hidden means invisible to everyone but the DM", which threw away the
        Search action's whole result — the guard who spotted the rogue could
        not see her on his own board.

        Your own people are never hidden from you. They are hiding from the
        enemy, and a player who cannot see their own token cannot play.
        """
        if not t.hidden or t.team == viewer_team:
            return True
        found = {str(n).strip().lower() for n in (t.found_by or [])}
        return any((o.name or "").strip().lower() in found
                   for o in toks if o.team == viewer_team)

    def state(self, map_id: int, *, viewer_team: str = Team.PARTY,
              include_terrain: bool = True) -> dict:
        """Everything the Activity overlay needs to draw one frame."""
        row = self.get_scene(map_id)
        if row is None:
            return {}
        toks = self.tokens(map_id)
        effs = self.effects(map_id)
        cur_token_id = self._current_token_id(row, toks)
        return {
            "id": row.id,
            "session_id": row.session_id,
            "encounter_id": row.encounter_id,
            "name": row.name,
            "kind": row.kind,
            "archetype": row.archetype,
            "width": row.width,
            "height": row.height,
            "square_ft": row.square_ft,
            "lighting": row.lighting,
            "mode": self.board_mode(row),
            "revision": row.revision,
            "active": row.active,
            "round": self._round(row),
            "current_token_id": cur_token_id,
            "terrain": (row.terrain or []) if include_terrain else [],
            # The ground floor's, flat, exactly where they have always been —
            # a client that knows nothing about storeys keeps working.
            "fog": row.fog or None,
            "sight": self.sight(map_id, team=viewer_team),
            "light": self.light_map(map_id),
            # …and one entry per floor, ground first, each carrying its OWN
            # terrain, memory, live sight and light. Every one of those is a
            # fact about a storey rather than about the board: a torch on the
            # gallery does not light the hall, and walking the hall does not
            # reveal the gallery.
            "levels": [{"name": l.get("name", f"Level {i}"),
                        "base_ft": int(l.get("base_ft") or 0),
                        "terrain": l.get("terrain") or [],
                        "fog": self.fog_of(row, i),
                        "sight": self.sight(map_id, team=viewer_team, level=i),
                        "light": self.light_map(map_id, i),
                        "stairs": list(l.get("stairs") or [])
                        if i else list((row.notes or {}).get("stairs") or [])}
                       for i, l in enumerate(self.levels_of(row))],
            "doors": row.doors or [],
            "elevation": row.elevation or {},
            "debris": self.debris_for(map_id),
            "objects": self.objects_for(map_id),
            # Scenery: drawn by every view, honoured by none of the rules.
            "decor": self.decor_for(map_id),
            # Landmarks: a mesh reference and where it stands. Both renderers
            # read this same list, and neither computes its scale — see
            # setpieces.mesh_fit for why that measurement is the server's.
            "setpieces": self.setpieces_for(map_id),
            # {tile code (or "code@skin") -> swatch image id}. The isometric
            # board builds its geometry out of these; a slot missing here just
            # falls back to its flat tile colour, which is plainer and equally
            # playable.
            "materials": self.materials_for(map_id),
            # What the board is MADE of, as opposed to what it does. Material
            # and silhouette only — no rule reads a skin. See vtt/skins.py.
            "skins": self.skins_for(map_id),
            # A vessel's hull, traced as one outline rather than a side per
            # square. Empty on every board that is not a ship. See vtt/hull.py.
            "shells": self.shells_for(map_id),
            # The route the last walk actually took, so a viewer can animate a
            # creature AROUND the wall rather than through it.
            "last_move": self.last_move(map_id),
            "background_image_id": row.background_image_id,
            "art_status": row.art_status,
            # The painted isometric layer, and the canonical framing it was
            # baked at. The client needs both: the picture, and where on its own
            # projection to lay it. Sent as squares (not pixels) because zoom is
            # the viewer's business and the framing is not.
            "iso_image_id": row.iso_image_id,
            "iso_art_status": row.iso_art_status,
            "description": (row.notes or {}).get("description", ""),
            # Named compartments: [{"name","level","x","y","w","h"}]. A label,
            # never a rule — the bulkheads around one are ordinary walls.
            "rooms": list((row.notes or {}).get("rooms") or []),
            "tokens": [_token_dict(t, row) for t in toks
                       if self._visible_to_team(t, toks, viewer_team)],
            "effects": [_effect_dict(e) for e in effs
                        if e.visible_to in ("all", viewer_team)],
            "legend": self.grid_of(row).legend(),
        }

    def render(self, map_id: int, *, max_tokens: int = 24) -> str:
        """The board as text for the DM prompt.

        Deliberately compact: an ASCII map with token letters, a key, and one
        line per creature with position, distance to the nearest enemy, and its
        cover. That's the spatial truth the narration needs, in a few hundred
        tokens rather than a few thousand.
        """
        row = self.get_scene(map_id)
        if row is None:
            return ""
        g = self.grid_of(row)
        toks = [t for t in self.tokens(map_id) if not t.defeated][:max_tokens]
        cur_token_id = self._current_token_id(row, toks)
        marks: dict[Square, str] = {}
        letters: list[tuple[str, MapToken]] = []
        alphabet = "abcdefghijklmnopqrstuvwxyz"
        for i, t in enumerate(toks):
            ch = (t.name[:1].upper() if t.team == Team.PARTY
                  else alphabet[i % len(alphabet)])
            letters.append((ch, t))
            for sq in geo.footprint(t.x, t.y, size_squares(t.size)):
                marks[sq] = ch

        light = {"dark": "unlit", "dim": "dim light",
                 "bright": "bright light"}.get(row.lighting or "bright", "bright light")
        medium = {"swim": ", fought underwater — everything here is swimming",
                  "fly": ", fought in open air — everything here is flying"}.get(
                      self.board_mode(row), "")
        floors = self.levels_of(row)
        storeys = ("" if len(floors) < 2 else
                   f", {len(floors)} floors: "
                   + "; ".join(f"{i} {f.get('name')} "
                               f"({_ft_offset(f.get('base_ft'))})"
                               for i, f in enumerate(floors)))
        lines = [f"# Board: {row.name} — {row.width}x{row.height} squares "
                 f"({row.square_ft} ft each), {light}{medium}{storeys}"]
        desc = (row.notes or {}).get("description")
        if desc:
            lines.append(f"  {desc}")
        # HIGH GROUND. A creature standing on a ledge already gets a note on
        # its own line, which is no help at all to a DM deciding whether
        # anybody SHOULD: the board knew about its own height and only ever
        # mentioned it once somebody was on it. Elevation is not a tile, so the
        # legend cannot carry it either.
        high = self._elevation_summary(row)
        if high:
            lines.append(f"  {high}")
        # What the rooms are CALLED. The grid below shows walls and a doorway
        # and cannot say which side of them the armoury is on, so a DM asked to
        # send somebody there would have to invent an answer the board would
        # then contradict.
        named = [r for r in ((row.notes or {}).get("rooms") or []) if r.get("name")]
        if named:
            lines.append("  rooms: " + "; ".join(
                f"{r['name']} at {r.get('x')},{r.get('y')}"
                + (f" on level {r['level']}" if int(r.get("level") or 0) else "")
                for r in named[:8]))
        # One grid per floor, each carrying only the creatures standing on it.
        # A single-storey board prints exactly what it always printed.
        for fi, floor in enumerate(floors):
            fg = self.grid_of(row, fi)
            if len(floors) > 1:
                stairs = self.stairs_on(row, fi)
                lines.append(
                    f"-- level {fi}: {floor.get('name')} "
                    f"({_ft_offset(floor.get('base_ft'))})"
                    + (" — " + ", ".join(
                        f"{s.get('kind', 'stairs')} at {s['x']},{s['y']} "
                        f"-> level {s['to']}" for s in stairs[:4])
                       if stairs else "")
                    + (" — ' ' is open air: you can see and fall through it"
                       if fi else ""))
            for y in range(fg.height):
                out = []
                for x in range(fg.width):
                    here = marks.get((x, y))
                    on_this_floor = here is not None and any(
                        ch == here and int(t.level or 0) == fi
                        for ch, t in letters)
                    out.append(here if on_this_floor else fg.get(x, y))
                lines.append("".join(out))
        legend = g.legend(rules=True)
        if legend:
            lines.append(f"terrain: {legend}")
            lines.append("  Terrain is enforced: a creature cannot enter a square "
                         "its movement forbids, and difficult ground costs double. "
                         "Narrate within that, and the board will never contradict you. "
                         "Furniture that grants cover can be attacked and broken "
                         "([[VTT: damage | x,y | amount | type]]).")
            # The players are looking at a PAINTING of this grid, and a
            # diffusion model embellishes. When someone asks about scenery
            # that isn't in the legend above, they are not confused and they
            # are not lying — they can see it. Answering "there is no water
            # there" makes the table doubt the board. Answering "ankle-deep,
            # nothing to swim in" costs nothing and is true of every square
            # the legend calls open floor.
            lines.append("  The players see a painted map of this grid, which "
                         "may show scenery the legend doesn't list. That "
                         "scenery is decoration: narrate it as shallow, dry or "
                         "harmless (a puddle, a stain, a crack) so it matches "
                         "the square's real rule. Never promise terrain the "
                         "legend doesn't give you — the board will refuse it.")
        if self.board_mode(row) == "swim":
            # Two of the three underwater rules are enforced by the code and
            # are stated so the narration doesn't apply them a second time. The
            # third is here because it CANNOT be enforced yet: the engine has
            # no damage-type layer, so nothing can halve fire damage on its
            # own, and a rule nobody is told about is a rule that never
            # happens. Say plainly which is which.
            lines.append(
                "  Underwater. The board already applies this — do NOT also "
                "narrate a penalty for it: a melee weapon that is swung rather "
                "than thrust (anything but a dagger, javelin, shortsword, spear "
                "or trident) is at disadvantage for anyone without a swimming "
                "speed, and a ranged weapon that isn't a crossbow, net or "
                "thrown spear is too, and misses automatically past its normal "
                "range. YOU must still apply the one thing the code can't: "
                "creatures fully immersed have RESISTANCE TO FIRE — halve it.")
        from .decor import describe as _describe_decor
        scenery = _describe_decor(self.decor_for(map_id))
        if scenery:
            # Said out loud so the DM can answer a player who asks about the
            # brazier. Unnamed, it is scenery the picture has and the board
            # denies — the exact confusion the painted-terrain rule exists for.
            lines.append(f"  {scenery}")

        # Landmarks. Named for the OPPOSITE reason to scenery: a decoration is
        # announced because it has no mechanical effect, and a set piece
        # because it is entirely mechanical — the squares it stamped are real
        # walls and real rock with real cover, already in the legend above, and
        # nothing there says the eleven of them are one thing. The DM never
        # sees the picture, so without this they cannot answer "what is that".
        marks = self.setpieces_for(map_id)
        if marks:
            lines.append("  landmarks (their squares are already in the "
                         "terrain above — this is only what they ARE): "
                         + "; ".join(
                             f"{m['name']} at {m['x']},{m['y']}"
                             + (f" — {m['words']}" if m.get("words") else "")
                             for m in marks[:6]))

        hurt = [o for o in self.breakables(map_id) if o["hp"] < o["hp_max"]]
        if hurt:
            lines.append("damaged: " + "; ".join(
                f"{o['name']} at {o['x']},{o['y']} ({o['hp']}/{o['hp_max']}, AC {o['ac']})"
                for o in hurt[:8]))

        # Cover is a fact about a target and ONE attacker, so the board reports
        # it from the acting creature's point of view: on Gruk's turn, every
        # line reads "how hard is this to hit, for Gruk".
        actor = next((t for t in toks if t.id == cur_token_id), None) if cur_token_id else None
        if actor is not None:
            lines.append(f"creatures (x,y = column,row from top-left) — "
                         f"distances and cover are from {actor.name}, who is acting:")
        else:
            lines.append("creatures (x,y = column,row from top-left):")
        for ch, t in letters:
            near = ""
            ref = actor if (actor is not None and actor.id != t.id) else None
            if ref is None and actor is None:
                foes = [o for o in toks if o.team != t.team and not o.defeated]
                ref = min(
                    foes, key=lambda o: geo.token_distance_ft(
                        geo.footprint(t.x, t.y, size_squares(t.size)),
                        geo.footprint(o.x, o.y, size_squares(o.size)),
                        row.square_ft,
                        dz_ft=self.height_gap_ft(row, t, o))) if foes else None
            if ref is not None:
                d = geo.token_distance_ft(
                    geo.footprint(t.x, t.y, size_squares(t.size)),
                    geo.footprint(ref.x, ref.y, size_squares(ref.size)),
                    row.square_ft, dz_ft=self.height_gap_ft(row, t, ref))
                cov = self.cover_for(map_id, ref.name, t.name)
                near = f", {d} ft from {ref.name}"
                if cov == "total":
                    near += " (TOTAL cover from them — cannot be targeted)"
                elif cov != "none":
                    near += f" ({cov} cover from them)"
            extras = []
            # Who can see whom, measured from the acting creature — the same
            # point of view the cover line above already takes. Reported both
            # ways round, because in 5e they are different facts with different
            # consequences: attacking what you can't see is at disadvantage,
            # and being unseen BY your target hands you advantage.
            if actor is not None and actor.id != t.id:
                out = self.vision(map_id, actor.name, t.name)
                back = self.vision(map_id, t.name, actor.name)
                if not out["sees"]:
                    extras.append(f"{actor.name} CANNOT see them ({out['note']}) "
                                  f"— attacks at disadvantage, and only if the "
                                  f"square is guessed correctly")
                elif out["via"] not in ("sight", "bond"):
                    extras.append(f"located by {out['via']}, not seen")
                if not back["sees"]:
                    extras.append(f"cannot see {actor.name} — "
                                  f"{actor.name}'s attacks have advantage")
            if actor is not None and actor.id == t.id:
                extras.append("ACTING NOW")
            if int(t.level or 0):
                extras.append(f"on level {t.level} "
                              f"({_ft_offset(self.level_base_ft(row, int(t.level)))})")
            if t.mounted_on:
                extras.append(f"riding {t.mounted_on} — move {t.mounted_on}")
            if t.squeezing:
                extras.append("SQUEEZING: its attacks have disadvantage and "
                              "attacks against it have advantage")
            if t.prone:
                extras.append("prone")
            elif actor is not None and actor.id != t.id:
                # Offer the tactic where it would actually pay. A model that
                # doesn't know low cover gets better when you lie down will
                # never suggest it, and a player who asks will be told no.
                low = geo.cover_between(
                    g, (actor.x, actor.y), (t.x, t.y),
                    attacker_size=size_squares(actor.size),
                    target_size=size_squares(t.size),
                    target_height_ft=profile_height_ft(t.size, True),
                    attacker_height_advantage_ft=max(
                        0, self.token_height_ft(row, actor)
                        - self.token_height_ft(row, t)))
                if low == "total" and self.cover_for(
                        map_id, actor.name, t.name) != "total":
                    extras.append("could drop prone here for TOTAL cover "
                                  "([[VTT: prone | " + t.name + "]])")
            if t.hidden:
                # The DC is stated because the DM must not invent one: it was
                # rolled, and a Search action is measured against THIS number.
                seekers = [o.name for o in toks
                           if o.team != t.team and not o.defeated
                           and o.name in list(t.found_by or [])]
                extras.append(
                    f"HIDDEN (Stealth {int(t.stealth_dc or 15)} — a Search "
                    f"action beats it with a Perception check, "
                    f"[[VTT: search | <name> | bonus=N]])"
                    + (f"; already found by {', '.join(seekers)}" if seekers else ""))
            height = self.token_height_ft(row, t)
            if height:
                # High ground isn't advantage in this game — it's a reason to
                # consider granting cover against anything shooting up at them.
                # The height is stated because it is now part of every distance
                # on this board: a creature 60 ft up is 60 ft away.
                extras.append(f"{height} ft up (counts toward every distance) — "
                              "consider cover from attackers below")
            standing = g.tile_at(t.x, t.y).name
            if standing not in ("floor", "grass", "road", "sand"):
                extras.append(f"in {standing}")
            tail = f" [{', '.join(extras)}]" if extras else ""
            lines.append(f"  {ch} {t.name} ({t.team}) at {t.x},{t.y}{near}{tail}")

        effs = self.effects(map_id)
        if effs:
            lines.append("effects on the field:")
            for e in effs[:12]:
                caught = [t.name for t in self.tokens_in_effect(e.id)]
                who = f" — on {', '.join(caught)}" if caught else ""
                span = (f"{e.radius_ft} ft radius" if e.radius_ft
                        else f"{len(e.squares or [])} squares")
                lines.append(f"  {e.name} ({e.kind}, {span}) at "
                             f"{e.origin_x},{e.origin_y}{who}")
        return "\n".join(lines)

    def brief(self, map_id: int) -> str:
        """One line for a prompt header when the full board is too much."""
        row = self.get_scene(map_id)
        if row is None:
            return ""
        toks = [t for t in self.tokens(map_id) if not t.defeated]
        party = sum(1 for t in toks if t.team == Team.PARTY)
        foes = sum(1 for t in toks if t.team == Team.FOE)
        return (f"{row.name}: {row.width}x{row.height} board, "
                f"{party} allies vs {foes} foes, {row.lighting} light")

    # ================================================================ plumbing

    def _round(self, row: TacticalMap) -> int:
        if self.tracker is not None and row.encounter_id:
            try:
                enc = self.tracker.get_encounter(row.encounter_id)
                if enc:
                    return int(enc.round)
            except Exception:
                pass
        return 1

    def _current_token_id(self, row: TacticalMap,
                          toks: list[MapToken]) -> Optional[int]:
        """Whose turn it is, translated from the initiative tracker."""
        if self.tracker is None or not row.encounter_id:
            return None
        try:
            cur = self.tracker.current_combatant(row.encounter_id)
        except Exception:
            return None
        if cur is None:
            return None
        for t in toks:
            if t.combatant_id == cur.id:
                return t.id
        return None

    def _set_fields(self, map_id: int, **fields) -> None:
        with Session(self.engine) as s:
            row = s.get(TacticalMap, map_id)
            if not row:
                return
            for k, v in fields.items():
                setattr(row, k, v)
            row.revision += 1
            row.updated_at = _now()
            s.add(row)
            s.commit()

    def _bump(self, map_id: int) -> None:
        self._set_fields(map_id)

    def _log(self, map_id: int, session_id: Optional[str], kind: str, *,
             actor: Optional[str] = None, summary: Optional[str] = None,
             payload: Optional[dict] = None) -> None:
        try:
            with Session(self.engine) as s:
                row = s.get(TacticalMap, map_id)
                s.add(MapEvent(
                    map_id=map_id,
                    session_id=session_id or (row.session_id if row else None),
                    round=self._round(row) if row else None,
                    kind=kind, actor=actor, summary=summary, payload=payload))
                s.commit()
        except Exception as e:  # telemetry must never break play
            print(f"[vtt] log failed: {e}")

    def events(self, map_id: int, limit: int = 100) -> list[MapEvent]:
        with Session(self.engine) as s:
            return list(s.exec(
                select(MapEvent).where(MapEvent.map_id == map_id)
                .order_by(MapEvent.id.desc()).limit(limit)  # type: ignore[attr-defined]
            ).all())


# ------------------------------------------------------------------ helpers

def _now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


_EFFECT_COLORS = {
    "fire": "#ff6b35", "flame": "#ff6b35", "burn": "#ff6b35",
    "ice": "#7fd7ff", "cold": "#7fd7ff", "frost": "#7fd7ff",
    "acid": "#9bd94a", "poison": "#7bc043", "web": "#d8d8d8",
    "grease": "#b58b3c", "light": "#ffd479", "dark": "#4b3a63",
    "holy": "#ffe8a3", "necro": "#8a4fbf", "lightning": "#8ecbff",
    "thunder": "#c9b6ff",
}


def _effect_color(kind: str, name: str) -> str:
    n = (name or "").lower()
    for word, color in _EFFECT_COLORS.items():
        if word in n:
            return color
    return {EffectKind.AURA: "#ffd479", EffectKind.LIGHT: "#ffd479",
            EffectKind.HAZARD: "#ff6b35", EffectKind.WALL: "#b0b0b0",
            EffectKind.MARKER: "#8ecbff"}.get(kind, "#a86bff")


def _grounded(t: MapToken) -> bool:
    """Is this creature in contact with the ground? Tremorsense's whole question."""
    return int(t.elevation_ft or 0) <= 0 and (t.movement_mode or "walk") != "fly"


def _token_dict(t: MapToken, row: TacticalMap) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "kind": t.kind,
        "team": t.team,
        "x": t.x, "y": t.y,
        "size": t.size,
        "squares": size_squares(t.size),
        "combatant_id": t.combatant_id,
        "character_id": t.character_id,
        "monster_slug": t.monster_slug,
        "image_id": t.image_id,
        "color": t.color,
        "label": t.label,
        "speed_ft": t.speed_ft,
        "reach_ft": t.reach_ft,
        "moved_ft": t.moved_ft,
        "movement_mode": t.movement_mode,
        "elevation_ft": t.elevation_ft,
        "hidden": t.hidden,
        "stealth_dc": t.stealth_dc,
        "found_by": list(t.found_by or []),
        "senses": (t.senses if isinstance(t.senses, dict) else {}),
        "swim_speed_ft": t.swim_speed_ft,
        "mounted_on": t.mounted_on,
        "squeezing": bool(t.squeezing),
        "level": int(t.level or 0),
        "prone": t.prone,
        "defeated": t.defeated,
    }


def _effect_dict(e: MapEffect) -> dict:
    return {
        "id": e.id,
        "name": e.name,
        "kind": e.kind,
        "shape": e.shape,
        "x": e.origin_x, "y": e.origin_y,
        "radius_ft": e.radius_ft,
        "length_ft": e.length_ft,
        "width_ft": e.width_ft,
        "direction_deg": e.direction_deg,
        "squares": e.squares or [],
        "color": e.color,
        "opacity": e.opacity,
        "icon": e.icon,
        "difficult_terrain": e.difficult_terrain,
        "blocks_sight": e.blocks_sight,
        "obscured": e.obscured,
        "silences": bool(getattr(e, "silences", False)),
        "level": int(getattr(e, "level", 0) or 0),
        "damage": e.damage,
        "save_ability": e.save_ability,
        "save_dc": e.save_dc,
        "trigger": e.trigger,
        "source_token_id": e.source_token_id,
        "concentration": e.concentration,
        "expires_round": e.expires_round,
    }
