/** GENERATED FILE — do not edit.
 *
 *  Written by scripts/gen_board_shapes.py from the Python side, which is
 *  authoritative. `scripts/iso_alignment_check.py` fails if this file is stale.
 *
 *  These are the shapes the isometric board is built from, and they matter on
 *  both sides of the wire: vtt/isocam.py rasterizes them into the depth map the
 *  painted layer is conditioned on, and vttScene3d.ts builds them as the
 *  geometry the player looks at. If the two disagree, the painting lands on
 *  furniture that is not there — and nothing in either program looks wrong.
 */


/** What things on the board are SHAPED like.
 *
 *  Two forms, told apart by whether the first element is a number — the same
 *  discriminator the Python side uses, so no tag has to be kept in step.
 *
 *  A **box** is `[x0, x1, z0, z1, yFrom, yTo]`: fractions of the square for
 *  x/z, of the thing's standing height for y. Axis-aligned and cheap.
 *
 *  A **solid** is `[bottomPolygon, topPolygon, yFrom, yTo]` — a prismatoid,
 *  two polygons of equal vertex count joined by a quad per edge. It subsumes
 *  the box, which is why gaining it rewrote nothing, and it is what stops
 *  everything reading as a cube: a narrower top is a TAPER (a tent's canvas
 *  drawn in to a ridge, a hull with tumblehome, a hipped roof), an offset top
 *  is a LEAN (the raked legs of a timber watchtower, a ladder), and more than
 *  four vertices is a CUT CORNER, which is what turns a stair-stepped hull
 *  outline into one continuous diagonal. */
export type BoxPart = readonly [number, number, number, number, number, number];
export type PolyPart = readonly (readonly [number, number])[];
export type SolidPart = readonly [PolyPart, PolyPart, number, number];
export type Part = BoxPart | SolidPart;

export function isSolid(part: Part): part is SolidPart {
  return typeof part[0] !== "number";
}

/** How thick a wall is DRAWN, as a fraction of its square.
 *  The square stays fully solid in the RULES; this only stops the
 *  wall's top face swallowing the room at this camera angle. */
export const WALL_THICKNESS = 0.34;

/** Radius of a pillar or tree trunk, as a fraction of its square. */
export const PILLAR_RADIUS = 0.32;

/** How much a tile's DRAWN height may wander, as a fraction.
 *  Never applied where the RULES quote a height — see heightScale. */
export const HEIGHT_JITTER = 0.12;

/** How tall each tile SCREENS a creature, in feet, per the rules.
 *  Non-zero means the height is an ANSWER a player reads off the
 *  board, so it must be drawn exactly and never jittered. */
export const COVER_HEIGHT_FT: Record<string, number> = {
  A: 4,
  n: 3,
  o: 4,
  w: 3,
};

/** How thick a platform is, in feet, where its floor meets a hole.
 *  Without it an island is a paper cut-out hanging in nothing. */
export const SKIRT_FT = 8;

/** How far the BOTTOM of a skirt pulls in from its own edge.
 *  A vertical drop is a slab and a ring of slabs is a box; pulled
 *  in, each side is a trapezoid instead. Perpendicular to the
 *  edge, never toward the square's centre, so two squares along
 *  one straight run stay coplanar. */
export const SKIRT_INSET = 0;

/** How deep a corner is cut where a floor's outline turns away
 *  on both sides. A vessel's deck is carved out of squares, so
 *  its outline is a STAIRCASE; at 1.0 each step is drawn as the
 *  diagonal it means and the whole bow reads as one line. */
export const CORNER_CHAMFER = 1;

/** Codes that are a HOLE, not ground — nothing is drawn on them.
 *  Open sky is air and a chasm is the absence of floor; drawing
 *  either as a surface invents ground the rules say you fall
 *  through. */
export const HOLE_CODES: ReadonlySet<string> = new Set([" ", "^", "x"]);

/** Codes that are the BUILDING rather than something standing in it. */
export const STRUCTURE_CODES: ReadonlySet<string> = new Set(["#", "R"]);

/** How tall each tile stands, in feet. 0 is floor you walk on. */
export const TILE_HEIGHT_FT: Record<string, number> = {
  "#": 10,
  "+": 8,
  A: 4,
  O: 10,
  R: 10,
  T: 12,
  n: 3,
  o: 4,
  p: 8,
  w: 3,
};

/** What each object is SHAPED like: one or more ARRANGEMENTS, each a
 *  list of parts (see Part). Which one a square uses comes from
 *  variantOf, so the same square always picks the same arrangement
 *  on both sides of the wire. */
export const OBJECT_VARIANTS: Record<string, readonly (readonly Part[])[]> = {
  A: [
      [[0.1, 0.9, 0.3, 0.7, 0, 0.72], [0.06, 0.94, 0.26, 0.74, 0.72, 1]],
      [[0.14, 0.86, 0.28, 0.72, 0, 0.62], [0.1, 0.62, 0.24, 0.76, 0.62, 0.94], [0.66, 0.96, 0.3, 0.7, 0.62, 0.82]]],
  n: [
      [[0.12, 0.22, 0.16, 0.26, 0, 0.72], [0.78, 0.88, 0.16, 0.26, 0, 0.72], [0.12, 0.22, 0.74, 0.84, 0, 0.72], [0.78, 0.88, 0.74, 0.84, 0, 0.72], [0.06, 0.94, 0.1, 0.9, 0.72, 1]],
      [[0.1, 0.24, 0.12, 0.88, 0, 1], [0.24, 0.34, 0.2, 0.3, 0.62, 0.78], [0.24, 0.34, 0.7, 0.8, 0.62, 0.78]]],
  o: [
      [[0.08, 0.58, 0.1, 0.62, 0, 0.62], [0.46, 0.92, 0.36, 0.9, 0, 0.48], [0.16, 0.56, 0.18, 0.58, 0.62, 1]],
      [[0.12, 0.66, 0.14, 0.7, 0, 1], [0.62, 0.94, 0.52, 0.92, 0, 0.54]],
      [[0.1, 0.52, 0.2, 0.64, 0, 0.7], [0.54, 0.9, 0.14, 0.56, 0, 0.86], [0.34, 0.74, 0.6, 0.94, 0, 0.44]]],
  w: [
      [[0.18, 0.82, 0, 1, 0, 0.8], [0.1, 0.9, 0, 1, 0.8, 1]]],
};

/** Scenery: drawn by every view, honoured by none of the rules.
 *  kind -> [heightFt, parts]. Capped below the lowest cover height
 *  in the tile table, so nothing decorative can ever be mistaken for
 *  something to crouch behind — see vtt/decor.py. */
export const MAX_DECOR_HEIGHT_FT = 2;
export const DECOR_KINDS: Record<string, readonly [number, readonly Part[]]> = {
  bones: [0.5, [[0.3, 0.7, 0.42, 0.52, 0, 0.5], [0.38, 0.48, 0.3, 0.7, 0, 0.4], [0.55, 0.72, 0.55, 0.68, 0, 0.7]]],
  brazier: [1.9, [[0.42, 0.58, 0.42, 0.58, 0, 0.62], [0.32, 0.68, 0.32, 0.68, 0.62, 1]]],
  roots: [0.6, [[0.05, 0.95, 0.4, 0.52, 0, 0.5], [0.36, 0.48, 0.05, 0.95, 0, 0.7]]],
  rug: [0.08, [[0.1, 0.9, 0.18, 0.82, 0, 1]]],
  sack: [1.6, [[0.3, 0.66, 0.32, 0.68, 0, 0.78], [0.34, 0.6, 0.36, 0.62, 0.78, 1]]],
  shards: [0.4, [[0.32, 0.52, 0.36, 0.56, 0, 1], [0.54, 0.68, 0.52, 0.66, 0, 0.6]]],
};

/** What a square is MADE OF, as opposed to what it DOES.
 *
 *  A tile code answers the rules — cover, movement, sight. A skin
 *  answers the eye, and nothing else: no rule reads one. It may hand
 *  over its own silhouette (which wins over everything, including the
 *  wall-face model, so a mountainside is drawn as rock mass rather
 *  than masonry panels) and its own drawn height — but never on a
 *  tile whose height the rules quote. See vtt/skins.py. */
export interface SkinShape {
  readonly substance: string;
  /** Feet. 0 means keep the tile's own standing height. */
  readonly heightFt: number;
  /** Line up along the run instead of taking a quarter-turn. */
  readonly directional: boolean;
  /** Turn so the part's authored +z side faces whichever way this
   *  square is NOT enclosed. Beats `directional` where both are set.
   *  What a tent needed: a wall that does not know which side the
   *  weather is on can only lean the same amount both ways. */
  readonly outward: boolean;
  /** Pick the arrangement from a COARSE hash, so neighbours agree.
   *  For a MASS (rock, coral) rather than a set of objects. */
  readonly smooth: boolean;
  /** Feet. Non-zero means this FLOOR carries its own side wherever
   *  it meets something that is not the same BODY — how a ship gets
   *  a hull, since deep water is not a hole. 0 = the board rule. */
  readonly skirtFt: number;
  /** How far that side's bottom pulls in, as a fraction. */
  readonly skirtInset: number;
  /** Squares of one non-empty body are one THING, and no side is
   *  drawn between them: a deck, the rail round it, the mast through
   *  it and the cabin on it are four skins and one hull. */
  readonly body: string;
  /** Draw at exactly this height, with no per-instance jitter.
   *  Anything BUILT says so: a jittered post is a platform standing
   *  clear of its own legs. */
  readonly exact: boolean;
  readonly variants: readonly (readonly Part[])[] | null;
}
export const SKINS: Record<string, SkinShape> = {
  boulder: { substance: "granite", heightFt: 8, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[[[0.941248, 0.317229], [0.659771, 0.114279], [0.307702, 0.035752], [0.091279, 0.330702], [0.091279, 0.669298], [0.307702, 0.964248], [0.659771, 0.885721], [0.941248, 0.682771]], [[0.749401, 0.396695], [0.590305, 0.281984], [0.39131, 0.237599], [0.268984, 0.40431], [0.268984, 0.59569], [0.39131, 0.762401], [0.590305, 0.718016], [0.749401, 0.603305]], 0, 0.86], [[[0.760209, 0.380502], [0.619498, 0.239791], [0.420502, 0.239791], [0.279791, 0.380502], [0.279791, 0.579498], [0.420502, 0.720209], [0.619498, 0.720209], [0.760209, 0.579498]], [[0.566194, 0.460866], [0.539134, 0.433806], [0.500866, 0.433806], [0.473806, 0.460866], [0.473806, 0.499134], [0.500866, 0.526194], [0.539134, 0.526194], [0.566194, 0.499134]], 0.86, 1]],
      [[[[0.847839, 0.379352], [0.647048, 0.088425], [0.310287, 0.178561], [0.034825, 0.363887], [0.034825, 0.716113], [0.310287, 0.901439], [0.647048, 0.991575], [0.847839, 0.700648]], [[0.724436, 0.430467], [0.587533, 0.232108], [0.357923, 0.293564], [0.170108, 0.419923], [0.170108, 0.660077], [0.357923, 0.786436], [0.587533, 0.847892], [0.724436, 0.649533]], 0, 0.62], [[[0.621731, 0.348156], [0.491844, 0.218269], [0.308156, 0.218269], [0.178269, 0.348156], [0.178269, 0.531844], [0.308156, 0.661731], [0.491844, 0.661731], [0.621731, 0.531844]], [[0.492388, 0.401732], [0.438268, 0.347612], [0.361732, 0.347612], [0.307612, 0.401732], [0.307612, 0.478268], [0.361732, 0.532388], [0.438268, 0.532388], [0.492388, 0.478268]], 0.62, 0.94]],
      [[[[0.970615, 0.305065], [0.656535, 0.122091], [0.289159, -0.009015], [0.083691, 0.327559], [0.083691, 0.672441], [0.289159, 1.009015], [0.656535, 0.877909], [0.970615, 0.694935]], [[0.833352, 0.361921], [0.610879, 0.232314], [0.350654, 0.139448], [0.205114, 0.377854], [0.205114, 0.622146], [0.350654, 0.860552], [0.610879, 0.767686], [0.833352, 0.638079]], 0, 0.44], [[[0.837164, 0.305195], [0.674805, 0.142836], [0.445195, 0.142836], [0.282836, 0.305195], [0.282836, 0.534805], [0.445195, 0.697164], [0.674805, 0.697164], [0.837164, 0.534805]], [[0.707821, 0.358771], [0.621229, 0.272179], [0.498771, 0.272179], [0.412179, 0.358771], [0.412179, 0.481229], [0.498771, 0.567821], [0.621229, 0.567821], [0.707821, 0.481229]], 0.44, 0.8], [[[0.506298, 0.551117], [0.408883, 0.453702], [0.271117, 0.453702], [0.173702, 0.551117], [0.173702, 0.688883], [0.271117, 0.786298], [0.408883, 0.786298], [0.506298, 0.688883]], [[0.395433, 0.597039], [0.362961, 0.564567], [0.317039, 0.564567], [0.284567, 0.597039], [0.284567, 0.642961], [0.317039, 0.675433], [0.362961, 0.675433], [0.395433, 0.642961]], 0.44, 0.66]],
      [[[[0.880866, 0.34224], [0.64176, 0.157762], [0.335613, 0.103134], [0.141762, 0.351613], [0.141762, 0.648387], [0.335613, 0.896866], [0.64176, 0.842238], [0.880866, 0.65776]], [[0.633303, 0.444784], [0.549616, 0.380217], [0.442465, 0.361097], [0.374617, 0.448065], [0.374617, 0.551935], [0.442465, 0.638903], [0.549616, 0.619783], [0.633303, 0.555216]], 0, 1]]] },
  canvas: { substance: "canvas", heightFt: 8, directional: false, outward: true,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "tent", exact: true,
    variants: [
      [[[[0, 1], [1, 1], [1, 0], [0, 0]], [[0, 0.2], [1, 0.2], [1, 0], [0, 0]], 0, 0.72], [0, 1, 0.02, 0.2, 0.7, 0.8], [[[0.2, 1.28], [0.28, 1.28], [0.28, 1.2], [0.2, 1.2]], [[0.22, 0.18], [0.28, 0.18], [0.28, 0.1], [0.22, 0.1]], 0, 0.68], [[[0.72, 1.28], [0.8, 1.28], [0.8, 1.2], [0.72, 1.2]], [[0.72, 0.18], [0.78, 0.18], [0.78, 0.1], [0.72, 0.1]], 0, 0.68]]] },
  "cave-rock": { substance: "limestone", heightFt: 13, directional: false, outward: false,
    smooth: true,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[-0.1, 1.1, -0.1, 1.1, 0, 1]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.74], [0.14, 0.82, 0.2, 0.88, 0.74, 0.9]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.62], [0.28, 1, 0, 0.72, 0.62, 0.84]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.88], [0.1, 0.72, 0.24, 0.9, 0.88, 1]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.54]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.7], [0, 0.64, 0.3, 1, 0.7, 0.94]]] },
  chitin: { substance: "chitin", heightFt: 8, directional: true, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "ship", exact: false,
    variants: [
      [[0, 1, 0.34, 0.66, 0, 0.58], [0.06, 0.94, 0.28, 0.72, 0.58, 0.86], [0.22, 0.78, 0.36, 0.64, 0.86, 1]],
      [[0, 1, 0.3, 0.7, 0, 0.7], [0.14, 0.86, 0.36, 0.64, 0.7, 1]]] },
  "chitin-deck": { substance: "chitin", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 13, skirtInset: 0.1, body: "ship", exact: true,
    variants: null },
  "chitin-rail": { substance: "chitin", heightFt: 0, directional: true, outward: false,
    smooth: false,     skirtFt: 13, skirtInset: 0.1, body: "ship", exact: false,
    variants: [
      [[0.08, 0.2, 0.42, 0.58, 0, 1], [0.44, 0.56, 0.42, 0.58, 0, 1], [0.8, 0.92, 0.42, 0.58, 0, 1], [0, 1, 0.44, 0.56, 0.8, 1], [0, 1, 0.45, 0.55, 0.34, 0.46]]] },
  cliff: { substance: "granite", heightFt: 14, directional: false, outward: false,
    smooth: true,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[-0.1, 1.1, -0.1, 1.1, 0, 1]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.74], [0.14, 0.82, 0.2, 0.88, 0.74, 0.9]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.62], [0.28, 1, 0, 0.72, 0.62, 0.84]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.88], [0.1, 0.72, 0.24, 0.9, 0.88, 1]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.54]],
      [[-0.1, 1.1, -0.1, 1.1, 0, 0.7], [0, 0.64, 0.3, 1, 0.7, 0.94]]] },
  coral: { substance: "coral", heightFt: 9, directional: false, outward: false,
    smooth: true,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[0.2, 0.62, 0.24, 0.66, 0, 0.72], [0.5, 0.86, 0.44, 0.8, 0, 0.5], [0.3, 0.54, 0.34, 0.58, 0.72, 1]],
      [[0.16, 0.56, 0.3, 0.72, 0, 0.6], [0.46, 0.9, 0.18, 0.6, 0, 0.86], [0.34, 0.62, 0.5, 0.8, 0.6, 0.8]],
      [[0.24, 0.78, 0.22, 0.76, 0, 0.42], [0.36, 0.6, 0.34, 0.58, 0.42, 1], [0.6, 0.84, 0.5, 0.74, 0.42, 0.7]],
      [[0.1, 0.5, 0.2, 0.58, 0, 0.8], [0.44, 0.72, 0.46, 0.82, 0, 0.56], [0.2, 0.42, 0.3, 0.5, 0.8, 1]]] },
  "doorway-stone": { substance: "dressed-stone", heightFt: 16, directional: true, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[0, 0.15, 0.28, 0.72, 0, 1], [0.85, 1, 0.28, 0.72, 0, 1], [0, 1, 0.28, 0.72, 0.58, 1]]] },
  "doorway-timber": { substance: "log-palisade", heightFt: 14, directional: true, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[0, 0.15, 0.28, 0.72, 0, 1], [0.85, 1, 0.28, 0.72, 0, 1], [0, 1, 0.28, 0.72, 0.58, 1]]] },
  "drowned-column": { substance: "drowned-stone", heightFt: 9, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[0.3, 0.7, 0.3, 0.7, 0, 0.44], [0.32, 0.68, 0.32, 0.68, 0.44, 0.52]],
      [[0.3, 0.7, 0.3, 0.7, 0, 0.7], [0.34, 0.64, 0.3, 0.6, 0.7, 0.78]],
      [[0.32, 0.68, 0.32, 0.68, 0, 0.28], [0.1, 0.86, 0.22, 0.48, 0, 0.14]],
      [[0.28, 0.66, 0.34, 0.72, 0, 0.58], [0.3, 0.62, 0.36, 0.68, 0.58, 0.66], [0.6, 0.92, 0.3, 0.54, 0, 0.16]]] },
  "drowned-wall": { substance: "drowned-stone", heightFt: 0, directional: true, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[0, 1, 0.18, 0.78, 0, 0.72], [0.06, 0.62, 0.22, 0.7, 0.72, 1]],
      [[0, 1, 0.22, 0.8, 0, 0.58], [0.34, 0.94, 0.26, 0.74, 0.58, 1]]] },
  flap: { substance: "canvas", heightFt: 8, directional: false, outward: true,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "tent", exact: true,
    variants: [
      [[[[0, 1], [0.17, 1], [0.17, 0], [0, 0]], [[0, 0.3], [0.13, 0.3], [0.13, 0], [0, 0]], 0, 0.7], [[[0.83, 1], [1, 1], [1, 0], [0.83, 0]], [[0.87, 0.3], [1, 0.3], [1, 0], [0.87, 0]], 0, 0.7], [0, 1, 0, 0.24, 0.56, 0.78]]] },
  hull: { substance: "tarred-planking", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "ship", exact: true,
    variants: null },
  masonry: { substance: "dressed-stone", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: null },
  mast: { substance: "spar-timber", heightFt: 26, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "ship", exact: true,
    variants: [
      [[0.43, 0.57, 0.43, 0.57, 0, 1], [0.06, 0.94, 0.46, 0.54, 0.6, 0.655], [0.2, 0.8, 0.47, 0.53, 0.84, 0.875], [0.34, 0.66, 0.34, 0.66, 0, 0.06]]] },
  palisade: { substance: "log-palisade", heightFt: 10, directional: true, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[0, 0.24, 0.34, 0.66, 0, 0.9], [0.04, 0.2, 0.4, 0.6, 0.9, 1], [0.26, 0.5, 0.34, 0.66, 0, 0.96], [0.3, 0.46, 0.4, 0.6, 0.96, 1], [0.52, 0.76, 0.34, 0.66, 0, 0.88], [0.56, 0.72, 0.4, 0.6, 0.88, 1], [0.78, 1, 0.34, 0.66, 0, 0.94]]] },
  parapet: { substance: "dressed-stone", heightFt: 9, directional: true, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[0, 1, 0.3, 0.7, 0, 0.62], [0, 0.3, 0.26, 0.74, 0.62, 1], [0.42, 0.72, 0.26, 0.74, 0.62, 1]]] },
  "plated-deck": { substance: "riveted-brass", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 13, skirtInset: 0.1, body: "ship", exact: true,
    variants: null },
  plating: { substance: "riveted-brass", heightFt: 8, directional: true, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "ship", exact: false,
    variants: [
      [[0, 1, 0.32, 0.68, 0, 0.84], [0, 1, 0.26, 0.74, 0.84, 1], [0.1, 0.34, 0.2, 0.3, 0.3, 0.62], [0.62, 0.88, 0.2, 0.3, 0.3, 0.62]]] },
  "plating-rail": { substance: "riveted-brass", heightFt: 0, directional: true, outward: false,
    smooth: false,     skirtFt: 13, skirtInset: 0.1, body: "ship", exact: false,
    variants: [
      [[0.08, 0.2, 0.42, 0.58, 0, 1], [0.44, 0.56, 0.42, 0.58, 0, 1], [0.8, 0.92, 0.42, 0.58, 0, 1], [0, 1, 0.44, 0.56, 0.8, 1], [0, 1, 0.45, 0.55, 0.34, 0.46]]] },
  railing: { substance: "spar-timber", heightFt: 0, directional: true, outward: false,
    smooth: false,     skirtFt: 9, skirtInset: 0.1, body: "ship", exact: false,
    variants: [
      [[0.08, 0.2, 0.42, 0.58, 0, 1], [0.44, 0.56, 0.42, 0.58, 0, 1], [0.8, 0.92, 0.42, 0.58, 0, 1], [0, 1, 0.44, 0.56, 0.8, 1], [0, 1, 0.45, 0.55, 0.34, 0.46]]] },
  scree: { substance: "scree", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: null },
  "sea-deck": { substance: "deck-planking", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 9, skirtInset: 0.1, body: "ship", exact: true,
    variants: null },
  "sewer-brick": { substance: "sewer-brick", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: null },
  "sewer-ledge": { substance: "wet-flagstone", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: null },
  "sky-deck": { substance: "deck-planking", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 14, skirtInset: 0.1, body: "ship", exact: true,
    variants: null },
  "sky-rail": { substance: "spar-timber", heightFt: 0, directional: true, outward: false,
    smooth: false,     skirtFt: 14, skirtInset: 0.1, body: "ship", exact: false,
    variants: [
      [[0.08, 0.2, 0.42, 0.58, 0, 1], [0.44, 0.56, 0.42, 0.58, 0, 1], [0.8, 0.92, 0.42, 0.58, 0, 1], [0, 1, 0.44, 0.56, 0.8, 1], [0, 1, 0.45, 0.55, 0.34, 0.46]]] },
  sludge: { substance: "sludge", heightFt: 0, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: null },
  "tent-canopy": { substance: "canvas", heightFt: 8, directional: true, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "tent", exact: true,
    variants: [
      [[0, 1, 0, 1, 0.66, 0.76], [0, 1, 0.42, 0.58, 0.76, 0.84]]] },
  "tower-ladder": { substance: "log-palisade", heightFt: 16, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: true,
    variants: [
      [[[[0.24, 0.93], [0.33, 0.93], [0.33, 0.84], [0.24, 0.84]], [[0.24, 0.53], [0.33, 0.53], [0.33, 0.44], [0.24, 0.44]], 0, 1], [[[0.67, 0.93], [0.76, 0.93], [0.76, 0.84], [0.67, 0.84]], [[0.67, 0.53], [0.76, 0.53], [0.76, 0.44], [0.67, 0.44]], 0, 1], [0.26, 0.74, 0.8, 0.88, 0.14, 0.18], [0.26, 0.74, 0.72, 0.8, 0.36, 0.4], [0.26, 0.74, 0.64, 0.72, 0.58, 0.62], [0.26, 0.74, 0.56, 0.64, 0.8, 0.84]]] },
  "tower-post": { substance: "log-palisade", heightFt: 17, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: true,
    variants: [
      [[[[0.26, 0.58], [0.58, 0.58], [0.58, 0.26], [0.26, 0.26]], [[0.4, 0.62], [0.62, 0.62], [0.62, 0.4], [0.4, 0.4]], 0, 0.88], [0.32, 0.72, 0.32, 0.72, 0.88, 1]]] },
  "tower-stone": { substance: "dressed-stone", heightFt: 16, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: false,
    variants: [
      [[0, 1, 0, 1, 0, 0.86], [0.02, 0.4, 0.02, 0.4, 0.86, 1], [0.6, 0.98, 0.02, 0.4, 0.86, 1]],
      [[0, 1, 0, 1, 0, 0.86], [0.3, 0.7, 0.02, 0.4, 0.86, 1], [0.02, 0.34, 0.6, 0.98, 0.86, 1]]] },
  "tower-top": { substance: "log-palisade", heightFt: 30, directional: false, outward: false,
    smooth: false,     skirtFt: 0, skirtInset: 0, body: "", exact: true,
    variants: [
      [[-2.1, 3.1, -2.1, 3.1, 0.44, 0.5], [-2.1, 3.1, -2.1, -1.9, 0.5, 0.6], [-2.1, 3.1, 2.9, 3.1, 0.5, 0.6], [-2.1, -1.9, -1.9, 2.9, 0.5, 0.6], [2.9, 3.1, -1.9, 2.9, 0.5, 0.6], [-1.95, -1.65, -1.95, -1.65, 0.58, 0.72], [2.65, 2.95, -1.95, -1.65, 0.58, 0.72], [-1.95, -1.65, 2.65, 2.95, 0.58, 0.72], [2.65, 2.95, 2.65, 2.95, 0.58, 0.72], [[[-2.3, 3.3], [3.3, 3.3], [3.3, -2.3], [-2.3, -2.3]], [[0.34, 0.66], [0.66, 0.66], [0.66, 0.34], [0.34, 0.34]], 0.7, 0.98], [0.36, 0.64, 0.36, 0.64, 0.98, 1]]] },
};
