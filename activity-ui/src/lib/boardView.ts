/** What every tactical-board renderer agrees on.
 *
 *  The board is drawn three ways across this project — the server's PNG for
 *  Discord tables (`vtt/render_image.py`), the flat canvas here
 *  (`vttPaint.ts`), and the isometric scene (`vttScene3d.ts`) — and all of them
 *  read the identical `VttEngine.state()` payload. This module holds the part
 *  the two BROWSER renderers share: the camera numbers, the tile palette, the
 *  draw-call arguments, and the interface `VttOverlay` talks to so it never has
 *  to know which one it is holding.
 *
 *  Deliberately free of any renderer import, so `vttPaint.ts` and
 *  `vttScene3d.ts` can both depend on it without a cycle. The canvas adapter
 *  lives next door in `canvasBoardView.ts` for the same reason.
 *
 *  ## Why one `View` serves both
 *
 *  A flat top-down board and a FIXED isometric one need exactly the same three
 *  numbers: a zoom and a two-axis screen offset. The isometric camera never
 *  rotates and is orthographic, so its projection is a plain affine transform —
 *  panning and zooming it is a translate-and-scale of the projected image,
 *  which is what `scale`/`ox`/`oy` already mean here. That is why the camera can
 *  stay in React state exactly where it has always been, and why swapping
 *  renderers is not also a state refactor.
 *
 *  (It is also why rotation is not offered. The moment the camera can turn,
 *  three numbers stop being enough — and a baked painting stops lining up with
 *  the geometry it was conditioned on.) */
import type { VttScene } from "./types";
// The board's SHAPES are owned by Python (vtt/isocam.py, vtt/terrain.py) and
// generated into that file, because the depth map the painted layer is
// conditioned on is rasterized from the same numbers. Hand-mirroring them was
// a drift waiting to happen, and the failure mode is invisible: the painting
// lands on furniture the player is not looking at.
export {
  COVER_HEIGHT_FT, DECOR_KINDS, DECOR_TINT, HEIGHT_JITTER, HOLE_CODES,
  MAX_DECOR_HEIGHT_FT, OBJECT_VARIANTS, SKINS, SKIRT_FT,
  GROUND_RIPPLE_FT, SKIRT_INSET, SMOOTH_STEP_FT, SOFT_GROUND,
  STRUCTURE_CODES,
  TILE_HEIGHT_FT, WALL_THICKNESS, isSolid,
} from "./boardShapes.generated";
export type {
  BoxPart, Part, PolyPart, SkinShape, SolidPart,
} from "./boardShapes.generated";
import {
  COVER_HEIGHT_FT as _COVER, HEIGHT_JITTER as _JITTER,
  HOLE_CODES as _HOLES, SKINS as _SKINS,
  GROUND_RIPPLE_FT as _RIPPLE, SMOOTH_STEP_FT as _SMOOTH,
  SOFT_GROUND as _SOFT,
  STRUCTURE_CODES as _STRUCTURE,
  TILE_HEIGHT_FT as _HEIGHT, WALL_THICKNESS as _THICK, isSolid as _isSolid,
} from "./boardShapes.generated";
import type { Part } from "./boardShapes.generated";
// The camera, for the one question that needs it here: which way a view ray
// travels. Not a renderer import — isocam is arithmetic, and both renderers
// already sit on top of it.
import { VERTICAL_SQUEEZE, YAW_DEG, basis } from "./isocam";

/** A stable 32-bit hash of a square. Mirrors `_hash` in vtt/isocam.py.
 *
 *  Both sides must agree exactly or a square picks one arrangement in the depth
 *  map and a different one in the geometry — so the Python side masks to 32
 *  bits to match what JavaScript's bitwise operators do to their operands. */
function hashOf(x: number, z: number, a: number, b: number): number {
  return ((x * a) ^ (z * b)) >>> 0;
}

/** Which arrangement this square's object uses.
 *
 *  Derived from the coordinates rather than rolled, so a room draws the same
 *  way every time — and the painter and the player pick the SAME one. */
export function variantOf(x: number, z: number, count: number): number {
  return count > 1 ? hashOf(x, z, 73856093, 19349663) % count : 0;
}

/** Quarter turns for this square's object. Breaks the grid-lock look. */
export function yawOf(x: number, z: number): number {
  return hashOf(x, z, 83492791, 29819387) & 3;
}

/** Like `variantOf`, but neighbouring squares usually agree.
 *
 *  For anything that is a MASS rather than a set of separate objects. A rock
 *  face whose every square picks its own height independently is a field of
 *  cubes — only the squares bordering open floor are drawn, so a one-square
 *  shell with per-square heights has nothing left to connect it. Sampling over
 *  two-square blocks turns the same variation into ridges and shelves.
 *  Mirrors `variant_smooth` in vtt/isocam.py. */
export function variantSmooth(x: number, z: number, count: number): number {
  if (count <= 1) return 0;
  return hashOf(Math.floor(x / 2), Math.floor(z / 2), 73856093, 19349663) % count;
}

/** Quarter turns that line a part up with the RUN it belongs to.
 *
 *  The companion to `yawOf`, for the things a per-square random turn gets
 *  wrong. A boulder may face any way; a ship's rail, a palisade and a tent wall
 *  are things that RUN, and turned individually they come out as a row of
 *  quarter-turned fragments rather than one continuous rail. Parts are authored
 *  running along x, so this returns 0 for an x-run and 1 for a z-run.
 *  Mirrors `run_axis` in vtt/isocam.py. */
export function runAxis(
  same: (x: number, z: number) => boolean, x: number, z: number,
): number {
  const alongX = (same(x - 1, z) ? 1 : 0) + (same(x + 1, z) ? 1 : 0);
  const alongZ = (same(x, z - 1) ? 1 : 0) + (same(x, z + 1) ? 1 : 0);
  return alongZ > alongX ? 1 : 0;
}

/** Which way a quarter turn sends a part authored facing +z (south).
 *  `(x, z) -> (1 - z, x)` per quarter, so south goes to west, then north, then
 *  east. Mirrors OUT_DIRS in vtt/isocam.py. */
const OUT_DIRS: readonly (readonly [number, number])[] =
  [[0, 1], [-1, 0], [0, -1], [1, 0]];

/** Quarter turns that point a part at the OUTDOORS.
 *
 *  The third orientation rule, and the one a tent needed. `yawOf` turns a
 *  boulder any way at all and `runAxis` lines a rail up with its run — but
 *  neither can answer "which side of this wall is the weather on", and without
 *  that a tent's canvas can only lean by the same amount in both directions,
 *  which is to say not at all. Directions are tried in a fixed order so a
 *  corner — which has two outsides — picks the same one in both languages.
 *  Mirrors `out_axis` in vtt/isocam.py. */
export function outAxis(
  inside: (x: number, z: number) => boolean, x: number, z: number,
  corner = false,
): number {
  const outs = OUT_DIRS.map(([dx, dz]) => !inside(x + dx, z + dz));
  if (corner) {
    // A part is authored facing +z, and a quarter turn sends its +x side one
    // step back round the compass — so a corner arrangement wants a turn where
    // both of those land on real outdoors.
    for (let t = 0; t < 4; t++) if (outs[t] && outs[(t + 3) % 4]) return t;
  }
  for (let t = 0; t < 4; t++) if (outs[t]) return t;
  return 0;
}

/** Does this square face the outdoors on two sides that MEET?
 *
 *  A tent's corner. Four of the twelve squares in a tent's wall ring are one,
 *  and a shape aimed at only one of their two outsides leaves the other a
 *  sheer face — so every tent had two pitched sides and two cliffs, which is
 *  what "the tents need corner pieces" means. Mirrors `out_corner` in
 *  vtt/isocam.py. */
export function outCorner(
  inside: (x: number, z: number) => boolean, x: number, z: number,
): boolean {
  const outs = OUT_DIRS.map(([dx, dz]) => !inside(x + dx, z + dz));
  if (outs.filter(Boolean).length !== 2) return false;
  for (let t = 0; t < 4; t++) if (outs[t] && outs[(t + 3) % 4]) return true;
  return false;
}

/** This square's skin name, or "" for the code's own default look.
 *
 *  A per-square override beats the archetype default, which is the whole point
 *  of having both: a camp is palisaded, and the tents inside it are canvas.
 *  Mirrors `skin_at` in vtt/skins.py. */
export function skinAt(
  scene: { skins?: { codes?: Record<string, string>;
                     squares?: Record<string, string> } },
  code: string, x: number, y: number,
): string {
  const sk = scene.skins;
  if (!sk) return "";
  return sk.squares?.[`${x},${y}`] ?? sk.codes?.[code] ?? "";
}

/** Marks a square stamped by a vtt/setpieces landmark. Mirrors
 *  `skins.SETPIECE_PREFIX`. */
export const SETPIECE_PREFIX = "setpiece:";

/** Is this square drawn by a landmark's MESH rather than by its own shape?
 *
 *  A prefix rather than an entry in SKINS because a mesh is a shape per
 *  LANDMARK, not per square, so there is nothing for the per-square vocabulary
 *  to hold — and every other skin lookup falls back to the code's own default
 *  for a name it does not know, which here would draw the ordinary block AND
 *  the mesh. See vtt/skins.py `is_setpiece`. */
export function isSetpieceSkin(name: string): boolean {
  return (name || "").startsWith(SETPIECE_PREFIX);
}

/** The angle to give a landmark's `rotation.y`, in radians.
 *
 *  NEGATED, and that is not a fudge. A quarter turn has to move the mesh the
 *  way it moves the TILES, or the picture turns and the cover does not — a
 *  creature takes three-quarters cover from a face of the statue now behind
 *  it. `setpieces._turned` sends (x,z) to (-z,x) at 90 degrees; three.js
 *  `rotation.y` is the other handedness and would send it to (z,-x). */
export function setpieceYaw(yawFix: number, yaw: number): number {
  return -(((yawFix || 0) + (yaw || 0)) * Math.PI / 180);
}

/** Where a point of a landmark's mesh lands after its quarter turn.
 *
 *  Exists so the gate can run the same points through both languages —
 *  `rotation.y` itself is inside three.js and cannot be compared. Mirrors
 *  `vtt.setpieces.rotate_xz`; see scripts/iso_alignment_check.py. */
export function setpieceRotate(x: number, z: number, deg: number): [number, number] {
  const a = setpieceYaw(0, deg);
  const c = Math.cos(a), s = Math.sin(a);
  // The rotation three.js applies for `rotation.y = a`.
  return [x * c + z * s, -x * s + z * c];
}

/** The material slot a square draws from — a tile code, or `code@skin`.
 *  Must agree with `materials_for` in vtt/scene.py, which builds the same key
 *  server-side. */
export function materialSlot(code: string, skin: string): string {
  return skin ? `${code}@${skin}` : code;
}

/** Per-instance height multiplier, in [1 - JITTER, 1].
 *
 *  **Never varies a height the RULES quote.** A crate screens four feet and a
 *  low wall three, and a player deciding whether to break line of sight reads
 *  that off the board — which is most of what fighting something stronger than
 *  you consists of. Drawing one crate shorter than another would invent a
 *  difference the engine will not honour, in exactly the situation where being
 *  misled is expensive. A non-zero cover height marks those tiles. */
export function heightScale(code: string, x: number, z: number): number {
  if ((_COVER[code] ?? 0) > 0) return 1;
  return 1 - _JITTER * (hashOf(x, z, 19349663, 83492791) & 255) / 255;
}

/** Turn one part's footprint a quarter at a time about its square's centre.
 *
 *  `(x, z) -> (1 - z, x)` per quarter, which keeps it inside the square — and
 *  keeps a shape that is symmetric about the centre exactly where it was, which
 *  is what lets a watchtower's roof reach out over the whole footprint from one
 *  square without a random turn moving it. Both part forms rotate by the same
 *  map. Mirrors `rotate_part` in vtt/isocam.py. */
export function rotatePart(part: Part, turns: number): Part {
  const t = turns & 3;
  if (_isSolid(part)) {
    let [bottom, top] = part;
    const [, , y0, y1] = part;
    for (let i = 0; i < t; i++) {
      bottom = bottom.map(([x, z]) => [1 - z, x] as const);
      top = top.map(([x, z]) => [1 - z, x] as const);
    }
    return [bottom, top, y0, y1];
  }
  let [x0, x1, z0, z1] = part;
  const [, , , , y0, y1] = part;
  for (let i = 0; i < t; i++) {
    [x0, x1, z0, z1] = [1 - z1, 1 - z0, x0, x1];
  }
  return [x0, x1, z0, z1, y0, y1];
}

/** Are these two squares part of one THING?
 *
 *  A ship is a deck, a rail round it, a mast through it and a cabin on it —
 *  four skins and one hull, so no side is drawn between any of them. Mirrors
 *  `same_body` in vtt/skins.py. */
export function sameBody(a: string, b: string): boolean {
  if (a === b) return true;
  const ba = _SKINS[a]?.body ?? "";
  return !!ba && ba === (_SKINS[b]?.body ?? "");
}

/** The square's four corners, in the order the floor's top face is wound —
 *  counter-clockwise seen from above, so the face's normal points up. */
const CORNERS: readonly (readonly [number, number])[] =
  [[0, 0], [0, 1], [1, 1], [1, 0]];

/** Where each outline vertex sits at the BOTTOM of the side below it.
 *
 *  A MITRE, not a per-edge offset. Pulling each side's bottom straight back
 *  along its own normal keeps two collinear sides coplanar — but where the
 *  outline turns, the two sides' bottoms part company and leave a notch, which
 *  on a hull is a wedge of daylight at every corner of the bow. Offsetting the
 *  VERTEX along the bisector gives both sides the same bottom point, so the
 *  shell closes. Mirrors `_outline_bottoms` in vtt/isocam.py. */
function outlineBottoms(pts: [number, number][], ends: boolean[],
                        inset: number): [number, number][] {
  const n = pts.length;
  if (inset <= 0 || n < 3) return pts.map(([x, z]) => [x, z] as [number, number]);
  const normal = (i: number): [number, number] | null => {
    const [ax, az] = pts[i];
    const [bx, bz] = pts[(i + 1) % n];
    const ex = bx - ax;
    const ez = bz - az;
    const run = Math.hypot(ex, ez);
    if (run < 1e-9) return null;
    return [-ez / run, ex / run];       // outward, for a ring wound CCW above
  };
  const out: [number, number][] = [];
  for (let i = 0; i < n; i++) {
    const a = ends[(i + n - 1) % n] ? normal((i + n - 1) % n) : null;
    const b = ends[i] ? normal(i) : null;
    let dx = 0;
    let dz = 0;
    if (a && b) {
      let bx = a[0] + b[0];
      let bz = a[1] + b[1];
      const mag = Math.hypot(bx, bz);
      if (mag < 1e-9) {                 // a spike doubling back on itself
        dx = a[0] * inset;
        dz = a[1] * inset;
      } else {
        bx /= mag;
        bz /= mag;
        // 1/cos(half angle), clamped so a very sharp corner does not throw its
        // bottom vertex across the board.
        const k = inset / Math.max(0.3, bx * a[0] + bz * a[1]);
        dx = bx * k;
        dz = bz * k;
      }
    } else if (a || b) {
      const m = (a || b) as [number, number];
      dx = m[0] * inset;
      dz = m[1] * inset;
    }
    out.push([pts[i][0] - dx, pts[i][1] - dz]);
  }
  return out;
}

/** This square's floor outline, which edges face the outside, and where the
 *  bottom of each side sits.
 *
 *  A square's outline is its square. There WAS a corner chamfer here, cutting
 *  each stair step's outer corner so a carved hull read as a line rather than
 *  a flight of steps — it worked, and it was superseded: a one-square cut is
 *  still a one-square answer, and joining the corners farthest from a hull's
 *  middle needs the outline as a LOOP, which no square can see. That is
 *  `scene.shells`, traced once on the server. Removed rather than left
 *  standing, because a code path nothing reaches is a trap for whoever comes
 *  next. Mirrors `footprint` in vtt/isocam.py. */
export function hullFootprint(
  eW: boolean, eE: boolean, eN: boolean, eS: boolean, inset = 0,
): { pts: [number, number][]; ends: boolean[]; low: [number, number][] } {
  const sideEnds = [eW, eS, eE, eN];        // edge i runs corner i -> corner i+1
  const square = CORNERS.map(([x, z]) => [x, z] as [number, number]);
  return { pts: square, ends: sideEnds,
           low: outlineBottoms(square, sideEnds, inset) };
}

/** Draw at exactly the stated height, or with a little per-instance life?
 *  Anything BUILT is exact: a jittered post is a platform standing clear of
 *  its own legs. Mirrors `is_exact` in vtt/skins.py. */
export function skinHeightScale(skin: string, code: string,
                                x: number, z: number): number {
  return _SKINS[skin]?.exact ? 1 : heightScale(code, x, z);
}

/** Zoom and screen offset. See the note above on why this suits both renderers. */
export interface View {
  scale: number;
  ox: number;
  oy: number;
  /** Which way the camera is turned, in degrees. Absent = `YAW_DEG`, the
   *  canonical angle everything the SERVER makes is aligned to.
   *
   *  Optional rather than required so that every View built before rotation
   *  existed — and every one persisted in a layout — still means what it meant.
   *  A viewer may put this anywhere; nothing that has to agree with the server
   *  may. */
  yaw?: number;
}

/** Screen pixels per square at zoom 1. The base unit both renderers scale from. */
export const CELL = 44;

type TileStyle = {
  fill: string;
  edge?: string;
  family: "floor" | "solid" | "water" | "rough" | "hazard" | "void";
};

/** Tile code -> how it paints. Mirrors vtt/terrain.py's TILES table.
 *
 *  Shared rather than owned by the flat renderer: the isometric board needs the
 *  same answer for its pre-painting state, and `family` is what tells it which
 *  codes to extrude as walls. Two palettes would be two rooms. */
export const TILE_STYLES: Record<string, TileStyle> = {
  ".": { fill: "#242a42", family: "floor" },
  g: { fill: "#2b3f31", family: "floor" },
  s: { fill: "#453b28", family: "floor" },
  b: { fill: "#4a3722", family: "floor" },
  "=": { fill: "#33384a", family: "floor" },
  ",": { fill: "#3a3a42", family: "rough" },
  '"': { fill: "#2f4433", family: "rough" },
  "~": { fill: "#1d4257", family: "water" },
  m: { fill: "#3a3126", family: "rough" },
  i: { fill: "#3b5566", family: "rough" },
  "%": { fill: "#4a4a52", family: "rough" },
  u: { fill: "#3b405c", family: "rough" },
  "#": { fill: "#10141f", edge: "#46527b", family: "solid" },
  R: { fill: "#1d1f2a", edge: "#3c3f52", family: "solid" },
  T: { fill: "#22371f", edge: "#4f7d4a", family: "solid" },
  O: { fill: "#3a4270", edge: "#7d8cc8", family: "solid" },
  o: { fill: "#4a3823", edge: "#8a6a33", family: "solid" },
  n: { fill: "#42311f", edge: "#7a5c30", family: "solid" },
  w: { fill: "#242a3d", edge: "#4a5478", family: "solid" },
  W: { fill: "#10293b", family: "water" },
  "^": { fill: "#141c33", family: "void" },
  x: { fill: "#06080e", edge: "#38406a", family: "hazard" },
  l: { fill: "#5a1c0c", edge: "#ff7a33", family: "hazard" },
  f: { fill: "#4a2410", edge: "#ff9a4a", family: "hazard" },
  A: { fill: "#3a3159", edge: "#8878c0", family: "solid" },
  "+": { fill: "#4b3a20", edge: "#a07c3c", family: "solid" },
  "/": { fill: "#3a3f57", edge: "#a07c3c", family: "floor" },
  p: { fill: "#2a2f42", edge: "#6a7290", family: "solid" },
  " ": { fill: "#05070d", family: "void" },
};

export function tileStyle(code: string): TileStyle {
  return TILE_STYLES[code] ?? TILE_STYLES["."];
}

/** How tall this tile stands, in feet. 0 is floor you walk on.
 *
 *  A DRAWING height, distinct from the rules' `cover_height_ft` — a pillar
 *  needs a figure even though the rules only care that it is narrow. */
export function tileHeightFt(code: string): number {
  return _HEIGHT[code] ?? 0;
}





/** Footprint(s) for a wall square: `[x0, x1, z0, z1]` offsets within it.
 *
 *  Drawn as the FACE of the solid region rather than the square's own run.
 *  Keying on run direction was tried twice and came out crenellated both times:
 *  mapgen walls are commonly two squares thick, so every square in the band
 *  reads as a corner and draws a plus, and a band of pluses notches at every
 *  seam. What you actually see of a thick wall is the skin where it meets open
 *  floor — and a buried square draws NOTHING, which is most of a thick band.
 *
 *  **A THIN wall is a different thing and needs the other treatment.** Where a
 *  square has floor on more than one side it is not the skin of a mass but a
 *  wall with two faces: a cabin's bulkhead, a ruin's standing course. Hugging
 *  each open side draws TWO slabs with a slot down the middle, which is
 *  exactly how a ship's deckhouse came back with double walls and a corridor
 *  between them. So a thin wall is drawn on its CENTRELINE instead — a hub and
 *  an arm toward each square the wall carries on into — which gives one wall,
 *  mitres its own corners, and stops cleanly at a stub end.
 *
 *  Mirrors `wall_parts` in vtt/isocam.py. */
export function wallParts(isOpen: (x: number, z: number) => boolean,
                          x: number, z: number):
    (readonly [number, number, number, number])[] {
  const t = _THICK;
  const n = isOpen(x, z - 1);
  const s = isOpen(x, z + 1);
  const w = isOpen(x - 1, z);
  const e = isOpen(x + 1, z);
  const out: (readonly [number, number, number, number])[] = [];
  if ((n ? 1 : 0) + (s ? 1 : 0) + (w ? 1 : 0) + (e ? 1 : 0) >= 2) {
    const lo = 0.5 - t / 2;
    const hi = 0.5 + t / 2;
    out.push([lo, hi, lo, hi]);                     // the hub
    if (!n) out.push([lo, hi, 0, hi]);              // the wall carries on
    if (!s) out.push([lo, hi, lo, 1]);
    if (!w) out.push([0, hi, lo, hi]);
    if (!e) out.push([lo, 1, lo, hi]);
    return out;
  }
  if (n) out.push([0, 1, 0, t]);
  if (s) out.push([0, 1, 1 - t, 1]);
  if (w) out.push([0, t, 0, 1]);
  if (e) out.push([1 - t, 1, 0, 1]);
  return out;
}



/** Is any of this solid square's EIGHT neighbours open floor?
 *
 *  `wallParts` asks the orthogonal question, because a wall's faces are
 *  orthogonal. A rock MASS needs the diagonal one too: a pass's track wanders,
 *  so the rock shell beside it steps diagonally as often as not, and a square
 *  whose only open neighbour is a diagonal was drawn as buried — which left a
 *  notch at every step and turned the face into a row of separate towers.
 *  Mirrors `exposed` in vtt/isocam.py. */
export function exposedRock(isOpen: (x: number, z: number) => boolean,
                            x: number, z: number): boolean {
  if (wallParts(isOpen, x, z).length) return true;
  return isOpen(x - 1, z - 1) || isOpen(x + 1, z - 1)
      || isOpen(x - 1, z + 1) || isOpen(x + 1, z + 1);
}

/** The tile rows for one storey.
 *
 *  `scene.terrain` is the GROUND floor and always has been — `state()` ships it
 *  where it has always been and repeats every floor's own inside `levels[]`. So
 *  anything that reads terrain and can be asked about an upper storey has to
 *  come through here, or it silently answers about the hall while the player is
 *  looking at the gallery. That was survivable while terrain reads were only
 *  the occlusion march (documented as ground-floor only); it stopped being
 *  survivable when the cutaway started deciding which walls to DRAW from it. */
function rowsOf(scene: VttScene, level = 0): readonly string[] {
  return (level ? scene.levels?.[level]?.terrain : undefined) ?? scene.terrain ?? [];
}

/** Is this square a FLOOR — something you could stand on and see the top of? */
function isFloorCode(code: string | null | undefined): code is string {
  return !!code && !_HOLES.has(code) && !_STRUCTURE.has(code)
    && tileHeightFt(code) <= 0;
}

function _commonest(tally: Map<string, number>): string | null {
  // Ties broken by name, so the same board draws the same way twice.
  return tally.size
    ? [...tally].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1))[0][0]
    : null;
}

const _DOMINANT = new WeakMap<object, Map<number, string | null>>();

/** The commonest floor on a storey — a board's own answer to "what is the
 *  ground here made of", for a square that has nothing to ask. */
export function dominantFloor(scene: VttScene, level = 0): string | null {
  let per = _DOMINANT.get(scene as object);
  if (!per) _DOMINANT.set(scene as object, (per = new Map()));
  if (per.has(level)) return per.get(level) ?? null;
  const rows = rowsOf(scene, level);
  const tally = new Map<string, number>();
  for (let z = 0; z < rows.length; z++) {
    for (let x = 0; x < rows[z].length; x++) {
      const c = rows[z][x];
      if (!isFloorCode(c)) continue;
      const sl = materialSlot(c, skinAt(scene, c, x, z));
      tally.set(sl, (tally.get(sl) ?? 0) + 1);
    }
  }
  const best = _commonest(tally);
  per.set(level, best);
  return best;
}

/** What the GROUND under this square is made of.
 *
 *  THE GROUND UNDER AN OBJECT IS NOT MADE OF THE OBJECT. The renderer had one
 *  mesh builder per square, chosen from that square's own tile code, and the
 *  floor fan went into it — so a crate square was drawn in the crate's
 *  material right out to its edges. Every crate came with a square yard of
 *  pine floor around it, every pillar stood on a disc of its own granite, and
 *  an altar on a slab of itself. Invisible for as long as the crate was the
 *  same grey-green as the road, and reported the moment it was not.
 *
 *  The tile code says a crate stands here and says NOTHING about what it
 *  stands on, so the answer is asked of the board in two tiers.
 *
 *  NEIGHBOURS first, because the local truth beats the average: a crate on a
 *  road is on cobbles even when the board is mostly grass.
 *
 *  Then the STOREY'S OWN commonest floor, and that tier is not a nicety —
 *  measured over every archetype at two seeds, **28.6% of object squares have
 *  no floor touching them at all**, and 796 of those 861 are on a CLEARING. A
 *  tree in the middle of a stand is surrounded by trees, so every one of them
 *  was drawn standing on a square of its own foliage: a green carpet under a
 *  wood, on every wooded board in the game. A crypt is the same fault with
 *  coffins. With both tiers, nothing anywhere falls through.
 *
 *  Things that FILL their square are left alone. A wall or a rock face covers
 *  its own ground, so there is nothing visible to get wrong, and the
 *  buried-face rules stay exactly as they were.
 */
export function groundSlot(scene: VttScene, x: number, z: number,
                           level = 0): string {
  const rows = rowsOf(scene, level);
  const code = rows[z]?.[x] ?? "";
  const own = materialSlot(code, skinAt(scene, code, x, z));
  if (_STRUCTURE.has(code) || tileHeightFt(code) <= 0) return own;
  const tally = new Map<string, number>();
  for (const [nx, nz] of [[x - 1, z], [x + 1, z], [x, z - 1], [x, z + 1]]) {
    const c = rows[nz]?.[nx];
    // A floor, not another object: a crate beside a crate says nothing about
    // the ground, and a wall says less.
    if (!isFloorCode(c)) continue;
    const sl = materialSlot(c, skinAt(scene, c, nx, nz));
    tally.set(sl, (tally.get(sl) ?? 0) + 1);
  }
  return _commonest(tally) ?? dominantFloor(scene, level) ?? own;
}

/** How tall the thing standing on this square is DRAWN, in feet above the
 *  storey's own floor — the ground's elevation plus whatever stands on it.
 *
 *  The same arithmetic the geometry is built from, jitter included, so the
 *  answer cannot disagree with what the player is looking at. A HOLE reports
 *  minus infinity rather than zero: nothing is drawn there at all, and a chasm
 *  is not a low wall — a creature down in a channel must not be hidden by the
 *  empty square in front of it.
 *
 *  A landmark's mesh counts at its declared height. Its stamped tiles are
 *  already in the answer, but a 40-ft gate tower stamps 10-ft masonry, and
 *  what stands between the camera and the creature is the tower. */
export function drawnTopFt(scene: VttScene, x: number, z: number,
                           yawDeg: number = YAW_DEG, level = 0): number {
  const row = rowsOf(scene, level)[z];
  // This storey's ground, not the ground floor's — see `rowsOf`.
  const elev = elevationAt(scene, x, z, level);
  let top = -Infinity;
  if (row !== undefined && x >= 0 && x < row.length) {
    const code = row[x];
    if (!_HOLES.has(code)) {
      const skin = skinAt(scene, code, x, z);
      const full = (_SKINS[skin]?.heightFt || _HEIGHT[code] || 0)
        * skinHeightScale(skin, code, x, z);
      // A cut wall really is that low now, and this is the one place the board
      // answers "what is standing in the way" — so a creature the cutaway
      // revealed must stop being reported as hidden by the thing that was cut.
      top = elev + full * cutawayHeightScale(scene, x, z, yawDeg, full, level);
    }
  }
  for (const sp of scene.setpieces ?? []) {
    if (x >= sp.x && z >= sp.y && x < sp.x + sp.w && z < sp.y + sp.d) {
      top = Math.max(top, elev + (sp.height_ft || 0));
    }
  }
  return top;
}

/** The GROUND's height at a grid corner, in feet — averaged where it may be.
 *
 *  Elevation is stored per square as whole feet, so a hillside is drawn as
 *  terraces: every square a flat plate at its own height with a step to its
 *  neighbour. Real ground does not do that, and the terracing is most of why
 *  an outdoor board reads as stacked blocks — the mountain pass came out a
 *  flight of stairs and a meadow with a knoll on it a wedding cake.
 *
 *  A corner is shared by up to four squares. It takes the mean of the ones
 *  that may JOIN this square: natural ground (a floor, a road, a bridge and a
 *  deck are LAID, and laid things are flat) within one STEP of it. A
 *  neighbour outside that is simply not counted, which is what keeps a ledge a
 *  cliff — the corner then reads this square's own height and the face between
 *  them stays vertical.
 *
 *  Drawing only. A creature stands at its square's stated height, every
 *  distance and cover check reads the integer, and only the surface between
 *  square centres bends. Mirrors `corner_lift_ft` in vtt/isocam.py, and the
 *  alignment gate compares them — a corner the two programs disagree about is
 *  a seam in the ground that the painting is then baked over. */
export function cornerLiftFt(scene: VttScene, x: number, z: number,
                             cx: number, cz: number, level = 0): number {
  const rows = rowsOf(scene, level);
  const own = elevationAt(scene, x, z, level);
  // A CORNER'S height must be a property of the CORNER, not of whichever
  // square is asking — anything that reads the asker's own code or height
  // gives the two squares sharing an edge two different answers there, and the
  // ground tears along every seam. So: the squares meeting at this corner, and
  // nothing else.
  const around: [number, number][] = [];
  for (const [ax, az] of [[cx - 1, cz - 1], [cx, cz - 1],
                          [cx - 1, cz], [cx, cz]] as [number, number][]) {
    if (rows[az]?.[ax] !== undefined) around.push([ax, az]);
  }
  if (!around.length) return own;
  if (around.some(([ax, az]) => !softAt(scene, ax, az, level))) return own;
  const fts = around.map(([ax, az]) => elevationAt(scene, ax, az, level));
  // A LEDGE is the height the rules make you decide about, and ramping one
  // draws a lie. The face between them stays vertical.
  if (Math.max(...fts) - Math.min(...fts) > _SMOOTH) return own;
  // …and a WANDER on top, hashed from the CORNER so both squares sharing it
  // get the same number and the ground cannot tear. Drawing only — see
  // GROUND_RIPPLE_FT — and the occlusion march never sees it.
  const wobble = ((hashOf(cx, cz, 26699, 45989) % 2048) / 2048 - 0.5) * 2;
  return fts.reduce((a, b) => a + b, 0) / fts.length + wobble * _RIPPLE;
}

/** May this square's surface slope?
 *
 *  The SKIN answers first, and has to: `.` is scree on a mountain pass and
 *  cobbles on a street, which is exactly the distinction a skin exists to
 *  make. A square wearing none falls back to the tile code. */
function softAt(scene: VttScene, x: number, z: number, level = 0): boolean {
  const code = rowsOf(scene, level)[z]?.[x];
  if (code === undefined) return false;
  const skin = _SKINS[skinAt(scene, code, x, z)];
  if (skin) return !!skin.soft;
  return _SOFT.has(code);
}

/** A square's own stored elevation, in feet above its storey's floor. */
export function elevationAt(scene: VttScene, x: number, z: number,
                            level = 0): number {
  return ((level ? scene.levels?.[level]?.elevation : undefined)
    ?? scene.elevation)?.[`${x},${z}`] ?? 0;
}

/** The WATER's surface over a square, in feet, or null where there is none.
 *
 *  Sparse and traced by the server (see vtt/water.py): water lies in a basin
 *  cut below its own bank, and this is the level sheet put back on top of it.
 *  Never derived here — a pool's surface is a property of the whole pool, and
 *  a second tracer in a second language is a second answer. */
export function waterAt(scene: VttScene, x: number, z: number,
                        level = 0): number | null {
  const w = (level ? scene.levels?.[level]?.water : undefined) ?? scene.water;
  const v = w?.[`${x},${z}`];
  return typeof v === "number" ? v : null;
}

/** The ground's height at a point INSIDE a square, bilinear over its corners.
 *
 *  `u`/`v` run 0..1 across the square. The floor is drawn as a fan over an
 *  outline that may have been chamfered, so its vertices are not only the four
 *  corners — every one of them has to land on the same surface or the fan
 *  tears. */
export function surfaceLiftFt(scene: VttScene, x: number, z: number,
                              u: number, v: number, level = 0): number {
  const a = cornerLiftFt(scene, x, z, x, z, level);
  const b = cornerLiftFt(scene, x, z, x + 1, z, level);
  const c = cornerLiftFt(scene, x, z, x, z + 1, level);
  const d = cornerLiftFt(scene, x, z, x + 1, z + 1, level);
  return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v;
}

// --------------------------------------------------------------------------
// Cutting away the near walls
//
// A room is a box, and an isometric camera looks into it over one of its
// corners — so the two walls nearest the lens are between the viewer and the
// fight. At the canonical angle that was survivable, because the wall is IN the
// painting and the painting is what you are looking at. It stopped being
// survivable the moment the camera could turn: swing a quarter and the wall
// that used to be the far one is now a ten-foot slab across the front of the
// board, hiding the half of the room the fight is in.
//
// The rule is one sentence: **cut the near walls exactly when you are looking
// at the geometry rather than at a painting of the room.** Where a painting is
// showing, the wall is a thing in that picture and no amount of not-drawing the
// geometry removes it — the geometry there is a depth-only proxy, so cutting it
// would delete the occlusion and change nothing anybody can see. Where no
// painting is showing — a board whose art has not been drawn, an offline table,
// or any angle the camera has turned to — the geometry IS the picture, and the
// near walls come down.
//
// It is a drawing decision and nothing else. Cover, sight, movement and reach
// all read the tile, which is untouched: a cut wall is still total cover and
// still impassable. What it must NOT do is contradict the board's own account
// of what is visible, which is why `drawnTopFt` applies the same reduction —
// so a creature the cutaway reveals stops being reported as hidden, from the
// one place that answers that question.
// --------------------------------------------------------------------------

/** How much of its height a cut wall keeps. */
const CUTAWAY_SCALE = 0.28;

/** ...but never less than this, in feet. A wall reduced to nothing leaves the
 *  floor looking like it is hanging in space; a low stub still says "the room
 *  ends here", which is the whole reason this is a cutaway and not a delete. */
const CUTAWAY_MIN_FT = 2.5;

/** How far to look for the floor a near wall is hiding, in squares.
 *
 *  Generated walls are commonly two squares thick, so a one-square test cuts
 *  the inner course and leaves the outer one standing. Three is enough for any
 *  wall and short enough that a MASS — a cliff, a mountainside — never
 *  qualifies: rock that is metres deep is not a wall in front of the room, it
 *  is the edge of the world, and slicing the top off it would read as a
 *  mountain someone had been at with a bread knife. */
const CUTAWAY_DEPTH = 3;

/** The eight ways "away from the camera" can point on a square grid. */
const _COMPASS: readonly (readonly [number, number])[] = [
  [1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1],
];

/** Which way is AWAY from the camera, to the nearest of eight.
 *
 *  Discretised on purpose. The exact direction changes with every degree of a
 *  drag, and the set of squares it picks out does not: it changes eight times
 *  in a full turn, at the moments the ray crosses a diagonal. Keying the mesh
 *  rebuild on this instead of on the raw angle is the difference between
 *  twenty-four rebuilds per turn and eight. */
export function awayDir(yawDeg: number = YAW_DEG): readonly [number, number] {
  const b = basis(yawDeg);
  // rayX/rayZ point TOWARD the lens, so away is the other way.
  const ang = Math.atan2(-b.rayZ, -b.rayX);
  const i = ((Math.round(ang / (Math.PI / 4)) % 8) + 8) % 8;
  return _COMPASS[i];
}

/** Are near walls being cut away on this board?
 *
 *  Always, now. It used to be "only where no PAINTING is showing", because
 *  under a painting the wall is a thing in the picture and not drawing the
 *  geometry removed nothing anybody could see. The painted layer is gone —
 *  it was a photograph of the room from one place and the camera turns — so
 *  the geometry IS the picture, everywhere, at every angle.
 *
 *  Kept as a function rather than folded away: what "in the way" means is
 *  about to change. At a fixed angle it is the two walls nearest the lens; with
 *  a camera the player can swing, it becomes whatever stands between the lens
 *  and the thing being looked at, which is a different question with the same
 *  name. */
export function cuttingAway(_scene: VttScene, _yawDeg: number = YAW_DEG): boolean {
  return true;
}

/** Is this square a near wall — one standing between the lens and open floor?
 *
 *  Structure only (`#`, `R`). That is not a shortcut: everything with a height
 *  the RULES quote — a crate at four feet, a low wall at three, a table, an
 *  altar — is an OBJECT and not structure, so restricting to structure is
 *  exactly the "never vary a height the rules quote" rule, arrived at from the
 *  other side. A player deciding whether they can break line of sight behind a
 *  crate must read that off the board; a wall is total cover at any height. */
export function cutAwayAt(scene: VttScene, x: number, z: number,
                          yawDeg: number = YAW_DEG, level = 0): boolean {
  if (!cuttingAway(scene, yawDeg)) return false;
  const rows = rowsOf(scene, level);
  const code = rows[z]?.[x];
  if (code === undefined || !_STRUCTURE.has(code)) return false;
  const [dx, dz] = awayDir(yawDeg);
  for (let n = 1; n <= CUTAWAY_DEPTH; n++) {
    const c = rows[z + dz * n]?.[x + dx * n];
    if (c === undefined) return false;              // off the board: an outer face
    if (_HOLES.has(c)) return false;                // a chasm is not a room
    if (!_STRUCTURE.has(c)) return true;            // open ground behind it
  }
  return false;                                     // a mass, not a wall
}

/** How much of its drawn height this square keeps. 1 for everything that is
 *  not being cut, which is every square of every painted board. */
export function cutawayHeightScale(scene: VttScene, x: number, z: number,
                                   yawDeg: number = YAW_DEG,
                                   fullFt = 0, level = 0): number {
  if (!cutAwayAt(scene, x, z, yawDeg, level)) return 1;
  if (fullFt <= 0) return CUTAWAY_SCALE;
  return Math.max(CUTAWAY_SCALE, Math.min(1, CUTAWAY_MIN_FT / fullFt));
}

/** Which square is really under this pixel, given that the board has HEIGHT.
 *
 *  Unprojecting onto a plane answers "which square would be here if the board
 *  were flat", and the board has not been flat since elevation went in. On a
 *  dais the square you click is not the square you get: the ground-plane answer
 *  is the square that would be there at floor level, and what you are actually
 *  looking at is a square five feet up and therefore drawn higher on screen.
 *  A player reported it exactly that way — "I need to click on the 2d mesh
 *  location" — and it is worse than it sounds, because the error grows with
 *  the height and the whole point of high ground is that people stand on it.
 *
 *  The fix is the march `occludedAt` already does, run the other way. Every
 *  point of the form `(gx + rayX*u, u*rayRise, gz + rayZ*u)` projects to the
 *  same pixel — that is what an orthographic camera means — so walking `u` down
 *  from above and asking each square how tall it is DRAWN finds the first
 *  surface the ray meets. Drawn, not "solid": you pick what you can see, so a
 *  click on what looks like the top of a wall selects that wall, and a wall the
 *  cutaway took down no longer swallows clicks meant for the floor behind it.
 *
 *  Falls back to the ground-plane square when the ray meets nothing at all —
 *  over a chasm, or off the edge of the built area — which is exactly the
 *  answer this replaced, so nothing that worked before can get worse.
 *
 *  `gx`/`gz` are the ground-plane hit in squares. The march is bounded by the
 *  board's own extremes in feet: `tallestFt` above the storey's floor and
 *  `deepestFt` below it. BOTH, because a sunken square is the same bug the
 *  other way round — a reef channel is ten feet down, so the ray reaches it
 *  BEYOND the ground-plane point rather than short of it, at a negative `u`,
 *  and a march that stopped at zero fell back to the plane every time. */
export function squareUnderRay(scene: VttScene, gx: number, gz: number,
                               yawDeg: number, tallestFt: number,
                               deepestFt = 0, level = 0): [number, number] {
  const { rayX, rayZ, rayRise } = basis(yawDeg);
  const sqFt = scene.square_ft || 5;
  const ground: [number, number] = [Math.floor(gx), Math.floor(gz)];
  const climbPerSquare = rayRise * sqFt;             // feet gained per square
  if (climbPerSquare <= 0) return ground;
  const step = 0.25;
  // A step PAST the deepest floor, not exactly to it. The march samples
  // discretely, and a surface sitting exactly on the bound is only ever
  // approached and never crossed — which is how a channel sunk exactly ten
  // feet was missed by a march bounded at exactly ten feet.
  const floorU = Math.min(0, deepestFt) / climbPerSquare - 0.25;
  for (let u = Math.max(0, tallestFt) / climbPerSquare; u >= floorU; u -= step) {
    const x = Math.floor(gx + rayX * u);
    const z = Math.floor(gz + rayZ * u);
    if (x < 0 || z < 0 || x >= scene.width || z >= scene.height) continue;
    if (drawnTopFt(scene, x, z, yawDeg, level) >= u * climbPerSquare) return [x, z];
  }
  return ground;
}

/** How high the ray may climb before nothing on any board could still be in
 *  the way, in feet. The tallest landmark in the catalogue is a 60-ft giant,
 *  and past that the march is walking the board for no reason. */
const MAX_OCCLUDER_FT = 64;

/** Is something opaque standing between this creature and the camera?
 *
 *  Pure grid arithmetic, and deliberately: the board is drawn FROM tile codes,
 *  so "is that wall in front of me" is a fact about the grid, exactly as cover
 *  and sight are. It is also the only way to answer it for the painted board,
 *  where the geometry draws no colour at all and there is no picture to read —
 *  and it costs no depth-buffer readback, which would stall the GPU every frame
 *  on a webview that can barely afford the draw calls it already makes.
 *
 *  The camera never MOVES, so for any one angle the ray from a creature back to
 *  the lens is one fixed direction (`basis(yaw)`): march it over the squares it
 *  crosses and ask each how tall it is drawn. Turning the camera picks a
 *  different direction and the march is the same march — which is the whole
 *  reason rotation was affordable here at all. The climb, `rayRise`, is
 *  tan(pitch) and does not depend on yaw.
 *
 *  Two decisions worth keeping:
 *
 *  * The point tested is the creature's CHEST, not its feet. A wall that hides
 *    the boots hides nothing worth marking, and at this pitch a ten-foot wall
 *    one square in front leaves exactly the head showing — which is precisely
 *    the case a silhouette is for.
 *  * The ray crosses whichever squares the current angle puts between the
 *    creature and the lens — at the canonical 45 degrees those are the diagonal
 *    ones. A pillar beside that line covers half the figure, and half a figure
 *    is still a figure you can find, so it is not called occluded.
 *
 *  A square is treated as a full column even where the thing on it is drawn
 *  narrow — a pillar is a third of its square wide. That is deliberate and
 *  cheap: the ray gains only about four feet crossing a square, so the answer
 *  differs from the exact silhouette over a band a few inches tall, and
 *  rebuilding every skin's shape here to close it would be a second copy of the
 *  geometry, which is the one thing the generated shape table exists to
 *  prevent. */
export function occludedAt(scene: VttScene, x: number, z: number,
                           squares: number, footFt: number,
                           yawDeg: number = YAW_DEG, level = 0): boolean {
  const { rayX, rayZ, rayRise } = basis(yawDeg);
  const sqFt = scene.square_ft || 5;
  // A token's DOM box is as tall in pixels as its footprint is wide, so the
  // figure it draws stands this tall in the world. See VERTICAL_SQUEEZE.
  const chestFt = footFt + (squares * sqFt) / VERTICAL_SQUEEZE / 2;
  // From the middle of the creature's footprint, in squares.
  const fromX = x + squares / 2;
  const fromZ = z + squares / 2;
  const step = 0.5;
  for (let run = step; run * sqFt * rayRise <= MAX_OCCLUDER_FT; run += step) {
    const sx = Math.floor(fromX + rayX * run);
    const sz = Math.floor(fromZ + rayZ * run);
    // Off the board in ANY direction. It used to test only the far edges,
    // which was right when the ray could only ever run one way; turned round,
    // the same ray leaves by the near ones.
    if (sx < 0 || sz < 0 || sx >= scene.width || sz >= scene.height) return false;
    // Its own square is not in its own way, whatever is drawn there.
    if (sx >= x && sz >= z && sx < x + squares && sz < z + squares) continue;
    if (drawnTopFt(scene, sx, sz, yawDeg, level) > chestFt + run * sqFt * rayRise)
      return true;
  }
  return false;
}

/** Everything a renderer needs to draw one frame. */
export interface PaintState {
  scene: VttScene;
  view: View;
  art?: HTMLImageElement | null;
  /** "x,y" -> feet spent, for the selected token's movement wash. */
  reach?: Map<string, number> | null;
  /** Squares of the previewed path, in order. */
  path?: [number, number][] | null;
  pathCost?: number;
  pathLegal?: boolean;
  /** The route leaves an enemy's reach — drawn as a warning. */
  pathProvokes?: boolean;
  /** Squares where LEAVING provokes. Drawn under the movement wash so a player
   *  can see the threatened ground BEFORE choosing, the way they see a wall —
   *  the opportunity warning on the path only arrives once the pointer has
   *  already settled on the far end of the route. */
  threatened?: Set<string> | null;
  /** The squares an armed spell's template would cover, and whether it is a
   *  legal place to put it. Drawn on the cursor while aiming. */
  area?: [number, number][] | null;
  areaLegal?: boolean;
  hover?: [number, number] | null;
  /** Measurement in progress: [from, to]. */
  measure?: [[number, number], [number, number]] | null;
  show: { grid: boolean; terrain: boolean; effects: boolean; fog: boolean };
  /** Transient ping markers with their spawn time. */
  pings?: { x: number; y: number; label?: string; at: number }[];
  /** Connectors leaving the floor being drawn. A player can't decide to climb
   *  a stair they can't see, so these are marked on the board itself. */
  stairs?: { x: number; y: number; to: number; tx: number; ty: number; kind?: string }[];
  /** Every floor, so a connector can name where it goes. */
  levels?: { name: string; base_ft: number }[];
  /** Which floor is being drawn — decides whether a connector reads up or down. */
  level?: number;
  /** The creature currently mid-walk and where it has got to, so its base on
   *  the floor travels with it instead of waiting at the destination. */
  walking?: { tokenId: number; x: number; y: number } | null;
  /** Matted sprites by stored image id (see lib/boardSprites). */
  sprites?: ReadonlyMap<number, HTMLImageElement>;
  now?: number;
}

/** Where a token's DOM element goes, in CSS pixels.
 *
 *  Tokens are DOM rather than painted (see `VttOverlay`), which is what makes
 *  them camera-facing for free on the isometric board — an element over a
 *  canvas always faces the viewer. So a renderer's whole obligation to the
 *  token layer is this: say where the square landed on screen, how big it came
 *  out, how far away it is, and whether anything is standing in front of it. */
export interface TokenPlacement {
  left: number;
  top: number;
  /** Side length of the footprint box. */
  size: number;
  /** Distance from the camera, for stacking order. Flat boards report 0. */
  depth: number;
  /** Something opaque is between the camera and this square.
   *
   *  Drawn as a silhouette rather than hidden: a token that simply vanished
   *  would be indistinguishable from a bug, which is the same reason
   *  `targets_for` returns illegal targets wearing their reason instead of
   *  dropping them. Flat boards can never occlude, and report false. */
  occluded: boolean;
}

/** The contract `VttOverlay` holds. One instance per mounted board.
 *
 *  Camera state is NOT owned here — it lives in React as a `View` and is passed
 *  in, so the component keeps driving pan and zoom exactly as it always has and
 *  a renderer swap stays a renderer swap. */
export interface BoardView {
  /** Can this renderer be turned? The isometric board can — its geometry is
   *  real and only the lens moves. The flat canvas cannot and never will:
   *  looking straight down, there is nothing a rotation would reveal. */
  readonly canTurn: boolean;

  /** Fit the whole board into a viewport, with breathing room. `yaw` is which
   *  way the camera is turned; a renderer that cannot turn ignores it. */
  fit(scene: VttScene, w: number, h: number, yaw?: number): View;

  /** Which square is under this pixel. Null when the pointer is off the board
   *  entirely — which a flat board can't tell you, and reports as never. */
  squareAt(view: View, scene: VttScene, px: number, py: number,
           level: number): [number, number] | null;

  /** The CONTINUOUS point on this storey's floor under a pixel, in squares.
   *
   *  Not `squareAt`: that one marches the view ray and answers "which square am
   *  I looking at", which is what a click wants and is deliberately affected by
   *  height. This is the flat, exact, invertible answer, and it is what turning
   *  the camera has to pivot about — a whole turn must come back to precisely
   *  where it started, and re-deriving a SQUARE each time drifts, because on a
   *  board with any height the square under the middle of the frame legitimately
   *  changes as you turn. */
  groundAt(view: View, scene: VttScene, px: number, py: number,
           level: number): [number, number];

  /** Where the token standing at this square belongs on screen. */
  screenOf(view: View, scene: VttScene, x: number, y: number, squares: number,
           level: number, elevationFt: number): TokenPlacement;

  /** Zoom about a screen point, so the square under it stays put. */
  zoomAt(view: View, px: number, py: number, factor: number): View;

  /** Slide the view by a drag, in screen pixels.
   *
   *  Behind the interface because a `View` is about to stop being an affine
   *  pan-and-zoom over a fixed projection: the flat board translates its
   *  image, an orbit camera moves its TARGET across the ground, and those are
   *  the same gesture and different arithmetic. The shell used to reach in and
   *  add to `ox`/`oy` itself, which made every renderer's camera the shell's
   *  business. */
  panBy(view: View, dxPx: number, dyPx: number,
        scene: VttScene, level: number): View;

  /** Turn the camera to an absolute yaw, keeping the same point of GROUND
   *  under the middle of the viewport.
   *
   *  The pivot is the continuous ground point and never a square: a square is
   *  what you are looking AT, which legitimately changes as the camera comes
   *  round on a board with any height, and pivoting about a moving target
   *  means a whole turn does not come back where it started.
   *
   *  A renderer that cannot turn returns the view unchanged. */
  turnTo(view: View, yawDeg: number, w: number, h: number,
         scene: VttScene, level: number): View;

  /** Where the painted layer belongs on screen, in CSS pixels, or null if this
   *  renderer has no painted layer.
   *
   *  The painting is baked at a CANONICAL framing — the board's projected
   *  bounding box plus a fixed margin — so laying it back over exactly that
   *  rectangle is the only placement that keeps it on the geometry, and the
   *  same View affine carries it through every pan and zoom. It is stored with
   *  the surround already cut away, so the corners need no clipping here. */
  /** Draw one frame. `w`/`h` are CSS pixels; device-pixel-ratio is the
   *  renderer's own business. */
  draw(st: PaintState, w: number, h: number): void;

  /** Release anything the browser won't collect on its own (GL contexts,
   *  geometry, textures). Called when the board unmounts. */
  dispose(): void;
}

/** Walk back down a cost map from a destination to build the route the server
 *  would take. Lets the overlay draw a live path on hover with no round trip —
 *  the authoritative path still comes from the server when the move is made.
 *
 *  Pure grid arithmetic with no notion of how the board is drawn, so it belongs
 *  here rather than in either renderer. */
export function pathFromCosts(
  reach: Map<string, number>,
  from: [number, number],
  to: [number, number],
): [number, number][] | null {
  const key = (x: number, y: number) => `${x},${y}`;
  if (!reach.has(key(to[0], to[1]))) return null;
  const path: [number, number][] = [to];
  let cur = to;
  let guard = 0;
  while ((cur[0] !== from[0] || cur[1] !== from[1]) && guard++ < 400) {
    let best: [number, number] | null = null;
    let bestCost = reach.get(key(cur[0], cur[1])) ?? Infinity;
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        if (!dx && !dy) continue;
        const nx = cur[0] + dx;
        const ny = cur[1] + dy;
        const c = reach.get(key(nx, ny));
        if (c === undefined) continue;
        if (c < bestCost) {
          bestCost = c;
          best = [nx, ny];
        }
      }
    }
    if (!best) break;
    path.push(best);
    cur = best;
  }
  path.reverse();
  return path;
}
