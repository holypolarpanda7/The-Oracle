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
 *  list of parts [x0, x1, z0, z1, yFrom, yTo] — fractions of the
 *  square for x/z, of the tile's standing height for y. Which one a
 *  square uses comes from variantOf, so the same square always picks
 *  the same arrangement on both sides of the wire. */
export const OBJECT_VARIANTS: Record<string, readonly (readonly (readonly [
  number, number, number, number, number, number])[])[]> = {
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
export const DECOR_KINDS: Record<string, readonly [number,
  readonly (readonly [number, number, number, number, number, number])[]]> = {
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
  /** Pick the arrangement from a COARSE hash, so neighbours agree.
   *  For a MASS (rock, coral) rather than a set of objects. */
  readonly smooth: boolean;
  readonly variants: readonly (readonly (readonly [
    number, number, number, number, number, number])[])[] | null;
}
export const SKINS: Record<string, SkinShape> = {
  canvas: { substance: "canvas", heightFt: 8, directional: true, smooth: false,
    variants: [
      [[0, 1, 0.02, 0.98, 0, 0.26], [0, 1, 0.16, 0.84, 0.26, 0.56], [0, 1, 0.31, 0.69, 0.56, 0.82], [0, 1, 0.43, 0.57, 0.82, 1]]] },
  "cave-rock": { substance: "limestone", heightFt: 13, directional: false, smooth: true,
    variants: [
      [[0, 1, 0, 1, 0, 1]],
      [[0, 1, 0, 1, 0, 0.74], [0.14, 0.82, 0.2, 0.88, 0.74, 0.9]],
      [[0, 1, 0, 1, 0, 0.58], [0.28, 1, 0, 0.72, 0.58, 0.84]],
      [[0, 1, 0, 1, 0, 0.88], [0.1, 0.72, 0.24, 0.9, 0.88, 1]],
      [[0, 1, 0, 1, 0, 0.46]],
      [[0, 1, 0, 1, 0, 0.66], [0, 0.64, 0.3, 1, 0.66, 0.94]]] },
  chitin: { substance: "chitin", heightFt: 8, directional: true, smooth: false,
    variants: [
      [[0, 1, 0.34, 0.66, 0, 0.58], [0.06, 0.94, 0.28, 0.72, 0.58, 0.86], [0.22, 0.78, 0.36, 0.64, 0.86, 1]],
      [[0, 1, 0.3, 0.7, 0, 0.7], [0.14, 0.86, 0.36, 0.64, 0.7, 1]]] },
  "chitin-deck": { substance: "chitin", heightFt: 0, directional: false, smooth: false,
    variants: null },
  "chitin-rail": { substance: "chitin", heightFt: 0, directional: true, smooth: false,
    variants: [
      [[0.08, 0.2, 0.42, 0.58, 0, 1], [0.44, 0.56, 0.42, 0.58, 0, 1], [0.8, 0.92, 0.42, 0.58, 0, 1], [0, 1, 0.44, 0.56, 0.8, 1], [0, 1, 0.45, 0.55, 0.34, 0.46]]] },
  cliff: { substance: "granite", heightFt: 14, directional: false, smooth: true,
    variants: [
      [[0, 1, 0, 1, 0, 1]],
      [[0, 1, 0, 1, 0, 0.74], [0.14, 0.82, 0.2, 0.88, 0.74, 0.9]],
      [[0, 1, 0, 1, 0, 0.58], [0.28, 1, 0, 0.72, 0.58, 0.84]],
      [[0, 1, 0, 1, 0, 0.88], [0.1, 0.72, 0.24, 0.9, 0.88, 1]],
      [[0, 1, 0, 1, 0, 0.46]],
      [[0, 1, 0, 1, 0, 0.66], [0, 0.64, 0.3, 1, 0.66, 0.94]]] },
  coral: { substance: "coral", heightFt: 9, directional: false, smooth: true,
    variants: [
      [[0.2, 0.62, 0.24, 0.66, 0, 0.72], [0.5, 0.86, 0.44, 0.8, 0, 0.5], [0.3, 0.54, 0.34, 0.58, 0.72, 1]],
      [[0.16, 0.56, 0.3, 0.72, 0, 0.6], [0.46, 0.9, 0.18, 0.6, 0, 0.86], [0.34, 0.62, 0.5, 0.8, 0.6, 0.8]],
      [[0.24, 0.78, 0.22, 0.76, 0, 0.42], [0.36, 0.6, 0.34, 0.58, 0.42, 1], [0.6, 0.84, 0.5, 0.74, 0.42, 0.7]],
      [[0.1, 0.5, 0.2, 0.58, 0, 0.8], [0.44, 0.72, 0.46, 0.82, 0, 0.56], [0.2, 0.42, 0.3, 0.5, 0.8, 1]]] },
  deck: { substance: "deck-planking", heightFt: 0, directional: false, smooth: false,
    variants: null },
  "drowned-column": { substance: "drowned-stone", heightFt: 9, directional: false, smooth: false,
    variants: [
      [[0.3, 0.7, 0.3, 0.7, 0, 0.44], [0.32, 0.68, 0.32, 0.68, 0.44, 0.52]],
      [[0.3, 0.7, 0.3, 0.7, 0, 0.7], [0.34, 0.64, 0.3, 0.6, 0.7, 0.78]],
      [[0.32, 0.68, 0.32, 0.68, 0, 0.28], [0.1, 0.86, 0.22, 0.48, 0, 0.14]],
      [[0.28, 0.66, 0.34, 0.72, 0, 0.58], [0.3, 0.62, 0.36, 0.68, 0.58, 0.66], [0.6, 0.92, 0.3, 0.54, 0, 0.16]]] },
  "drowned-wall": { substance: "drowned-stone", heightFt: 0, directional: true, smooth: false,
    variants: [
      [[0, 1, 0.18, 0.78, 0, 0.72], [0.06, 0.62, 0.22, 0.7, 0.72, 1]],
      [[0, 1, 0.22, 0.8, 0, 0.58], [0.34, 0.94, 0.26, 0.74, 0.58, 1]]] },
  hull: { substance: "tarred-planking", heightFt: 0, directional: false, smooth: false,
    variants: null },
  masonry: { substance: "dressed-stone", heightFt: 0, directional: false, smooth: false,
    variants: null },
  mast: { substance: "spar-timber", heightFt: 26, directional: false, smooth: false,
    variants: [
      [[0.43, 0.57, 0.43, 0.57, 0, 1], [0.06, 0.94, 0.46, 0.54, 0.6, 0.655], [0.2, 0.8, 0.47, 0.53, 0.84, 0.875], [0.34, 0.66, 0.34, 0.66, 0, 0.06]]] },
  palisade: { substance: "log-palisade", heightFt: 10, directional: true, smooth: false,
    variants: [
      [[0, 0.24, 0.34, 0.66, 0, 0.9], [0.04, 0.2, 0.4, 0.6, 0.9, 1], [0.26, 0.5, 0.34, 0.66, 0, 0.96], [0.3, 0.46, 0.4, 0.6, 0.96, 1], [0.52, 0.76, 0.34, 0.66, 0, 0.88], [0.56, 0.72, 0.4, 0.6, 0.88, 1], [0.78, 1, 0.34, 0.66, 0, 0.94]]] },
  parapet: { substance: "dressed-stone", heightFt: 9, directional: true, smooth: false,
    variants: [
      [[0, 1, 0.3, 0.7, 0, 0.62], [0, 0.3, 0.26, 0.74, 0.62, 1], [0.42, 0.72, 0.26, 0.74, 0.62, 1]]] },
  "plated-deck": { substance: "riveted-brass", heightFt: 0, directional: false, smooth: false,
    variants: null },
  plating: { substance: "riveted-brass", heightFt: 8, directional: true, smooth: false,
    variants: [
      [[0, 1, 0.32, 0.68, 0, 0.84], [0, 1, 0.26, 0.74, 0.84, 1], [0.1, 0.34, 0.2, 0.3, 0.3, 0.62], [0.62, 0.88, 0.2, 0.3, 0.3, 0.62]]] },
  "plating-rail": { substance: "riveted-brass", heightFt: 0, directional: true, smooth: false,
    variants: [
      [[0.08, 0.2, 0.42, 0.58, 0, 1], [0.44, 0.56, 0.42, 0.58, 0, 1], [0.8, 0.92, 0.42, 0.58, 0, 1], [0, 1, 0.44, 0.56, 0.8, 1], [0, 1, 0.45, 0.55, 0.34, 0.46]]] },
  railing: { substance: "spar-timber", heightFt: 0, directional: true, smooth: false,
    variants: [
      [[0.08, 0.2, 0.42, 0.58, 0, 1], [0.44, 0.56, 0.42, 0.58, 0, 1], [0.8, 0.92, 0.42, 0.58, 0, 1], [0, 1, 0.44, 0.56, 0.8, 1], [0, 1, 0.45, 0.55, 0.34, 0.46]]] },
  scree: { substance: "scree", heightFt: 0, directional: false, smooth: false,
    variants: null },
  "sewer-brick": { substance: "sewer-brick", heightFt: 0, directional: false, smooth: false,
    variants: null },
  "sewer-ledge": { substance: "wet-flagstone", heightFt: 0, directional: false, smooth: false,
    variants: null },
  sludge: { substance: "sludge", heightFt: 0, directional: false, smooth: false,
    variants: null },
};
