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

import os
import random
from pathlib import Path
from typing import Any, Iterable, Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from . import geometry as geo
from .art import render_battlemap, render_debris, layout_signature
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
from .terrain import Grid, tile, required_mode, object_stats

Square = tuple[int, int]


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

#: Team colours the overlay falls back to when a token has no portrait.
TEAM_COLORS = {Team.PARTY: "#4fa3ff", Team.FOE: "#ff5a5a", Team.NEUTRAL: "#d9b25a"}


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

    def create_tables(self) -> None:
        SQLModel.metadata.create_all(self.engine)

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
                   auto_close: bool = True) -> TacticalMap:
        """Open a tactical board for a session, closing any board already out.

        ``archetype`` may be a generator name or loose DM language ("a smoky
        taproom") — :func:`vtt.mapgen.archetype_for` maps it. When
        ``reuse_place`` is on and this place already had a board, its layout and
        art come back instead of being regenerated, so a room the party fought
        in last week looks the same today.

        ``auto_close`` marks a board the *system* put out (a fight started), so
        the system may take it away again when the reason passes. A board the DM
        opened by hand is theirs until they close it — nothing tidies it up
        underneath them.
        """
        kind = kind if kind in SceneKind.ALL else SceneKind.COMBAT
        arch = archetype_for(archetype or place_hint or "", default="open")
        w, h = DEFAULT_SIZE.get(kind, (24, 18))
        w, h = int(width or w), int(height or h)

        prior = self._prior_place_map(place_slug, arch, w, h) if (
            reuse_place and place_slug) else None
        seed = int(seed if seed is not None else (
            prior.seed if prior else random.randint(1, 2**31 - 1)))

        gen = generate_map(arch, width=w, height=h, seed=seed, lighting=lighting)

        self.close_scene(session_id=session_id)

        row = TacticalMap(
            session_id=session_id, encounter_id=encounter_id,
            place_slug=place_slug,
            name=(name or (place_hint or gen.description or "Tactical Scene"))[:120],
            kind=kind, archetype=arch, biome=biome,
            width=gen.width, height=gen.height, square_ft=5,
            terrain=gen.grid.to_rows(), elevation=gen.elevation or None,
            doors=gen.doors or None, lighting=gen.lighting,
            fog=self._blank_fog(gen.width, gen.height) if fog else None,
            seed=seed, active=True, revision=1,
            notes={"description": gen.description,
                   "auto_close": bool(auto_close),
                   # The medium this board is fought in (walk|swim|fly) — spawn
                   # placement and default token movement follow it.
                   "mode": gen.mode,
                   "spawn_party": [list(s) for s in gen.spawn_party[:60]],
                   "spawn_foes": [list(s) for s in gen.spawn_foes[:60]]},
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
        # Pin the picture to the layout as GENERATED, not as currently damaged.
        # Terrain changes hash into the signature, so without this a smashed
        # pillar would re-render the entire room mid-fight — for one square.
        # What the party broke is painted on top instead (see debris_for).
        pristine = generate_map(row.archetype, width=row.width, height=row.height,
                                seed=row.seed, lighting=row.lighting)
        ref = layout_signature(pristine.grid, pristine.archetype, pristine.seed)
        self._set_fields(map_id, art_status="pending")
        art = render_battlemap(
            gen, store=self.image_store, name=row.name, biome=row.biome,
            lighting=row.lighting, extra=extra, conditions=conditions,
            ref_slug=ref)
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
                           seed=row.seed, lighting=row.lighting)
        # The stored terrain wins — a DM may have edited it after generation.
        gen.grid = self.grid_of(row)
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

    def grid_of(self, row: TacticalMap) -> Grid:
        return Grid.from_rows(row.terrain or [])

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
        deb = dict(row.debris or {})
        deb[f"{x},{y}"] = {"code": becomes, "was": obj["name"],
                           "material": obj.get("material", ""), "image_id": None}
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
                out.append({"x": x, "y": y, **entry})
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
                   "restrained", "grappled_by"}
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
                  ignore_teams: Iterable[str] = ()) -> set[Square]:
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
        g = self.grid_of(row)
        budget = int(budget_ft if budget_ft is not None
                     else max(0, tok.speed_ft * (2 if dash else 1) - tok.moved_ft))
        blocked = self._occupied(tok.map_id, exclude=token_id,
                                 ignore_teams=(tok.team,) if tok.team else ())
        hard = self._occupied(tok.map_id, exclude=token_id)
        costs = geo.reachable_costs(
            g, (tok.x, tok.y), budget, size=size_squares(tok.size),
            mode=tok.movement_mode, blocked=blocked,
            extra_cost=self._effect_cost_fn(tok.map_id, tok.movement_mode, row),
            square_ft=row.square_ft)
        return {
            "token_id": token_id,
            "budget_ft": budget,
            "squares": [{"x": x, "y": y, "cost": c} for (x, y), c in costs.items()
                        if (x, y) not in hard or (x, y) == (tok.x, tok.y)],
        }

    def path_preview(self, token_id: int, x: int, y: int) -> dict:
        """The route a token would take to a square, and what it would cost."""
        tok = self.get_token(token_id)
        if not tok:
            return {"ok": False, "reason": "no such token"}
        row = self.get_scene(tok.map_id)
        if row is None:
            return {"ok": False, "reason": "no board"}
        g = self.grid_of(row)
        blocked = self._occupied(tok.map_id, exclude=token_id,
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
        g = self.grid_of(row)
        n = size_squares(tok.size)
        dest = (int(x), int(y))
        hard_blocked = self._occupied(tok.map_id, exclude=token_id)
        soft_blocked = self._occupied(tok.map_id, exclude=token_id,
                                      ignore_teams=(tok.team,) if tok.team else ())

        if not g.in_bounds(*dest):
            return {"ok": False, "reason": "that square is off the map"}
        if not geo._fits(g, dest, n, mode=tok.movement_mode, blocked=hard_blocked):
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
            path, cost = geo.find_path(
                g, (tok.x, tok.y), dest, size=n, mode=tok.movement_mode,
                blocked=soft_blocked,
                extra_cost=self._effect_cost_fn(tok.map_id, tok.movement_mode, row),
                square_ft=row.square_ft)
            if not path:
                return {"ok": False,
                        "reason": f"there's no way through to that square"}
            # Crawling costs an extra foot for every foot — so, twice.
            crawling = tok.prone and tok.movement_mode == "walk"
            if crawling:
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
        broke = self._break_far_grapples(tok.map_id, row)
        if broke:
            out["grapples_broken"] = broke
        # Stepping off a ledge is a fact the DM should narrate (and charge for);
        # the board reports the drop rather than silently applying damage.
        drop = self._drop_ft(row, (tok.x, tok.y), dest)
        if drop >= 10 and tok.movement_mode != "fly":
            out["fall_ft"] = drop
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
        self.update_token(b.id, prone=True)
        return {"ok": True, "detail": f"{b.name} drops prone."}

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
        own = int(tok.elevation_ft or 0)
        return own if own else VttEngine._height_at(row, (tok.x, tok.y))

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
        return geo.cover_between(
            g, (a.x, a.y), (b.x, b.y),
            attacker_size=size_squares(a.size), target_size=size_squares(b.size),
            obstacles=obstacles)

    def can_see(self, map_id: int, a_ref: str, b_ref: str) -> bool:
        a, b = self.find_token(map_id, a_ref), self.find_token(map_id, b_ref)
        row = self.get_scene(map_id)
        if not (a and b and row):
            return False
        if self._is_linked(map_id, a_ref, b_ref):
            return True
        g = self.grid_of(row)
        blockers = {tuple(p) for eff in self.effects(map_id) if eff.blocks_sight
                    for p in (eff.squares or [])}
        return geo.has_line_of_sight(
            g, (a.x, a.y), (b.x, b.y),
            a_size=size_squares(a.size), b_size=size_squares(b.size),
            blocker=lambda x, y: g.blocks_sight(x, y) or (x, y) in blockers)

    # =================================================================== fog

    @staticmethod
    def _blank_fog(w: int, h: int) -> list[str]:
        return ["0" * w for _ in range(h)]

    def reveal(self, map_id: int, x: int, y: int, radius_ft: int = 30) -> int:
        """Reveal what can be seen from a square. Returns squares newly lit."""
        row = self.get_scene(map_id)
        if row is None or not row.fog:
            return 0
        g = self.grid_of(row)
        fog = [list(r) for r in row.fog]
        n = 0
        for sx, sy in geo.visible_squares(g, (int(x), int(y)), radius_ft,
                                          square_ft=row.square_ft):
            if 0 <= sy < len(fog) and 0 <= sx < len(fog[sy]) and fog[sy][sx] == "0":
                fog[sy][sx] = "1"
                n += 1
        if n:
            self._set_fields(map_id, fog=["".join(r) for r in fog])
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
            total += self.reveal(map_id, t.x, t.y, r)
        return total

    def clear_fog(self, map_id: int) -> None:
        self._set_fields(map_id, fog=None)

    # ================================================================= views

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
            "fog": row.fog or None,
            "doors": row.doors or [],
            "elevation": row.elevation or {},
            "debris": self.debris_for(map_id),
            "background_image_id": row.background_image_id,
            "art_status": row.art_status,
            "description": (row.notes or {}).get("description", ""),
            "tokens": [_token_dict(t, row) for t in toks
                       if not (t.hidden and viewer_team != Team.FOE)],
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
        lines = [f"# Board: {row.name} — {row.width}x{row.height} squares "
                 f"({row.square_ft} ft each), {light}{medium}"]
        desc = (row.notes or {}).get("description")
        if desc:
            lines.append(f"  {desc}")
        for y in range(g.height):
            out = []
            for x in range(g.width):
                out.append(marks.get((x, y), g.get(x, y)))
            lines.append("".join(out))
        legend = g.legend(rules=True)
        if legend:
            lines.append(f"terrain: {legend}")
            lines.append("  Terrain is enforced: a creature cannot enter a square "
                         "its movement forbids, and difficult ground costs double. "
                         "Narrate within that, and the board will never contradict you. "
                         "Furniture that grants cover can be attacked and broken "
                         "([[VTT: damage | x,y | amount | type]]).")
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
            if actor is not None and actor.id == t.id:
                extras.append("ACTING NOW")
            if t.prone:
                extras.append("prone")
            if t.hidden:
                extras.append("hidden")
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
        "damage": e.damage,
        "save_ability": e.save_ability,
        "save_dc": e.save_dc,
        "trigger": e.trigger,
        "source_token_id": e.source_token_id,
        "concentration": e.concentration,
        "expires_round": e.expires_round,
    }
