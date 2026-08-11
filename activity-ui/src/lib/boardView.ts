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
  COVER_HEIGHT_FT, DECOR_KINDS, HEIGHT_JITTER, HOLE_CODES,
  MAX_DECOR_HEIGHT_FT, OBJECT_VARIANTS, PILLAR_RADIUS, SKINS, SKIRT_FT,
  SKIRT_INSET, STRUCTURE_CODES,
  TILE_HEIGHT_FT, WALL_THICKNESS, isSolid,
} from "./boardShapes.generated";
export type {
  BoxPart, Part, PolyPart, SkinShape, SolidPart,
} from "./boardShapes.generated";
import {
  COVER_HEIGHT_FT as _COVER, HEIGHT_JITTER as _JITTER, SKINS as _SKINS,
  TILE_HEIGHT_FT as _HEIGHT, WALL_THICKNESS as _THICK, isSolid as _isSolid,
} from "./boardShapes.generated";
import type { Part } from "./boardShapes.generated";

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
export interface View { scale: number; ox: number; oy: number }

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
  /** Fit the whole board into a viewport, with breathing room. */
  fit(scene: VttScene, w: number, h: number): View;

  /** Which square is under this pixel. Null when the pointer is off the board
   *  entirely — which a flat board can't tell you, and reports as never. */
  squareAt(view: View, scene: VttScene, px: number, py: number,
           level: number): [number, number] | null;

  /** Where the token standing at this square belongs on screen. */
  screenOf(view: View, scene: VttScene, x: number, y: number, squares: number,
           level: number, elevationFt: number): TokenPlacement;

  /** Zoom about a screen point, so the square under it stays put. */
  zoomAt(view: View, px: number, py: number, factor: number): View;

  /** Where the painted layer belongs on screen, in CSS pixels, or null if this
   *  renderer has no painted layer.
   *
   *  The painting is baked at a CANONICAL framing — the board's projected
   *  bounding box plus a fixed margin — so laying it back over exactly that
   *  rectangle is the only placement that keeps it on the geometry, and the
   *  same View affine carries it through every pan and zoom. It is stored with
   *  the surround already cut away, so the corners need no clipping here. */
  backdropRect(view: View, scene: VttScene):
    { left: number; top: number; width: number; height: number } | null;

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
