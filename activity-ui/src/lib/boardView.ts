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

/** How tall each tile stands, in feet. 0 is floor you walk on.
 *
 *  Mirrors vtt/terrain.py, and takes its numbers from two different places
 *  there. Anything with a `cover_height_ft` uses that value, because the rules
 *  already decided how tall a crate is when they decided you can lie down
 *  behind one. Everything else is full-height structure, where the exact figure
 *  is the picture's business and not the rules': cover from a pillar does not
 *  depend on how tall the pillar is, only on it being taller than you.
 *
 *  Flat boards ignore this entirely. It exists because an isometric board has
 *  to put something in the third dimension, and guessing per-render would let
 *  two frames disagree about the same room. */
export const TILE_HEIGHT_FT: Record<string, number> = {
  "#": 10,   // wall
  R: 10,     // rock face
  T: 12,     // tree — the canopy, not the trunk
  O: 10,     // pillar
  "+": 8,    // closed door, filling its opening
  p: 8,      // portcullis
  o: 4,      // crate        \
  A: 4,      // altar         | these four carry a cover_height_ft in the
  n: 3,      // furniture     | rules, and this is that number
  w: 3,      // low wall     /
};

export function tileHeightFt(code: string): number {
  return TILE_HEIGHT_FT[code] ?? 0;
}

/** Codes that are the BUILDING rather than something standing in it.
 *
 *  The same split vtt/terrain.py makes with `OBJECT_SPRITES`: everything in
 *  that table is a discrete thing which can be attacked and broken, so it has
 *  to be able to change and is drawn per-square; what is left is structure,
 *  which the painted layer is conditioned on and never has to change. */
export const STRUCTURE_CODES: ReadonlySet<string> = new Set(["#", "R"]);

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
