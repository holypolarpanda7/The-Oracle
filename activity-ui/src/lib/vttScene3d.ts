/** The isometric tactical board.
 *
 *  A third reader of `VttEngine.state()`, beside the server's PNG and the flat
 *  canvas. It draws the same room those two draw, from the same payload, under
 *  the same rule: **the grid is the truth and the picture is a texture.** Every
 *  wall here stands where a tile code says it stands; nothing is invented, and
 *  nothing the model or the mesh does can move a square.
 *
 *  ## What is 3D and what is not
 *
 *  The floor and the structure are geometry. The creatures are not — they stay
 *  DOM elements over the canvas, positioned by `screenOf`. That is the whole
 *  billboard trick: an element over a canvas already faces the viewer, so a
 *  camera-facing character costs a projection and no character art. It also
 *  means every existing token affordance — portraits, HP fill, condition pips,
 *  cover badges, targeting states, click handling — arrives here working, with
 *  no second implementation to keep in step.
 *
 *  Being DOM is also the whole of why occlusion has to be computed rather than
 *  drawn: an element over a canvas is in front of everything by construction,
 *  so a creature behind a wall needs somebody to SAY so. `screenOf` marches a
 *  view ray over the grid and reports it — see `occludedAt` in boardView.ts.
 *
 *  ## What is deliberately not here yet
 *
 *  Nothing structural. Props, fog, light tint, effects, stair markers and the
 *  painted overlay all landed after the geometry, which is the point of having
 *  built it in that order — everything is layered onto a thing that already
 *  works offline, and turning any of it off leaves a playable board.
 *
 *  Lighting is form only. One directional light and some ambient exist so a
 *  wall reads as solid; the board's *mechanical* light level comes from
 *  `scene.light`, computed by the server, and will be a tint. Real lights here
 *  would let the picture disagree with the grid about who can see whom. */
import * as THREE from "three";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import type { VttObject, VttScene } from "./types";
import {
  CELL, DECOR_KINDS, DECOR_TINT, HOLE_CODES, OBJECT_VARIANTS, SKINS,
  SKIRT_FT, SKIRT_INSET,
  STRUCTURE_CODES, awayDir, cutawayHeightScale, cuttingAway, exposedRock,
  hullFootprint, squareUnderRay, surfaceLiftFt,
  isSetpieceSkin, isSolid, materialSlot,
  outAxis, outCorner, occludedAt, runAxis, setpieceYaw,
  sameBody,
  skinAt, skinHeightScale, variantSmooth,
  rotatePart,
  tileHeightFt,
  tileStyle, variantOf, wallParts, waterAt, yawOf,
  type BoardView, type Part, type PaintState, type TokenPlacement, type View,
} from "./boardView";
import {
  FRAME_PAD_SQUARES, YAW_DEG, basis, boundsOf, paintOpacity, project,
  unproject,
} from "./isocam";

/** How far back the camera sits. Orthographic, so this changes nothing about
 *  the image — it only has to clear the tallest thing on the board. */
const CAMERA_DISTANCE = 500;

/** Chamfer on the top edge of every extruded block, in world units (squares).
 *
 *  Small, and the single biggest reason the pre-painting board does not read as
 *  Minecraft. An unbevelled box meets the light along a mathematically perfect
 *  edge and catches no highlight, so it reads as a flat-shaded solid; a chamfer
 *  a few centimetres wide gives every edge a bright line and the eye reads a
 *  built thing. Cheap: four quads and a smaller top face per block. */
const BEVEL = 0.06;

/** Whether the painted layer is laid over the board.
 *
 *  ON. It was off for a while, and the reason is worth keeping: the painting
 *  was a screen-space quad rendered by a second WebGL camera, and that pass
 *  never drew — a correct rectangle, a loaded texture, no stencil, and not even
 *  a plain red quad appeared. Because a board WITH a painting switches its
 *  geometry to depth-only, the failure was not "no picture" but "no board".
 *
 *  The fix was to stop rendering it: the painting is a DOM image BEHIND an
 *  alpha canvas (see `backdropRect` and `VttOverlay`), which needs no second
 *  camera and no stencil, since it already carries its own alpha. Turning this
 *  off still restores the plain geometry board, which stays good,
 *  terrain-accurate and offline-safe. */
const PAINTED_BACKDROP = true;

/** Depth is a sort key for `z-index`, not a distance. Scaled up so that two
 *  creatures a single square apart still land on different integers. */
const DEPTH_STEPS = 8;

/** Accumulates triangles for one merged mesh.
 *
 *  Merging matters: a 40x30 board is 1200 floor tiles, and 1200 meshes is 1200
 *  draw calls, which is the difference between a board that runs on a phone
 *  inside a Discord webview and one that does not. */
/* A square used to take its UVs TURNED AND FLIPPED by its own coordinates —
 * eight arrangements of one swatch, so the eye would not read the repeat. What
 * it actually bought was that no surface on the board was continuous: rotation
 * breaks the seamlessness a tiling swatch is drawn for, so every square met its
 * neighbours along a mismatched edge and the grid showed as a lattice of
 * chevrons over grass, sand and stone alike. On anything with a GRAIN it was
 * ruinous — a ship's deck came back as basketwork and a taproom's boarded walls
 * and floor as a maze of nested outlines, which is not a deck or a floor that
 * anybody laid. Deleted rather than made conditional: the variety it gave was
 * variety of ORIENTATION, on materials that have one. See `quad`. */

/** A slow, smooth variation over the whole board, as a multiplier near 1.
 *
 *  The other half of why a board read as a tile engine. A seamless swatch laid
 *  at one scale over a whole meadow is the SAME picture at the same brightness
 *  in every square, and the eye finds that instantly however good the picture
 *  is. Real ground varies over yards: damp patches, wear, where the light has
 *  bleached it. Two octaves of value noise — one about seven squares across and
 *  one about two and a half — sampled at the vertex rather than the square, so
 *  it crosses square boundaries smoothly and never draws the grid it is there
 *  to hide.
 *
 *  Deterministic in world coordinates, like every other derived look on this
 *  board: the same square shades the same way every time it is drawn. */
function macroAt(x: number, z: number): number {
  const lattice = (ix: number, iz: number): number => {
    const h = Math.imul(ix | 0, 374761393) ^ Math.imul(iz | 0, 668265263);
    const m = Math.imul(h ^ (h >>> 13), 1274126177);
    return ((m ^ (m >>> 16)) >>> 0) / 4294967296;
  };
  const octave = (period: number): number => {
    const u = x / period;
    const v = z / period;
    const ix = Math.floor(u);
    const iz = Math.floor(v);
    const fx = u - ix;
    const fz = v - iz;
    // Smoothstep, or the lattice shows as a diamond grid of its own — which
    // would be the tile problem again at a different pitch.
    const sx = fx * fx * (3 - 2 * fx);
    const sz = fz * fz * (3 - 2 * fz);
    const a = lattice(ix, iz), b = lattice(ix + 1, iz);
    const c = lattice(ix, iz + 1), d = lattice(ix + 1, iz + 1);
    return (a + (b - a) * sx) + ((c + (d - c) * sx) - (a + (b - a) * sx)) * sz;
  };
  return 1 + (octave(7.3) - 0.5) * 0.20 + (octave(2.6) - 0.5) * 0.09;
}

/** How dark the foot of an upright face goes, and over how much of a square. */
const CONTACT_AO = 0.34;
const CONTACT_FT = 0.55;

/** How dark a floor goes in the crease of a corner. */
const CREASE_AO = 0.42;

class MeshBuilder {
  private pos: number[] = [];
  private norm: number[] = [];
  private col: number[] = [];
  private uv: number[] = [];
  /** Which SQUARE each vertex belongs to, so shading can be rewritten in place
   *  without rebuilding the mesh. See `reshade`. */
  private owner: number[] = [];
  /** The square subsequent geometry belongs to. */
  at = 0;
  /** How many times this material's picture repeats per square. One is the
   *  pitch of the grid, which is what made a board look like a tile set; the
   *  server decides it per substance (vtt/surface.SURFACE_TILE_FT). */
  uvScale = 1;
  /** Vary the albedo slowly across the board — see `macroAt`. Off for anything
   *  drawn in its own colour on purpose, where a variation would read as the
   *  thing being a different thing. */
  macro = true;

  get empty(): boolean { return this.pos.length === 0; }

  /** One quad, wound counter-clockwise as seen from the side the normal faces.
   *
   *  UVs come from the WORLD: one unit is one square, on a face of any size and
   *  at any angle. The unit square was the default everywhere but the roof, and
   *  it means "this face, whatever its size, shows exactly one copy of the
   *  picture" — so a sixteen-foot tower face and a four-foot crate face were
   *  drawn at four times the scale of each other, a wall's boards stopped and
   *  restarted at every square, and the BEVEL round every block top squeezed a
   *  whole swatch into a sliver. Same fix and the same sentence as the roof
   *  quad: one unit is one square. */
  quad(a: THREE.Vector3Like, b: THREE.Vector3Like, c: THREE.Vector3Like,
       d: THREE.Vector3Like, color: THREE.Color,
       uvs?: readonly [number, number][],
       aos?: readonly number[]): void {
    const nx = (b.y - a.y) * (c.z - a.z) - (b.z - a.z) * (c.y - a.y);
    const ny = (b.z - a.z) * (c.x - a.x) - (b.x - a.x) * (c.z - a.z);
    const nz = (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x);
    const len = Math.hypot(nx, ny, nz) || 1;
    // A face lying flat is read off the floor plan; an upright one along its
    // own run and up its own height, so two coplanar neighbours continue each
    // other instead of each starting the picture again.
    const flat = Math.abs(ny) >= Math.max(Math.abs(nx), Math.abs(nz));
    const tan = Math.hypot(nx, nz) || 1;
    // CONTACT SHADING. A cast shadow only darkens what the sun can see past;
    // the inside of every corner stays exactly as bright as open floor, which
    // is what leaves geometry looking like it is lying ON the ground rather
    // than standing in it. An upright face is darkened toward its own bottom
    // edge — always true of a real wall, needs nothing but the quad itself,
    // and it is what actually plants a thing on the floor.
    const footY = Math.min(a.y, b.y, c.y, d.y);
    for (const [i, j, k] of [[0, 1, 2], [0, 2, 3]] as const) {
      for (const idx of [i, j, k]) {
        const v = [a, b, c, d][idx];
        this.pos.push(v.x, v.y, v.z);
        this.norm.push(nx / len, ny / len, nz / len);
        let m = this.macro ? macroAt(v.x, v.z) : 1;
        if (aos) m *= aos[idx];
        else if (!flat) {
          m *= 1 - CONTACT_AO * Math.max(0, 1 - (v.y - footY) / CONTACT_FT);
        }
        this.col.push(color.r * m, color.g * m, color.b * m);
        const [u, w] = uvs ? uvs[idx]
          : flat ? [v.x, v.z] : [(-nz * v.x + nx * v.z) / tan, v.y];
        this.uv.push(u * this.uvScale, w * this.uvScale);
        this.owner.push(this.at);
      }
    }
  }

  build(): THREE.BufferGeometry {
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(this.pos, 3));
    g.setAttribute("normal", new THREE.Float32BufferAttribute(this.norm, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(this.col, 3));
    g.setAttribute("uv", new THREE.Float32BufferAttribute(this.uv, 2));
    return g;
  }

  owners(): Uint32Array { return Uint32Array.from(this.owner); }
}

/** Catalogue swatches by stored image id.
 *
 *  Module-level, like `boardSprites`, because that is what the thing being
 *  cached is: one swatch of dungeon flagstone serves every board in every
 *  session. A texture is created immediately and fills in when the bytes land,
 *  so the mesh never has to be rebuilt on arrival — only redrawn. */
const TEXTURES = new Map<number, THREE.Texture | null | undefined>();

/** Fetch a swatch, and tell the caller when its fate is decided.
 *
 *  Deliberately does NOT touch a material. Vertex colour carries fog, sight and
 *  light (see `shade`), so a mesh must know whether it is textured at BUILD
 *  time: textured, the vertex colour has to be a pure multiplier over white, or
 *  the tile's own colour tints the picture; untextured, it has to be that tile
 *  colour, or the board goes grey. Switching a live material from one to the
 *  other is what silently erased the fog tiers the first time.
 *
 *  So a texture arriving invalidates the terrain instead, and the next frame
 *  rebuilds knowing the answer. It resolves once per board and a rebuild is
 *  cheap, which makes this the honest trade rather than the clever one. */
function requestTexture(id: number, settled: () => void): void {
  if (TEXTURES.has(id)) return;                // resolved, or already in flight
  TEXTURES.set(id, undefined);
  new THREE.TextureLoader().load(
    `/imagery/image/${id}`,
    (t) => {
      t.wrapS = THREE.RepeatWrapping;
      t.wrapT = THREE.RepeatWrapping;
      t.colorSpace = THREE.SRGBColorSpace;
      t.anisotropy = 4;
      TEXTURES.set(id, t);
      settled();
    },
    undefined,
    // No imagery server (the offline demo) or a pruned id. Remembered as bad so
    // it is asked for once, and the board simply stays flat-coloured.
    () => { TEXTURES.set(id, null); settled(); },
  );
}

/** The average colour of a swatch, for geometry that cannot wear it.
 *
 *  A mesh out of a FILE has no uvs — the OBJ readers this project ships take
 *  `v` and `f` and nothing else — so no swatch can ever be laid on a landmark
 *  or a furniture model. They were drawn in the tile's flat palette colour
 *  instead, and that palette is chosen for a dark 2D board: a great stone
 *  statue and a ruined arch both came back VIOLET, standing on stone they were
 *  supposedly cut from. Worse in the other direction — on a textured board the
 *  tile colour is white, because it is a multiplier over a picture, so a
 *  furniture model would have been drawn as a white crate.
 *
 *  So a mesh takes the colour of the stuff it is MADE of. One pixel, sampled
 *  once per swatch: the browser does the averaging in `drawImage`, and an
 *  average is exactly right here — a textured surface beside it lights as its
 *  own average times the same light. */
const SWATCH_TINT = new Map<number, THREE.Color>();

function swatchTint(id: number | undefined | null): THREE.Color | null {
  if (id == null) return null;
  const got = SWATCH_TINT.get(id);
  if (got) return got;
  const tex = TEXTURES.get(id);
  const img = tex?.image as { width?: number } | undefined;
  if (!img?.width) return null;                // not landed yet, or never will
  try {
    const c = document.createElement("canvas");
    c.width = 1;
    c.height = 1;
    const g = c.getContext("2d", { willReadFrequently: true });
    if (!g) return null;
    g.drawImage(img as CanvasImageSource, 0, 0, 1, 1);
    const [r, gr, b] = g.getImageData(0, 0, 1, 1).data;
    const col = new THREE.Color().setRGB(r / 255, gr / 255, b / 255,
                                         THREE.SRGBColorSpace);
    SWATCH_TINT.set(id, col);
    return col;
  } catch {
    return null;                               // tainted canvas: keep the flat
  }
}

/** Derived surface channels (normal, roughness), by URL.
 *
 *  A second map rather than more entries in `TEXTURES` because these are not
 *  colour: they must NOT be decoded as sRGB. A normal map read through the
 *  sRGB curve is a normal map whose vectors are all subtly wrong, which lights
 *  as the material being a slightly different colour from the one next to it —
 *  the kind of wrongness nobody can point at and everybody can see.
 *
 *  Same economics as the swatches: one normal map of dungeon flagstone serves
 *  every board in every session, and it is derived from a stored picture that
 *  never changes, so the cache can never go stale. */
const SURFACE_MAPS = new Map<string, THREE.Texture | null | undefined>();

function requestSurface(url: string, settled: () => void): void {
  if (SURFACE_MAPS.has(url)) return;           // resolved, or already in flight
  SURFACE_MAPS.set(url, undefined);
  new THREE.TextureLoader().load(
    url,
    (t) => {
      t.wrapS = THREE.RepeatWrapping;
      t.wrapT = THREE.RepeatWrapping;
      // Linear, deliberately. See above.
      t.colorSpace = THREE.NoColorSpace;
      t.anisotropy = 4;
      SURFACE_MAPS.set(url, t);
      settled();
    },
    undefined,
    // No backend (the offline demo), or a swatch this server could not derive
    // from. Remembered as bad so it is asked for once, and the board is the
    // flat-lit board it has always been.
    () => { SURFACE_MAPS.set(url, null); settled(); },
  );
}

/** Landmark meshes, by URL. Module-level for the same reason as `TEXTURES`:
 *  one shipwreck serves every board in every session, and the file is
 *  committed and immutable so the cache can never go stale.
 *
 *  Only the GEOMETRY is kept — materials, normals and UVs in the file are
 *  discarded. A set piece contributes volume and silhouette; once the painted
 *  layer lands it is a depth occluder and draws no colour at all, which is
 *  what makes a stranger's art style cost so little here. */
const SETPIECE_MESHES = new Map<string, THREE.BufferGeometry | null | undefined>();

/** Fetch a landmark's mesh, and tell the caller when its fate is decided.
 *
 *  Same trade as `requestTexture`: a mesh arriving invalidates the terrain and
 *  the next frame rebuilds knowing the answer, rather than mutating live
 *  geometry. It resolves once per board and a rebuild is cheap. Until then the
 *  landmark is simply absent from the picture — never a placeholder box, which
 *  would be a shape the depth map does not have. */
function requestSetpiece(url: string, settled: () => void): void {
  if (SETPIECE_MESHES.has(url)) return;        // resolved, or already in flight
  SETPIECE_MESHES.set(url, undefined);
  new OBJLoader().load(
    url,
    (group) => {
      const geoms: THREE.BufferGeometry[] = [];
      group.traverse((o) => {
        const m = o as THREE.Mesh;
        if (m.isMesh && m.geometry) {
          // Position only. Merging needs every geometry to carry the same
          // attributes, and an OBJ's groups routinely disagree about whether
          // they have UVs — which fails the merge and loses the landmark.
          const src = m.geometry as THREE.BufferGeometry;
          const pos = src.getAttribute("position");
          if (!pos) return;
          const g = new THREE.BufferGeometry();
          g.setAttribute("position", pos.clone());
          if (src.index) g.setIndex(src.index.clone());
          // All non-indexed, because merging requires every input to agree on
          // that as well as on its attributes.
          geoms.push(src.index ? g.toNonIndexed() : g);
        }
      });
      if (!geoms.length) { SETPIECE_MESHES.set(url, null); settled(); return; }
      const merged = mergeGeometries(geoms, false);
      if (merged) merged.computeVertexNormals();
      SETPIECE_MESHES.set(url, merged ?? geoms[0]);
      settled();
    },
    undefined,
    // No asset server, or a pack nobody collected. Remembered as bad so it is
    // asked for once, and the board falls back to the tiles the piece stamped.
    () => { SETPIECE_MESHES.set(url, null); settled(); },
  );
}

const v3 = (x: number, y: number, z: number) => new THREE.Vector3(x, y, z);


/** How much of a square a door panel fills ACROSS its wall. Mirrors
 *  vtt/render_image.py's _PANEL_THICKNESS and the flat renderer's: a door is a
 *  plank in a wall, and drawn square and centred it reads as furniture parked
 *  on the floor. */
/** What the water SHEET is tinted, before fog and light.
 *
 *  Its own colour rather than the tile's: `~`'s swatch is a picture of the
 *  BED — silt, weed, sand — which is exactly right for the bottom of the basin
 *  and is not what looking at water looks like. */
const WATER_TINT = new THREE.Color("#3f6f86");

/** The water COLUMN, on a board fought inside it.
 *
 *  Every other thing a board draws is decided per square — a tile, a skin, a
 *  swatch — and the one thing that makes a reef read as a reef is a property of
 *  none of them: the water in front of all of them. The painted layer has put
 *  it back since the reef pass (`art._underwater_grade`), and the GEOMETRY
 *  never did, so an unpainted swim board — which is every swim board until its
 *  picture lands, and every one of them offline — was a dry seabed. Open water
 *  came back as a corrugated beige plain with pale patches on it.
 *
 *  It is fog, because fog is what a water column IS: tinted toward the sea's
 *  own colour, DARKER with distance, since water absorbs — a grade that pales
 *  with distance reads as mist, which is a thing that happens in air. */
const SEA_COLUMN = new THREE.Color("#12414f");

const PANEL_THICKNESS = 0.46;

/** Apertures — a gap in a wall rather than a block filling a square. */
const APERTURES: ReadonlySet<string> = new Set(["+", "/", "p"]);

/** What the party can see of a square, right now.
 *
 *  Two tiers, and both are needed or a door means nothing: `fog` is MEMORY and
 *  never dims, `sight` is recomputed per frame from real line of sight. With
 *  memory only, closing a door behind you changes nothing; with sight only, the
 *  party forgets the map every time it turns around. */
const enum Seen { Never = 0, Remembered = 1, Watched = 2 }

/** Dim and tint a tile for what can be seen of it and how lit it is.
 *
 *  Baked into the mesh rather than laid over it, because an overlay quad on the
 *  floor cannot darken a ten-foot wall standing on that square — the fog would
 *  stop at the skirting board. That is why fog, sight and light are part of the
 *  terrain cache key: they change on a move, not on a pointer flick. */
function shade(base: THREE.Color, seen: Seen, light: string): THREE.Color {
  const c = base.clone();
  if (seen === Seen.Never) return c.multiplyScalar(0.05);
  if (seen === Seen.Remembered) {
    // A cold veil: you remember the room, you are not watching it.
    c.multiplyScalar(0.4);
    return c.lerp(new THREE.Color(0x2b3c5e), 0.35);
  }
  if (light === "x") {
    // Watched but unlit — someone is seeing this by darkvision, and the rule
    // is that darkvision is greyscale. Rendering it merely dim would claim a
    // colour nobody in the room can actually make out.
    const g = c.r * 0.299 + c.g * 0.587 + c.b * 0.114;
    return c.setRGB(g, g, g).multiplyScalar(0.55);
  }
  return light === "d" ? c.multiplyScalar(0.72) : c;
}

/** A block with a chamfered top, emitting only the side faces that show.
 *
 *  Skipping buried faces is not only cheaper: a long wall run drawn as
 *  independent boxes has a bright chamfer line at every internal seam, which
 *  reads as a row of separate pillars rather than one wall. */
function block(mb: MeshBuilder, x: number, z: number, y0: number, y1: number,
               color: THREE.Color, exposed: (dx: number, dz: number) => boolean): void {
  boxFaces(mb, x, x + 1, z, z + 1, y0, y1, color, exposed);
}

/** A box over an arbitrary footprint, all four sides drawn.
 *
 *  For the things that do not fill their square: a door panel lying along its
 *  wall run, and a heap of wreckage. Nothing abuts them, so there are no shared
 *  faces to cull. */
function panelBlock(mb: MeshBuilder, x0: number, x1: number, z0: number,
                    z1: number, y0: number, y1: number, color: THREE.Color): void {
  boxFaces(mb, x0, x1, z0, z1, y0, y1, color, () => true);
}

/** A PRISMATOID: two polygons at two heights, joined edge by edge.
 *
 *  The primitive that stops everything being a cube. A box is the special case
 *  where both polygons are the same rectangle, so nothing had to be rewritten
 *  to gain tapers, leans and cut corners — a tent's canvas drawn in to a ridge,
 *  the raked legs of a timber watchtower, a hull tumbling home under the
 *  waterline. See `Part` in boardShapes.generated.ts.
 *
 *  Not chamfered, unlike `boxFaces`: the bevel exists to give a mathematically
 *  perfect edge a highlight to catch, and a shape whose faces already meet at
 *  arbitrary angles has plenty of those. */
function solidFaces(mb: MeshBuilder, ox: number, oz: number,
                    bottom: readonly (readonly [number, number])[],
                    top: readonly (readonly [number, number])[],
                    y0: number, y1: number, color: THREE.Color): void {
  const n = bottom.length;
  if (n < 3) return;
  // Top cap, as a fan of quads from its first vertex (one triangle of each is
  // degenerate — cheaper than a separate triangle path in MeshBuilder).
  const [tx0, tz0] = top[0];
  for (let i = 1; i < n - 1; i++) {
    const [ax, az] = top[i];
    const [bx, bz] = top[i + 1];
    mb.quad(v3(ox + tx0, y1, oz + tz0), v3(ox + ax, y1, oz + az),
            v3(ox + bx, y1, oz + bz), v3(ox + tx0, y1, oz + tz0), color);
  }
  for (let i = 0; i < n; i++) {
    const j = (i + 1) % n;
    const [ax, az] = bottom[i];
    const [bx, bz] = bottom[j];
    const [cx, cz] = top[i];
    const [dx, dz] = top[j];
    // Winding matters here for the same reason it does in boxFaces: `quad`
    // derives the normal from the vertex order, and a reversed face gets a
    // normal pointing into the solid, where the light finds nothing to catch.
    mb.quad(v3(ox + cx, y1, oz + cz), v3(ox + ax, y0, oz + az),
            v3(ox + bx, y0, oz + bz), v3(ox + dx, y1, oz + dz), color);
  }
}

/** Draw one arrangement's parts, in whichever of the two forms each is.
 *
 *  The one place the part vocabulary is interpreted in the browser; mirrors
 *  `draw_parts` in vtt/isocam.py, so a new form has exactly two places to
 *  reach. */
function drawParts(mb: MeshBuilder, parts: readonly Part[], turns: number,
                   ox: number, oz: number, base: number, height: number,
                   color: THREE.Color): void {
  for (const raw of parts) {
    const part = rotatePart(raw, turns);
    if (isSolid(part)) {
      const [bottom, top, py0, py1] = part;
      solidFaces(mb, ox, oz, bottom, top,
                 base + height * py0, base + height * py1, color);
    } else {
      const [px0, px1, pz0, pz1, py0, py1] = part;
      panelBlock(mb, ox + px0, ox + px1, oz + pz0, oz + pz1,
                 base + height * py0, base + height * py1, color);
    }
  }
}

function boxFaces(mb: MeshBuilder, x0: number, x1: number, z0: number, z1: number,
                  y0: number, y1: number, color: THREE.Color,
                  exposed: (dx: number, dz: number) => boolean): void {
  const top = y1;
  const rim = Math.max(y0, y1 - BEVEL);
  // A chamfer can never eat more than half of the thing it is chamfering — a
  // door panel is thinner than two bevels, and left unclamped its top face
  // turns inside out.
  const b = Math.min(BEVEL, (x1 - x0) / 3, (z1 - z0) / 3);

  // The top is inset ONLY where a side is actually exposed. Insetting all four
  // regardless left a bevel-wide slot down every seam of a wall run — two
  // neighbours each pulling their top face back from a shared edge that has no
  // side quad to close it — so a plain wall read as a row of separate blocks
  // with grooves between them. That was a large part of why this looked like
  // Minecraft, and it was a bug rather than a consequence of using boxes.
  const ix0 = exposed(-1, 0) ? x0 + b : x0;
  const ix1 = exposed(1, 0) ? x1 - b : x1;
  const iz0 = exposed(0, -1) ? z0 + b : z0;
  const iz1 = exposed(0, 1) ? z1 - b : z1;

  // Top face.
  mb.quad(v3(ix0, top, iz0), v3(ix0, top, iz1), v3(ix1, top, iz1), v3(ix1, top, iz0), color);

  // North (-z), south (+z), west (-x), east (+x). Each gets a wall face up to
  // the rim and a chamfer from the rim to the inset top.
  //
  // Winding is load-bearing here, not a detail: `quad` derives the normal from
  // the vertex order, so a reversed face does not merely cull — it gets a
  // normal pointing INTO the block, and the light then finds nothing to catch.
  // Every run below is ordered so (b-a) x (c-a) points out of the block.
  const faces: [number, number, THREE.Vector3[], THREE.Vector3[]][] = [
    [0, -1, [v3(x0, y0, z0), v3(x0, rim, z0), v3(x1, rim, z0), v3(x1, y0, z0)],
            [v3(x0, rim, z0), v3(ix0, top, iz0), v3(ix1, top, iz0), v3(x1, rim, z0)]],
    [0, 1, [v3(x1, y0, z1), v3(x1, rim, z1), v3(x0, rim, z1), v3(x0, y0, z1)],
           [v3(x1, rim, z1), v3(ix1, top, iz1), v3(ix0, top, iz1), v3(x0, rim, z1)]],
    [-1, 0, [v3(x0, y0, z1), v3(x0, rim, z1), v3(x0, rim, z0), v3(x0, y0, z0)],
            [v3(x0, rim, z1), v3(ix0, top, iz1), v3(ix0, top, iz0), v3(x0, rim, z0)]],
    [1, 0, [v3(x1, y0, z0), v3(x1, rim, z0), v3(x1, rim, z1), v3(x1, y0, z1)],
           [v3(x1, rim, z0), v3(ix1, top, iz0), v3(ix1, top, iz1), v3(x1, rim, z1)]],
  ];
  for (const [dx, dz, side, chamfer] of faces) {
    if (!exposed(dx, dz)) continue;
    mb.quad(side[0], side[1], side[2], side[3], color);
    mb.quad(chamfer[0], chamfer[1], chamfer[2], chamfer[3], color);
  }
}

/** Squares -> flat quads just above the floor. Used for every wash and marker. */
function decal(squares: Iterable<[number, number]>, y: number,
               color: string, opacity: number,
               lift: (x: number, z: number) => number = () => 0):
    THREE.Mesh | null {
  const pos: number[] = [];
  for (const [x, z] of squares) {
    // Every wash rides the floor it is painted on. Flat at the storey's height
    // it would sink under a ledge, so the movement range on a ship's deck read
    // as a stain on the sea beside it.
    const h = y + lift(x, z);
    pos.push(x, h, z, x, h, z + 1, x + 1, h, z + 1,
             x, h, z, x + 1, h, z + 1, x + 1, h, z);
  }
  if (!pos.length) return null;
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  const m = new THREE.MeshBasicMaterial({
    color: new THREE.Color(color), transparent: true, opacity,
    depthWrite: false, side: THREE.DoubleSide,
    polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
  });
  return new THREE.Mesh(g, m);
}

function disposeTree(obj: THREE.Object3D): void {
  obj.traverse((o) => {
    const m = o as THREE.Mesh | THREE.LineSegments;
    m.geometry?.dispose?.();
    const mat = m.material;
    if (Array.isArray(mat)) mat.forEach((x) => x.dispose());
    else mat?.dispose?.();
  });
}

export function createIsoBoardView(canvas: HTMLCanvasElement): BoardView {
  // `alpha` matters: the painted layer is a DOM image BEHIND this canvas, and
  // an opaque canvas would hide it.
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  renderer.setClearColor(0x000000, 0);
  // A rendered image rather than a diagram. Without tone mapping every colour
  // is its raw sRGB value clipped at white, which is why lit stone read as
  // flat paper — highlights have nowhere to roll off and the midtones sit dead
  // flat. ACES is the film curve everything modern uses; the exposure is above
  // one because the board is lit from a long way off and the curve pulls the
  // mids down.
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  // SHADOWS. The single largest difference between a diorama of flat tiles and
  // a room with things standing in it: without them nothing on the board is
  // attached to the ground it stands on. Cheap here for a reason peculiar to
  // this camera — the sun never moves and the board is static, so the map is
  // rendered once per rebuild rather than once per frame (see `sunlight`).
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.shadowMap.autoUpdate = false;

  const scene3 = new THREE.Scene();
  const camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, CAMERA_DISTANCE * 2);

  // Form only — see the header. Angled off the camera axis so the two visible
  // faces of every corner differ, which is what makes a block read as a block.
  const sun = new THREE.DirectionalLight(0xfff2dc, 2.1);
  sun.position.set(-0.4, 1, 0.65);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  // A thin wall lit almost edge-on is the classic acne case, and `normalBias`
  // is the fix that does not also detach every shadow from the thing casting
  // it — which is what a large depth bias does, and what makes a shadow read
  // as a decal lying beside the object.
  sun.shadow.bias = -0.0004;
  sun.shadow.normalBias = 0.03;
  scene3.add(sun);
  scene3.add(sun.target);
  // A cool fill from BELOW as well as above: a hemisphere light is what stops
  // the underside of every ledge, rail and hull going to flat black now that
  // the surfaces respond to light instead of merely being tinted by it.
  // Lowered from 1.25 when the sun learned to cast: fill that high is what
  // washed a shadow out to nothing, and the underside of a ledge going flat
  // black — the thing this light exists to prevent — is a question of it being
  // present at all, not of it being strong.
  scene3.add(new THREE.HemisphereLight(0xb9c8ff, 0x3a3326, 0.55));

  // Metalness is a switch, not a dial — a brass fitting either conducts or it
  // does not — and a metal with nothing to reflect renders BLACK. So the scene
  // carries a small procedural environment: cheap (a 256px cube, generated
  // once), and enough that brass reads as brass rather than as a hole.
  const pmrem = new THREE.PMREMGenerator(renderer);
  try {
    scene3.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    scene3.environmentIntensity = 0.2;
  } catch {
    // An environment is a luxury; a board that will not start is not.
  }
  pmrem.dispose();

  const terrainGroup = new THREE.Group();
  const decalGroup = new THREE.Group();
  scene3.add(terrainGroup, decalGroup);

  renderer.autoClear = false;

  /** The terrain mesh is expensive and the board redraws on every pointer move,
   *  so it is rebuilt only when the ROOM changes. Keying on `scene.revision`
   *  would rebuild on every step anyone takes; keying on the tile rows means a
   *  smashed pillar rebuilds and a walk does not. */
  let terrainKey = "";

  /** The last frame's arguments, so a swatch arriving late can repaint without
   *  the component being told. A texture fills into a material that is already
   *  on the mesh, so this is a redraw and never a rebuild. */
  let lastFrame: { st: PaintState; w: number; h: number } | null = null;
  let pending = 0;
  const redraw = () => {
    if (pending || !lastFrame) return;
    pending = requestAnimationFrame(() => {
      pending = 0;
      if (lastFrame) paintFrame(lastFrame.st, lastFrame.w, lastFrame.h);
    });
  };

  /** A swatch's fate has been decided: rebuild, because whether a code is
   *  textured changes what its vertex colours have to mean. */
  const invalidate = () => { terrainKey = ""; redraw(); };

  const heightUnits = (scene: VttScene, ft: number) => ft / (scene.square_ft || 5);
  const baseUnits = (scene: VttScene, level: number) =>
    heightUnits(scene, scene.levels?.[level]?.base_ft ?? 0);

  /** A square's own elevation above ITS OWN storey, in feet.
   *
   *  Stored on the board, shipped in `state()` and folded into every distance,
   *  reach, cover and area check since the board went 3D — and drawn by
   *  nobody, so a mountain-pass ledge stood ten feet up in the rules and flat
   *  in the picture. A creature standing on it rides it too, or the figure
   *  sinks into the ledge it is supposed to be on.
   *
   *  Per STOREY, and `scene.elevation` is the GROUND — the same split as
   *  terrain, fog, sight and light. Reading the flat field for a gallery is
   *  the hall's height map answering for a different room. Above the storey's
   *  own floor, never above the ground: `base` carries where the floor sits
   *  and adding the two is the caller's job, done once. */
  const elevFt = (scene: VttScene, x: number, y: number, level = 0) =>
    ((level ? scene.levels?.[level]?.elevation : undefined)
      ?? scene.elevation)?.[`${x},${y}`] ?? 0;

  /** The lowest FLOOR on the board, in feet below the storey's own.
   *
   *  The other end of `tallestUnits`, and it exists for the picker: a square
   *  sunk ten feet is reached by the view ray BEYOND the ground plane, so a
   *  march that started at the top and stopped at zero never saw it. Read off
   *  the sparse elevation map rather than by scanning every square, because
   *  that is where a hole in the floor is recorded. */
  function deepestFt(scene: VttScene, level = 0): number {
    let ft = 0;
    const own = (level ? scene.levels?.[level]?.elevation : undefined)
      ?? scene.elevation ?? {};
    for (const v of Object.values(own)) {
      if (typeof v === "number" && v < ft) ft = v;
    }
    return ft;
  }

  /** Tallest thing on the board, for framing and for the camera's far plane. */
  function tallestUnits(scene: VttScene, level = 0): number {
    let ft = 0;
    const rows = (level ? scene.levels?.[level]?.terrain : undefined)
      ?? scene.terrain ?? [];
    for (let z = 0; z < rows.length; z++) {
      for (let x = 0; x < rows[z].length; x++) {
        const skin = skinAt(scene, rows[z][x], x, z);
        // A skin may raise the drawn height and elevation raises the ground it
        // stands on. Both count, or a mast the framing never heard about comes
        // back with its top cropped off. Mirrors `tallest` in vtt/isocam.py.
        ft = Math.max(ft, (SKINS[skin]?.heightFt || tileHeightFt(rows[z][x]))
                          + elevFt(scene, x, z, level));
      }
    }
    return heightUnits(scene, ft);
  }

  /** Is a painting being drawn behind the geometry this frame? */
  let backdrop = false;

  /** Per code-mesh: which square each vertex belongs to, and the untinted
   *  colour that shading multiplies. Kept so fog, sight and light can be
   *  rewritten straight into the colour attribute.
   *
   *  Rebuilding the whole terrain for a shading change was the old behaviour
   *  and it happened on every step anyone took — geometry, normals and UVs all
   *  thrown away and remade because a torch moved. The positions did not
   *  change; only the tint did. */
  //
  //  `base` is the ONE colour a mesh starts from, which is right for terrain
  //  merged per material slot — every vertex of it is the same swatch. A mesh
  //  whose vertices carry DIFFERENT colours needs `tints`: a copy of the
  //  colours as built, so shading multiplies them instead of replacing them.
  //  Without it the scenery mesh — one builder for bushes, tussocks, deadfall,
  //  stumps and stones, each emitted in its own tint — was registered with a
  //  base of WHITE, and the first reshade painted every piece of it white. It
  //  had looked right for exactly one frame since fog shading went in.
  let shadeTargets: { geom: THREE.BufferGeometry; owners: Uint32Array;
                      base: THREE.Color;
                      tints?: Float32Array }[] = [];
  let shadeKey = "";

  function buildTerrain(scene: VttScene, level: number, showGrid: boolean,
                        yawDeg: number = YAW_DEG): void {
    disposeTree(terrainGroup);
    terrainGroup.clear();

    const rows = scene.terrain ?? [];
    const base = baseUnits(scene, level);
    const at = (x: number, z: number): string | null => {
      if (z < 0 || z >= rows.length) return null;
      const r = rows[z];
      if (x < 0 || x >= r.length) return null;
      return r[x];
    };

    // One builder per TILE CODE, not one for the whole floor: each code carries
    // its own swatch, and a mesh has one material. Still a handful of draw
    // calls for a whole board — a code, not a square.
    // Keyed by material SLOT, not tile code: a board can carry a log palisade
    // and canvas tents, both of them '#', and merging them into one mesh would
    // give them one swatch between them.
    const byCode = new Map<string, MeshBuilder>();
    // The WATER, kept apart from every other builder because it is the one thing
    // on the board you can see THROUGH. See vtt/water.py: the bed is cut into a
    // basin below its bank and this is the level sheet put back on top of it.
    const waterMb = new MeshBuilder();
    const builderFor = (slot: string) => {
      let b = byCode.get(slot);
      if (!b) {
        b = new MeshBuilder();
        // How often this material's picture repeats, in squares. The server
        // says how much WORLD one repeat covers (vtt/surface.tile_ft): ground
        // is fifteen feet and made things are five, so a meadow stops
        // repeating at the pitch of the grid and a plank stays a plank.
        const ft = scene.surfaces?.[slot]?.tile_ft;
        if (ft && ft > 0) b.uvScale = (scene.square_ft || 5) / ft;
        byCode.set(slot, b);
      }
      return b;
    };
    const gridPts: number[] = [];
    const swatches = scene.materials ?? {};

    /** What this square is MADE of. Material and silhouette only — no rule
     *  reads a skin. See vtt/skins.py. */
    const skinOf = (x: number, z: number): string => {
      const c = at(x, z);
      return c === null ? "" : skinAt(scene, c, x, z);
    };

    /** How high this square's FLOOR is drawn, or null if it is not there.
     *  Null means a hole or off the board — nothing to stand on, and the square
     *  beside it needs a side all the way down rather than a step. Mirrors
     *  `floor_y` in vtt/isocam.py. */
    const floorY = (x: number, z: number): number | null => {
      const c = at(x, z);
      if (c === null || HOLE_CODES.has(c)) return null;
      return base + heightUnits(scene, elevFt(scene, x, z, level));
    };

    // Which way the wall runs through each aperture. Read off the server's own
    // objects list rather than re-derived here: `aperture_axis` already
    // answered it once, from the grid, for every view.
    const axes = new Map<string, string>();
    for (const o of scene.objects ?? []) {
      if (o.axis) axes.set(`${o.x},${o.y}`, o.axis);
    }
    const wrecked = new Set((scene.debris ?? []).map((d) => `${d.x},${d.y}`));

    // Which codes will actually be drawn with a picture on this pass. Anything
    // still in flight counts as untextured for now and the mesh is rebuilt when
    // it lands — see `requestTexture`.
    const textured = new Set<string>();
    for (const [code, id] of Object.entries(swatches)) {
      const t = TEXTURES.get(id);
      if (t instanceof THREE.Texture) textured.add(code);
      else if (t === undefined) requestTexture(id, invalidate);
    }
    // The relief and the shine. Asked for only where there is a picture to lay
    // them over — a normal map on a flat-coloured tile has no surface to
    // modulate and would only make the tile light unevenly for no reason.
    const surfaces = scene.surfaces ?? {};
    // Which squares have a MODEL rather than a silhouette out of the tables.
    const models = new Map<string, NonNullable<VttObject["model"]>>();
    for (const o of scene.objects ?? []) {
      if (o.model) models.set(`${o.x},${o.y}`, o.model);
    }
    for (const code of textured) {
      const sf = surfaces[code];
      if (!sf) continue;
      for (const url of [sf.normal, sf.rough_map]) {
        if (url && SURFACE_MAPS.get(url) === undefined
            && !SURFACE_MAPS.has(url)) requestSurface(url, invalidate);
      }
    }

    /** The one place a terrain material is built.
     *
     *  `MeshStandardMaterial` rather than Lambert, which is the whole PBR
     *  change: Lambert has no concept of how rough a surface is, so wet
     *  flagstones, brass fittings and dry limestone all returned the same
     *  light for the same orientation and the board read as coloured
     *  cardboard. Standard at roughness ~0.9 and metalness 0 is
     *  indistinguishable from the Lambert it replaces, so a material nobody
     *  has classified looks exactly as it did.
     *
     *  Vertex colour still carries fog, sight and light — see `shade` — which
     *  is why a textured mesh must be built knowing it is textured: the
     *  vertex colour is a multiplier over white there, and the tile's own
     *  colour anywhere else. */
    const terrainMaterial = (code: string): THREE.Material => {
      const has = textured.has(code);
      const tex = has ? TEXTURES.get(swatches[code]) : null;
      const sf = has ? surfaces[code] : undefined;
      const nrm = sf ? SURFACE_MAPS.get(sf.normal) : null;
      const rgh = sf ? SURFACE_MAPS.get(sf.rough_map) : null;
      return new THREE.MeshStandardMaterial({
        vertexColors: true,
        roughness: sf ? sf.roughness : 0.9,
        metalness: sf ? sf.metalness : 0.0,
        ...(tex instanceof THREE.Texture ? { map: tex } : {}),
        ...(nrm instanceof THREE.Texture
          ? { normalMap: nrm, normalScale: new THREE.Vector2(1, 1) } : {}),
        ...(rgh instanceof THREE.Texture ? { roughnessMap: rgh } : {}),
        // With a painting behind it the geometry stops drawing and becomes a
        // depth-only proxy — invisible, but still occluding the decals and
        // answering "is a wall in front of this creature". That is precisely
        // what Baldur's Gate shipped beside each of its backgrounds, and it is
        // why the geometry is not thrown away once the picture arrives.
        ...(backdrop ? { colorWrite: false } : {}),
      });
    };

    /** How boxed-in a point on the floor is, in [0, 1].
     *
     *  The other half of contact shading, and the half a cast shadow cannot
     *  give you: a corner is dark because most of the SKY is blocked from it,
     *  which is true whichever way the sun happens to be pointing. Sampled at
     *  the four squares meeting at the nearest grid corner, so it is a
     *  property of the corner and two squares sharing an edge agree about it —
     *  the same rule `corner_lift_ft` follows for the ground itself.
     *
     *  Memoised per corner: a 46x34 board has 1,600 of them and every vertex
     *  of every floor lands on one. */
    const occCache = new Map<number, number>();
    const occAt = (px: number, pz: number): number => {
      const ix = Math.round(px);
      const iz = Math.round(pz);
      const key = iz * 4096 + ix;
      const got = occCache.get(key);
      if (got !== undefined) return got;
      let n = 0;
      for (const [dx, dz] of [[-1, -1], [0, -1], [-1, 0], [0, 0]] as const) {
        const c = at(ix + dx, iz + dz);
        // Off the board is open sky, not a wall — a board's own rim must not
        // come back with a dark border painted round it.
        if (c === null) continue;
        if (STRUCTURE_CODES.has(c) || tileHeightFt(c) > 0) n++;
      }
      const occ = n / 4;
      occCache.set(key, occ);
      return occ;
    };

    /** Floor a creature could stand on — not structure, not off the board.
     *  Void counts as closed: on an upper storey it is open air, and a wall
     *  should not grow a face onto a hole. Mirrors is_open in vtt/isocam.py. */
    const isOpen = (x: number, z: number): boolean => {
      const c = at(x, z);
      return c !== null && !HOLE_CODES.has(c) && !STRUCTURE_CODES.has(c);
    };

    const seenAt = (x: number, z: number): Seen => {
      if (!scene.fog) return Seen.Watched;          // no fog = all visible
      if (scene.sight?.[z]?.[x] === "1") return Seen.Watched;
      return scene.fog[z]?.[x] === "1" ? Seen.Remembered : Seen.Never;
    };
    const lightAt = (x: number, z: number): string =>
      scene.light?.[z]?.[x] ?? "b";

    for (let z = 0; z < rows.length; z++) {
      for (let x = 0; x < rows[z].length; x++) {
        const code = rows[z][x];
        // A HOLE, not dark ground. Void is open air on an upper storey; open
        // sky IS air; a chasm is the absence of floor. Drawing any of them as a
        // surface is the geometry inventing ground the rules say you fall
        // through — a sky-islands board is mostly open sky, and paved over it
        // came back a flat plane instead of stones floating in nothing.
        if (HOLE_CODES.has(code)) continue;

        // The UNTINTED colour. Fog, sight and light are applied afterwards by
        // `reshade`, straight into the colour attribute, so a torch moving does
        // not throw away the geometry it is lighting.
        // NB: not `base` — that is the storey height, a few lines up.
        const skin = skinOf(x, z);
        const shape = skin ? SKINS[skin] : undefined;
        const slot = materialSlot(code, skin);
        const tint = textured.has(slot) ? "#ffffff" : tileStyle(code).fill;
        const color = new THREE.Color(tint);
        // Heights get a little life, except where the rules quote one. A skin
        // may raise the drawn height — and is refused at import from doing so
        // on any tile whose height the rules DO quote, so this cannot smuggle
        // a lie past heightScale.
        const full = (shape?.heightFt || tileHeightFt(code))
          * skinHeightScale(skin, code, x, z);
        // A room is a box and the camera looks into it over a corner, so the
        // two walls nearest the lens stand between the viewer and the fight.
        // They come down to a stub wherever the geometry IS the picture — see
        // cutAwayAt. Structure only, which is the same thing as "never a height
        // the rules quote", and `drawnTopFt` applies the identical reduction so
        // the board's account of who is hidden follows what it drew.
        const h = full * cutawayHeightScale(scene, x, z, yawDeg, full, level);
        const mb = builderFor(slot);
        // Everything emitted from here belongs to this square, so `reshade` can
        // find its vertices again without rebuilding anything.
        mb.at = z * scene.width + x;

        // Three ways a floor can END, and it needs a side or it is a sheet of
        // paper hanging in nothing. Against a HOLE, which gives an island its
        // underside. Against a LOWER square, which is a ledge or a quay or a
        // ship's freeboard. Or against anything that is not the same BODY,
        // which is how a vessel gets a hull: deep water is not a hole, so a
        // sea ship used to have no sides at all.
        const here = floorY(x, z) ?? 0;
        // Sides are indexed the way `hullFootprint` winds them: W, S, E, N.
        const nbrs: [number, number][] =
          [[x - 1, z], [x, z + 1], [x + 1, z], [x, z - 1]];
        const sideEnds: boolean[] = [];
        const sideDrop: number[] = [];
        for (const [nx2, nz2] of nbrs) {
          if (shape?.skirtFt) {
            // A VESSEL, and its side is not this square's business: the hull
            // is one traced SHELL over the whole body (scene.shells), because
            // joining the corners farthest from the middle needs the outline
            // as a loop and no square can see one.
            sideEnds.push(false);
            sideDrop.push(here);
            continue;
          }
          const below = floorY(nx2, nz2);
          if (below === null) {
            sideEnds.push(true);
            sideDrop.push(here - heightUnits(scene, SKIRT_FT));
          } else if (below < here - 1e-9) {
            sideEnds.push(true);
            sideDrop.push(below);
          } else {
            sideEnds.push(false);
            sideDrop.push(here);
          }
        }
        const { pts, ends: edgeEnds, low } = hullFootprint(
          sideEnds[0], sideEnds[2], sideEnds[3], sideEnds[1], SKIRT_INSET);

        // Floor under everything: an object stands ON a square, and without
        // this a pillar's base is a hole in the ground. Cut to the outline, so
        // a hull that steps a square at a time is drawn as the diagonal it
        // means rather than as a staircase.
        // The surface, not the plate. Elevation is stored per square as whole
        // feet, so a hillside drawn at one height per square is a flight of
        // terraces — which is most of why an outdoor board reads as stacked
        // blocks. Every vertex of the fan takes its own height off the shared
        // CORNERS (see surfaceLiftFt), so two squares meet exactly and a ledge
        // still keeps its vertical face.
        const ground = (u: number, w: number) =>
          base + heightUnits(scene, surfaceLiftFt(scene, x, z, u, w, level));
        const crease = (u: number, w: number) =>
          1 - CREASE_AO * occAt(x + u, z + w);
        for (let k = 1; k < pts.length - 1; k++) {
          mb.quad(v3(x + pts[0][0], ground(pts[0][0], pts[0][1]), z + pts[0][1]),
                  v3(x + pts[k][0], ground(pts[k][0], pts[k][1]), z + pts[k][1]),
                  v3(x + pts[k + 1][0], ground(pts[k + 1][0], pts[k + 1][1]),
                     z + pts[k + 1][1]),
                  v3(x + pts[0][0], ground(pts[0][0], pts[0][1]), z + pts[0][1]),
                  color, undefined,
                  [crease(pts[0][0], pts[0][1]), crease(pts[k][0], pts[k][1]),
                   crease(pts[k + 1][0], pts[k + 1][1]),
                   crease(pts[0][0], pts[0][1])]);
        }
        for (let k = 0; k < pts.length; k++) {
          if (!edgeEnds[k]) continue;
          const m = (k + 1) % pts.length;
          const [ax, az] = pts[k];
          const [bx, bz] = pts[m];
          if (Math.hypot(bx - ax, bz - az) < 1e-9) continue;
          // A chamfered outline has cut the sides about, so its edges no longer
          // match up one-for-one with the four compass drops; a vessel has one
          // depth all round, which is why the chamfer is only offered where a
          // skin declares its own side.
          const drop = sideDrop[k];
          // The bottom comes from `hullFootprint`, which MITRES it at every
          // vertex — offsetting each side along its own normal keeps a straight
          // run coplanar and opens a wedge of daylight wherever the outline
          // turns, which on a hull is every corner of the bow.
          mb.quad(v3(x + ax, ground(ax, az), z + az),
                  v3(x + low[k][0], drop, z + low[k][1]),
                  v3(x + low[m][0], drop, z + low[m][1]),
                  v3(x + bx, ground(bx, bz), z + bz),
                  color);
        }

        // The water over this square, if it is under any. Flush to the
        // square's own edges rather than inset: two pool squares meet bank to
        // bank, and a sheet that shrank from its outline would be a grid of
        // puddles with mortar between them.
        const wft = waterAt(scene, x, z, level);
        if (wft !== null) {
          const wy = base + heightUnits(scene, wft);
          if (wy > here + 1e-6) {
            waterMb.at = z * scene.width + x;
            waterMb.quad(v3(x, wy, z), v3(x, wy, z + 1),
                         v3(x + 1, wy, z + 1), v3(x + 1, wy, z),
                         WATER_TINT);
          }
        }

        if (showGrid && seenAt(x, z) !== Seen.Never) {
          // The grid follows the ground, or a slope has its own squares
          // floating over it.
          gridPts.push(x, ground(0, 0), z, x + 1, ground(1, 0), z);
          gridPts.push(x, ground(0, 0), z, x, ground(0, 1), z + 1);
        }

        if (h > 0 && isSetpieceSkin(skinAt(scene, code, x, z))) {
          // The landmark's own mesh is this square's standing geometry, and
          // drawing the tile's shape as well puts a statue inside a pillar.
          // The floor above has already been laid, which is the half that must
          // NOT be skipped: a set piece's walkable squares are real ground at
          // a real elevation. Mirrors vtt/isocam.py.
        } else if (h > 0) {
          const top = here + heightUnits(scene, h);
          const axis = axes.get(`${x},${z}`);
          if (APERTURES.has(code) && axis) {
            // A door belongs to the wall it interrupts: a thin panel lying
            // along that wall's run, never a block filling its square.
            const t = PANEL_THICKNESS / 2;
            const [x0, x1] = axis === "ew" ? [x, x + 1] : [x + 0.5 - t, x + 0.5 + t];
            const [z0, z1] = axis === "ew" ? [z + 0.5 - t, z + 0.5 + t] : [z, z + 1];
            panelBlock(mb, x0, x1, z0, z1, here, top, color);
          } else if (shape?.variants) {
            // A skin's silhouette wins over everything, INCLUDING the wall-face
            // model. That is the point: a mountainside drawn as thin panels
            // round a corridor is what made the pass read as architecture.
            // What survives from the wall model is the part that pays for
            // itself — a structure square with no open side is buried, and
            // buried rock is not drawn.
            if (!STRUCTURE_CODES.has(code) || exposedRock(isOpen, x, z)) {
              const vs = shape.variants;
              const pick = shape.smooth ? variantSmooth : variantOf;
              let parts = vs[pick(x, z, vs.length)];
              // "Part of the same structure" means the same BODY, a solid
              // neighbour, or an aperture. The second lets a DOORWAY find its
              // wall — its neighbours are the tower's own masonry, a different
              // skin, so matching on skin alone left every door facing an
              // arbitrary way. The third is the same rule pointed the other
              // way: a doorway belongs to the wall it interrupts, so a tent
              // must not take its own flap for the outdoors.
              const same = (ax: number, az: number) => {
                if (sameBody(skinOf(ax, az), skin)) return true;
                const c = at(ax, az);
                return c !== null && (STRUCTURE_CODES.has(c) || APERTURES.has(c));
              };
              // An outward skin does NOT roll for its arrangement: which one
              // it wears is a fact about where the square sits. Arrangement 0
              // is the plain run, 1 is the CORNER — aimed at a single outside,
              // the other side of a corner is left a sheer face, and a tent
              // had two pitched sides and two cliffs.
              const atCorner = shape.outward && vs.length > 1
                && outCorner(same, x, z);
              if (shape.outward) parts = vs[atCorner ? 1 : 0];
              const turns = shape.outward ? outAxis(same, x, z, atCorner)
                : shape.directional ? runAxis(same, x, z)
                : yawOf(x, z);
              drawParts(mb, parts, turns, x, z, here, top - here, color);
            }
          } else if (STRUCTURE_CODES.has(code)) {
            // A thin skin where the solid region meets open floor, never a full
            // five-foot cube. The square stays solid in the RULES — this only
            // stops the wall's top face swallowing the room at this camera
            // angle. See wallParts.
            for (const [wx0, wx1, wz0, wz1] of wallParts(isOpen, x, z)) {
              panelBlock(mb, x + wx0, x + wx1, z + wz0, z + wz1, here, top, color);
            }
          } else {
            // Everything else is a THING standing on a square, and things are
            // not cubes. Each gets a silhouette of roughly its real footprint —
            // a 5-ft square is a big place, and a pillar filling one is the
            // single loudest voxel tell on the board.
            const cx = x + 0.5, cz = z + 0.5;
            const model = models.get(`${x},${z}`);
            if (model) {
              // A MODEL for this kind of thing. Scaled to the height the board
              // would have DRAWN on this square, so a quoted height stays
              // quoted and a jittered one still jitters — see vtt/furniture.py.
              // Not merged into the tile's builder: it came out of a file
              // rather than out of the shape tables, so it is placed with a
              // transform like a landmark rather than emitted vertex by vertex.
              requestSetpiece(model.mesh, invalidate);
              const geom = SETPIECE_MESHES.get(model.mesh);
              if (geom) {
                const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
                  // Its own material's colour, never the square's `color` —
                  // that is white wherever the square is textured, because it
                  // multiplies a picture this mesh has no uvs to carry.
                  color: swatchTint(swatches[slot]) ?? color,
                  roughness: 0.86, metalness: 0.0,
                  ...(backdrop ? { colorWrite: false } : {}),
                }));
                const k = model.unit_scale * heightUnits(scene, h);
                const [px, py, pz] = model.pivot;
                mesh.scale.setScalar(k);
                mesh.position.set(-px * k, -py * k, -pz * k);
                const spin = new THREE.Group();
                spin.add(mesh);
                spin.rotation.y = (yawOf(x, z) * Math.PI) / 2;
                const holder = new THREE.Group();
                holder.add(spin);
                holder.position.set(cx, here, cz);
                terrainGroup.add(holder);
              }
            } else if (OBJECT_VARIANTS[code]) {
              // A built silhouette, in one of several arrangements chosen by the
              // square itself — shared with the depth map the painted layer is
              // conditioned on, so what stands here is what gets painted here.
              const vs = OBJECT_VARIANTS[code];
              const parts = vs[variantOf(x, z, vs.length)];
              drawParts(mb, parts, yawOf(x, z), x, z, here, top - here, color);
            } else {
              block(mb, x, z, here, top, color, () => true);
            }
          }
        } else if (wrecked.has(`${x},${z}`)) {
          // Something stood here and was broken. The square's terrain already
          // changed to what it left, so the floor is right — but wreckage that
          // is only a change of colour appears from nowhere. A low heap says a
          // thing came down here.
          panelBlock(mb, x + 0.12, x + 0.88, z + 0.12, z + 0.88,
                     here, here + 0.22, color);
        }
      }
    }

    // Vessel hulls. One traced outline per ship rather than a side per square
    // — see vtt/hull.py for why that cannot be done a square at a time. The
    // server ships the answer, so this draws and never derives.
    for (const shell of scene.shells ?? []) {
      const loop = shell.loop ?? [];
      const low2 = shell.low ?? loop;
      if (loop.length < 3) continue;
      const slot = shell.slot || "b";
      const mb = builderFor(slot);
      mb.at = 0;
      const col = new THREE.Color(
        textured.has(slot) ? "#ffffff" : tileStyle(slot.split("@")[0]).fill);
      const top = base + heightUnits(scene, shell.top_ft || 0);
      const drop = top - heightUnits(scene, shell.drop_ft || 0);
      // The deck out to its own hull: each triangle is what a smoothed notch
      // gave up, so without them the planking stops short of the line.
      for (const tri of shell.fill ?? []) {
        mb.quad(v3(tri[0][0], top, tri[0][1]), v3(tri[1][0], top, tri[1][1]),
                v3(tri[2][0], top, tri[2][1]), v3(tri[0][0], top, tri[0][1]), col);
      }
      for (let i = 0; i < loop.length; i++) {
        const j = (i + 1) % loop.length;
        mb.quad(v3(loop[i][0], top, loop[i][1]), v3(low2[i][0], drop, low2[i][1]),
                v3(low2[j][0], drop, low2[j][1]), v3(loop[j][0], top, loop[j][1]),
                col);
      }
    }

    // ROOFS. One per building, traced over its footprint — see vtt/hull.py's
    // `roofs` for why a gable per square makes a terrace into a row of huts.
    // Drawn into the SKIN's own builder, so a roof wears the material of the
    // building under it rather than a colour of its own.
    for (const roof of scene.roofs ?? []) {
      const eaves = roof.eaves ?? [];
      const ridge = roof.ridge ?? eaves;
      if (eaves.length < 3 || ridge.length !== eaves.length) continue;
      // A roof may be made of something no square is — see Skin.roof_skin.
      // The server names that material outright; empty means the roof wears
      // the building's own, which is right for a ruin whose tiles are gone.
      const slot = roof.slot || materialSlot("#", roof.skin);
      const mb = builderFor(slot);
      mb.at = 0;
      const col = new THREE.Color(
        textured.has(slot) ? "#ffffff" : tileStyle("#").fill);
      const lo = base + heightUnits(scene, roof.eaves_ft || 0);
      const hi = base + heightUnits(scene, roof.ridge_ft || 0);
      for (let i = 0; i < eaves.length; i++) {
        const j = (i + 1) % eaves.length;
        // A pitch. Where the ridge has collapsed the quad's two upper corners
        // coincide and it degenerates to the triangle a hip end really is.
        // The SAME cycle the depth map walks (vtt/isocam.py): eaves i, eaves j,
        // ridge j, ridge i. Reversed, the normal points into the roof and the
        // pitch is culled — the building comes back with no top, and neither
        // program looks wrong on its own.
        // WORLD UVs, not the default 0..1. A quad's corners default to the
        // unit square, which is right for a floor tile — one square, one
        // repeat — and stretches the whole swatch across a pitch six squares
        // long. Every roof on a staged street came back as a set of nested
        // bands, a ziggurat rather than a roof, and the geometry was correct
        // the whole time. One unit is one square here, exactly as on the floor.
        mb.quad(v3(eaves[i][0], lo, eaves[i][1]), v3(eaves[j][0], lo, eaves[j][1]),
                v3(ridge[j][0], hi, ridge[j][1]), v3(ridge[i][0], hi, ridge[i][1]),
                col,
                [eaves[i] as [number, number], eaves[j] as [number, number],
                 ridge[j] as [number, number], ridge[i] as [number, number]]);
      }
      // The ridge itself, so a hip is closed rather than open to the sky.
      const flat = ridge.every((p) => Math.abs(p[0] - ridge[0][0]) < 1e-9
                                   && Math.abs(p[1] - ridge[0][1]) < 1e-9);
      if (!flat) {
        for (let i = 1; i + 1 < ridge.length; i++) {
          mb.quad(v3(ridge[0][0], hi, ridge[0][1]),
                  v3(ridge[i][0], hi, ridge[i][1]),
                  v3(ridge[i + 1][0], hi, ridge[i + 1][1]),
                  v3(ridge[0][0], hi, ridge[0][1]), col,
                  [ridge[0] as [number, number], ridge[i] as [number, number],
                   ridge[i + 1] as [number, number],
                   ridge[0] as [number, number]]);
        }
      }
    }

    // Scenery, into its own builder so it shares the plain untextured material
    // and never inherits a wall's swatch. It stands ON the floor and occludes
    // nothing the rules care about, so it needs no ordering.
    const decorMb = new MeshBuilder();
    for (const d of scene.decor ?? []) {
      const spec = DECOR_KINDS[d.kind];
      if (!spec) continue;
      const [ft, parts] = spec;
      decorMb.at = d.y * scene.width + d.x;
      drawParts(decorMb, parts, yawOf(d.x, d.y), d.x, d.y, floorY(d.x, d.y) ?? base,
                heightUnits(scene, ft), new THREE.Color(DECOR_TINT[d.kind] ?? "#6b6255"));
    }

    // Landmarks. Not built into a MeshBuilder like everything else: the
    // geometry came out of a file rather than out of the shape tables, so it
    // is placed with a transform instead of being emitted vertex by vertex.
    // Every term of that transform is the SERVER'S — `scale` and `pivot` are
    // measured off this same mesh by setpieces.mesh_fit — so there is no
    // arithmetic here to arrive at differently.
    for (const sp of scene.setpieces ?? []) {
      if (!sp.mesh || !sp.scale) continue;
      requestSetpiece(sp.mesh, invalidate);
      const geom = SETPIECE_MESHES.get(sp.mesh);
      if (!geom) continue;                     // in flight, or never collected
      const spSlot = materialSlot(sp.code || "#",
                                  skinAt(scene, sp.code || "#", sp.x, sp.y));
      const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
        color: swatchTint(swatches[spSlot])
               ?? new THREE.Color(tileStyle(sp.code || "#").fill),
        roughness: 0.86, metalness: 0.0,
        ...(backdrop ? { colorWrite: false } : {}),
      }));
      const [px, py, pz] = sp.pivot ?? [0, 0, 0];
      // scale -> centre on the footprint -> yaw -> stand on the floor.
      // Half a square per unit of width is what puts an even-sided landmark on
      // the seam and an odd-sided one on its middle square's own centre.
      mesh.scale.setScalar(sp.scale);
      mesh.position.set(-px, -py, -pz);
      const pivot = new THREE.Group();
      pivot.add(mesh);
      // The handedness lives in `setpieceYaw`, where the alignment gate can
      // reach it — `rotation.y` is inside three.js and cannot be compared
      // against the Python.
      pivot.rotation.y = setpieceYaw(sp.yaw_fix ?? 0, sp.yaw);
      const holder = new THREE.Group();
      holder.add(pivot);
      holder.position.set(sp.x + sp.w / 2,
                          floorY(sp.x, sp.y) ?? base,
                          sp.y + sp.d / 2);
      terrainGroup.add(holder);
    }

    shadeTargets = [];
    if (!decorMb.empty) {
      const geom = decorMb.build();
      shadeTargets.push({
        geom, owners: decorMb.owners(), base: WHITE.clone(),
        // Each piece of scenery is emitted in its OWN tint, so the colours as
        // built are the thing to shade rather than something to overwrite.
        tints: Float32Array.from(
          (geom.getAttribute("color") as THREE.BufferAttribute)
            .array as Float32Array),
      });
      terrainGroup.add(new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
        vertexColors: true, roughness: 0.92, metalness: 0.0,
        ...(backdrop ? { colorWrite: false } : {}),
      })));
    }
    if (!waterMb.empty) {
      const geom = waterMb.build();
      // Shaded like everything else — a pool in an unlit crypt is as dark as
      // the floor beside it — but drawn LAST and without writing depth, so the
      // bed stays visible through it from every angle.
      shadeTargets.push({ geom, owners: waterMb.owners(),
                          base: WATER_TINT.clone() });
      const mesh = new THREE.Mesh(geom, new THREE.MeshStandardMaterial({
        vertexColors: true, roughness: 0.18, metalness: 0.0,
        transparent: true, opacity: 0.72, depthWrite: false,
        ...(backdrop ? { colorWrite: false } : {}),
      }));
      mesh.renderOrder = 2;
      terrainGroup.add(mesh);
    }
    for (const [code, mb] of byCode) {
      if (mb.empty) continue;
      const geom = mb.build();
      shadeTargets.push({
        geom, owners: mb.owners(),
        base: new THREE.Color(textured.has(code) ? "#ffffff" : tileStyle(code).fill),
        // The colours as BUILT, because they are no longer one colour per
        // mesh: `macroAt` varies every vertex a little. Same reason the
        // scenery builder needed them — shading multiplies a tint rather than
        // replacing it, and without this the first shading pass would flatten
        // the variation straight back out.
        tints: Float32Array.from(
          (geom.getAttribute("color") as THREE.BufferAttribute)
            .array as Float32Array),
      });
      terrainGroup.add(new THREE.Mesh(geom, terrainMaterial(code)));
    }
    shadeKey = "";      // force a tint pass over the new geometry

    // THE SUN, fitted to this board. A directional light shadows through an
    // orthographic camera of its own, and one sized to the board keeps the
    // texels dense — about six to the foot at 2048 — where a fixed generous
    // box would spend most of its map on empty space and give every edge a
    // staircase.
    const span = Math.max(scene.width, scene.height);
    const dir = new THREE.Vector3(-0.4, 1, 0.65).normalize();
    sun.position.set(scene.width / 2 + dir.x * span, dir.y * span,
                     scene.height / 2 + dir.z * span);
    sun.target.position.set(scene.width / 2, base, scene.height / 2);
    sun.target.updateMatrixWorld();
    const shadowCam = sun.shadow.camera as THREE.OrthographicCamera;
    // Three quarters of the long side covers the DIAGONAL, which is what a
    // light coming in across the corner actually has to reach.
    const reach = span * 0.75;
    shadowCam.left = -reach;
    shadowCam.right = reach;
    shadowCam.top = reach;
    shadowCam.bottom = -reach;
    shadowCam.near = 0.5;
    shadowCam.far = span * 2.5;
    shadowCam.updateProjectionMatrix();
    // Everything with a real material casts and receives. The washes, the
    // markers and the grid are MeshBasicMaterial and are skipped by that test,
    // which is right: a movement range is a diagram drawn ON the floor, and a
    // diagram that cast a shadow would be a thing in the room.
    terrainGroup.traverse((o) => {
      const m = o as THREE.Mesh;
      if (!m.isMesh) return;
      const mat = m.material as THREE.Material & { isMeshStandardMaterial?: boolean };
      if (!mat?.isMeshStandardMaterial) return;
      m.castShadow = true;
      m.receiveShadow = true;
      // THE reason the first attempt cast nothing at all. For a FrontSide
      // material three renders BACK faces into the shadow map, which is the
      // right default for closed solids and silently wrong for everything
      // here: this board is built out of open single-sided SHEETS — a roof
      // pitch, a floor quad, a wall face — and the face a sheet turns to the
      // sun is exactly the one that gets culled. Nothing looks broken; there
      // are simply no shadows.
      mat.shadowSide = THREE.DoubleSide;
    });
    // Once per rebuild, never per frame: the sun does not move and the board
    // is static, so panning and turning the camera cost no shadow work at all.
    renderer.shadowMap.needsUpdate = true;

    if (gridPts.length) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(gridPts, 3));
      terrainGroup.add(new THREE.LineSegments(g, new THREE.LineBasicMaterial({
        color: 0x8fa2d8, transparent: true, opacity: 0.16,
        polygonOffset: true, polygonOffsetFactor: -1,
      })));
    }
  }

  /** Rewrite fog, sight and light into the colour attribute, in place.
   *
   *  Cheap for a reason worth stating: shading only ever takes nine values —
   *  three visibility tiers by three light levels — so the colours are computed
   *  nine times per mesh and the per-vertex loop is a table lookup and three
   *  writes. No allocation, no geometry, nothing for the collector.
   */
  const WHITE = new THREE.Color("#ffffff");

  /** (visibility tier, light level) for a square, as one small integer. */
  function _shadeKey(scene: VttScene, tile: number, w: number,
                     fog: string[] | null | undefined,
                     sight: string[] | null | undefined,
                     light: string[] | null | undefined): number {
    const z = (tile / w) | 0;
    const x = tile - z * w;
    const seen: Seen = !fog ? Seen.Watched
      : sight?.[z]?.[x] === "1" ? Seen.Watched
      : fog[z]?.[x] === "1" ? Seen.Remembered : Seen.Never;
    const lv = light?.[z]?.[x] ?? "b";
    return seen * 4 + (lv === "x" ? 2 : lv === "d" ? 1 : 0);
  }

  function reshade(scene: VttScene, level: number, showFog: boolean): void {
    const fog = showFog ? scene.fog : null;
    const sight = showFog ? scene.sight : null;
    const light = scene.light;
    const w = scene.width;

    for (const { geom, owners, base, tints } of shadeTargets) {
      const attr = geom.getAttribute("color") as THREE.BufferAttribute;
      const arr = attr.array as Float32Array;
      // Memoised by (tier, light) — nine possibilities, whatever the board size.
      const cache = new Map<number, THREE.Color>();
      let lastTile = -1, r = 0, g = 0, b = 0;
      for (let v = 0; v < owners.length; v++) {
        const tile = owners[v];
        if (tints) {
          // Per-vertex colours: shade the vertex's OWN tint. Still nine
          // multipliers, still a table lookup — the shade is computed against
          // white and applied as a factor.
          const i = v * 3;
          const k = _shadeKey(scene, owners[v], w, fog, sight, light);
          // WATCHED BUT UNLIT is the one shade that is not a multiplier: the
          // rule is that darkvision is greyscale, and a factor computed
          // against white cannot take the colour out of a tint — it would
          // claim a colour nobody in the room can make out. Done per vertex,
          // off the vertex's own tint.
          if ((k >> 2) === Seen.Watched && (k & 3) === 2) {
            const y = (tints[i] * 0.299 + tints[i + 1] * 0.587
                       + tints[i + 2] * 0.114) * 0.55;
            arr[i] = y; arr[i + 1] = y; arr[i + 2] = y;
            continue;
          }
          let f = cache.get(k);
          if (!f) { f = shade(WHITE, (k >> 2) as Seen, "bdx"[k & 3] ?? "b");
                    cache.set(k, f); }
          arr[i] = tints[i] * f.r;
          arr[i + 1] = tints[i + 1] * f.g;
          arr[i + 2] = tints[i + 2] * f.b;
          continue;
        }
        if (tile !== lastTile) {
          const z = (tile / w) | 0;
          const x = tile - z * w;
          const seen: Seen = !fog ? Seen.Watched
            : sight?.[z]?.[x] === "1" ? Seen.Watched
            : fog[z]?.[x] === "1" ? Seen.Remembered : Seen.Never;
          const lv = light?.[z]?.[x] ?? "b";
          const key = seen * 4 + (lv === "x" ? 2 : lv === "d" ? 1 : 0);
          let c = cache.get(key);
          if (!c) { c = shade(base, seen, lv); cache.set(key, c); }
          r = c.r; g = c.g; b = c.b;
          lastTile = tile;
        }
        const i = v * 3;
        arr[i] = r; arr[i + 1] = g; arr[i + 2] = b;
      }
      attr.needsUpdate = true;
    }
  }

  function buildDecals(st: PaintState, level: number): void {
    disposeTree(decalGroup);
    decalGroup.clear();
    const { scene } = st;
    const y = baseUnits(scene, level) + 0.012;
    const lift = (x: number, z: number) =>
      heightUnits(scene, elevFt(scene, x, z, level));
    const keys = (m: Iterable<string>) =>
      [...m].map((k) => k.split(",").map(Number) as [number, number]);

    const add = (mesh: THREE.Mesh | null) => { if (mesh) decalGroup.add(mesh); };

    // Spell areas, zones, auras and lingering hazards. Their `squares` are the
    // SERVER's — already clipped by line of effect — so this only paints what
    // it is handed and never works out a footprint of its own. Drawn first, so
    // the things a player is deciding right now sit above them.
    if (st.show.effects) {
      for (const e of scene.effects ?? []) {
        if ((e.level ?? 0) !== level || !e.squares?.length) continue;
        add(decal(e.squares, y + 0.001, e.color || "#a86bff",
                  Math.min(0.7, Math.max(0.12, e.opacity || 0.3)), lift));
      }
    }

    // A way off this floor is a feature of the room, and a player cannot choose
    // a stair they cannot see. The destination's NAME is a DOM label; this is
    // the mark on the board underneath it.
    for (const s of st.stairs ?? []) {
      add(decal([[s.x, s.y]], y + 0.009, "#e6bc64", 0.42, lift));
    }

    // Threatened ground goes UNDER the movement wash, so a player sees the
    // danger while choosing rather than after the pointer has settled.
    if (st.threatened) add(decal(keys(st.threatened), y, "#ff6b57", 0.2, lift));
    if (st.reach) add(decal(keys(st.reach.keys()), y + 0.002, "#5aa9e6", 0.22, lift));
    if (st.area) {
      add(decal(st.area, y + 0.006, st.areaLegal === false ? "#ff6b57" : "#c07bff", 0.34, lift));
    }
    if (st.path) add(decal(st.path, y + 0.004, st.pathProvokes ? "#ffb347" : "#8fd6ff", 0.4, lift));
    if (st.hover) add(decal([st.hover], y + 0.008, "#e6bc64", 0.3, lift));

    // A base under each creature. Without it a standee floats: the disc is
    // drawn at head height and nothing says which square it belongs to.
    const bases: [number, number][] = [];
    for (const t of scene.tokens) {
      if ((t.level ?? 0) !== level || t.defeated) continue;
      // Mid-walk the base follows the creature; left at t.x/t.y it would sit
      // at the destination while the figure was still crossing the room.
      const wk = st.walking && st.walking.tokenId === t.id ? st.walking : null;
      const bx = wk ? wk.x : t.x;
      const bz = wk ? wk.y : t.y;
      for (let dx = 0; dx < t.squares; dx++) {
        for (let dz = 0; dz < t.squares; dz++) bases.push([bx + dx, bz + dz]);
      }
    }
    add(decal(bases, y + 0.01, "#0a0d16", 0.35, lift));
  }

  return {
    // The geometry is real 3D and never moves; only the lens does.
    canTurn: true,

    fit(scene: VttScene, w: number, h: number, yaw: number = YAW_DEG): View {
      const pad = 18;
      const b = boundsOf(scene.width, scene.height, tallestUnits(scene), 0, yaw);
      const spanX = Math.max(1e-6, b.maxX - b.minX);
      const spanY = Math.max(1e-6, b.maxY - b.minY);
      const scale = Math.max(0.28, Math.min(
        (w - pad * 2) / (CELL * spanX),
        (h - pad * 2) / (CELL * spanY),
        1.6,
      ));
      return {
        scale,
        ox: w / 2 - CELL * scale * (b.minX + b.maxX) / 2,
        oy: h / 2 - CELL * scale * (b.minY + b.maxY) / 2,
        yaw,
      };
    },

    squareAt(view: View, scene: VttScene, px: number, py: number,
             level: number): [number, number] | null {
      const k = CELL * view.scale;
      const yaw = view.yaw ?? YAW_DEG;
      const [wx, wz] = unproject((px - view.ox) / k, (py - view.oy) / k,
                                 baseUnits(scene, level), yaw);
      // Unprojecting answers "which square would be here if the board were
      // flat", and it has not been flat since elevation went in — so on a dais
      // or in a channel the square you click is not the square you get. The
      // ray march is the correction: see squareUnderRay.
      const [x, y] = squareUnderRay(
        scene, wx, wz, yaw,
        (scene.square_ft || 5) * tallestUnits(scene, level),
        deepestFt(scene, level), level);
      // Unlike the flat board this CAN miss: the viewport is a rectangle and
      // the board inside it is a diamond, so a good part of the frame is not
      // over the board at all. Reporting a square there would let a click on
      // empty space walk someone off the map.
      if (x < 0 || y < 0 || x >= scene.width || y >= scene.height) return null;
      return [x, y];
    },

    screenOf(view: View, scene: VttScene, x: number, y: number, squares: number,
             level: number, elevationFt: number): TokenPlacement {
      const k = CELL * view.scale;
      // The square's own elevation PLUS whatever the creature is doing on top
      // of it: a wyvern hovering over a ledge is above both.
      const footFt = elevFt(scene, x, y, level) + elevationFt;
      const wy = baseUnits(scene, level) + heightUnits(scene, footFt);
      const p = project(x + squares / 2, wy, y + squares / 2,
                        view.yaw ?? YAW_DEG);
      const size = k * squares;
      return {
        left: view.ox + k * p.x - size / 2,
        // The token's FOOT sits on the square, not its middle — that is what
        // makes a flat disc read as a figure standing on the board rather than
        // a counter lying on it.
        top: view.oy + k * p.y - size,
        size,
        depth: p.depth * DEPTH_STEPS,
        // Marched over the grid, not read off the picture — the geometry draws
        // no colour once a painting lands, so there is nothing to read. Heights
        // are relative to this STOREY: only one floor is ever drawn, so an
        // upper gallery is not standing in the hall's way.
        occluded: occludedAt(scene, x, y, squares, footFt,
                             view.yaw ?? YAW_DEG, level),
      };
    },

    groundAt(view: View, scene: VttScene, px: number, py: number,
             level: number): [number, number] {
      const k = CELL * view.scale;
      return unproject((px - view.ox) / k, (py - view.oy) / k,
                       baseUnits(scene, level), view.yaw ?? YAW_DEG);
    },

    zoomAt(view: View, px: number, py: number, factor: number): View {
      const scale = Math.max(0.2, Math.min(3, view.scale * factor));
      const k = scale / view.scale;
      return { scale, ox: px - (px - view.ox) * k, oy: py - (py - view.oy) * k,
               yaw: view.yaw };
    },

    backdropRect(view: View, scene: VttScene) {
      if (!PAINTED_BACKDROP || !scene.iso_image_id) return null;
      // A painting is a photograph of the room from ONE place, and turning the
      // camera away from that place makes it a picture of somewhere else. It
      // fades out rather than being clipped or stretched — see paintOpacity.
      if (paintOpacity(view.yaw ?? YAW_DEG) <= 0) return null;
      const k = CELL * view.scale;
      // At the CANONICAL yaw, always: the rectangle the picture was baked to
      // is a fact about the bake, not about where the viewer is looking now.
      const b = boundsOf(scene.width, scene.height, tallestUnits(scene));
      const p = FRAME_PAD_SQUARES;
      return {
        left: view.ox + k * (b.minX - p),
        top: view.oy + k * (b.minY - p),
        width: k * (b.maxX - b.minX + p * 2),
        height: k * (b.maxY - b.minY + p * 2),
      };
    },

    draw(st: PaintState, w: number, h: number): void {
      lastFrame = { st, w, h };
      paintFrame(st, w, h);
    },

    dispose(): void {
      if (pending) cancelAnimationFrame(pending);
      lastFrame = null;
      disposeTree(scene3);
      renderer.dispose();
    },
  };

  function paintFrame(st: PaintState, w: number, h: number): void {
      if (w === 0 || h === 0) return;
      const { scene, view } = st;
      const level = st.level ?? 0;

      renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
      renderer.setSize(w, h, false);
      canvas.style.width = `${w}px`;
      canvas.style.height = `${h}px`;

      // The painted layer. Only the ground floor has one — an upper storey is
      // its own room and would need its own painting, so peeking upstairs falls
      // back to geometry rather than showing the hall's picture on a gallery.
      const isoId = level === 0 ? scene.iso_image_id ?? 0 : 0;
      const isoTex = isoId ? TEXTURES.get(isoId) : null;
      if (isoId && isoTex === undefined) requestTexture(isoId, invalidate);
      backdrop = PAINTED_BACKDROP && isoTex instanceof THREE.Texture;

      // Fog, live sight and light are baked into the mesh (see `shade`), so
      // they belong in the key. They change on a MOVE, not on a pointer flick,
      // so this rebuilds when someone walks and stays cached while they aim.
      // Geometry depends on the ROOM. Shading does not, and used to be baked
      // into the same key — so every step anyone took rebuilt the whole mesh
      // because the fog had moved. They are two keys now: the room is rare, the
      // tint is every frame that matters.
      // The CUT SET is part of the room now. It does not change with every
      // degree of a drag — `awayDir` is one of eight — so this rebuilds eight
      // times in a full turn rather than on every frame of one.
      const away = awayDir(view.yaw ?? YAW_DEG);
      const cutting = cuttingAway(scene, view.yaw ?? YAW_DEG);
      const key = [
        scene.id, level, st.show.grid, st.show.terrain, backdrop,
        cutting ? `${away[0]},${away[1]}` : "-",
        // The cut set is read off THIS storey's tiles; `level` is already in
        // the key above, which is what keeps a gallery from being cut to the
        // hall's plan.
        (scene.terrain ?? []).join(""),
        (scene.debris ?? []).map((d) => `${d.x},${d.y}`).join(";"),
      ].join("|");
      if (key !== terrainKey) {
        buildTerrain(scene, level, st.show.grid, view.yaw ?? YAW_DEG);
        terrainKey = key;
      }
      const tint = [
        st.show.fog, (scene.fog ?? []).join(""), (scene.sight ?? []).join(""),
        (scene.light ?? []).join(""),
      ].join("|");
      if (tint !== shadeKey) {
        reshade(scene, level, st.show.fog);
        shadeKey = tint;
      }
      buildDecals(st, level);

      // Drive the orthographic camera from `View`, so pan and zoom mean exactly
      // what they mean on the flat board and `screenOf` and the rendered image
      // cannot drift apart.
      const k = CELL * view.scale;
      // Whichever way the viewer has turned. The geometry is real 3D and never
      // moves; the only thing rotation touches is where the lens is, which is
      // why turning cost nothing here and everything to the painted layer.
      const cam = basis(view.yaw ?? YAW_DEG);
      const halfW = w / 2 / k;
      const halfH = h / 2 / k;
      const cx = (w / 2 - view.ox) / k;
      const cyUp = (view.oy - h / 2) / k;
      const target = new THREE.Vector3(
        cam.RIGHT[0] * cx + cam.UP[0] * cyUp,
        cam.RIGHT[1] * cx + cam.UP[1] * cyUp,
        cam.RIGHT[2] * cx + cam.UP[2] * cyUp,
      );
      camera.left = -halfW;
      camera.right = halfW;
      camera.top = halfH;
      camera.bottom = -halfH;
      camera.near = 0.1;
      camera.far = CAMERA_DISTANCE * 2;
      camera.position.set(
        target.x - cam.FORWARD[0] * CAMERA_DISTANCE,
        target.y - cam.FORWARD[1] * CAMERA_DISTANCE,
        target.z - cam.FORWARD[2] * CAMERA_DISTANCE,
      );
      camera.up.set(cam.UP[0], cam.UP[1], cam.UP[2]);
      camera.lookAt(target);
      camera.updateProjectionMatrix();

      // The water column, measured against the board rather than guessed: the
      // four corners' depths along the view axis say where the near and far
      // edges of the picture are, whatever the camera has been turned to. The
      // far edge keeps some of itself (the margin), because a board whose back
      // half is solid sea colour is a board you cannot fight on.
      if (scene.mode === "swim") {
        // All THREE components: the lens looks DOWN as well as along, so
        // dropping the y term loses a constant the size of the camera's own
        // height and puts the whole board past the far plane — which is a
        // board drawn as one flat slab of sea colour.
        const depth = (x: number, z: number) =>
          (x - camera.position.x) * cam.FORWARD[0]
          + (0 - camera.position.y) * cam.FORWARD[1]
          + (z - camera.position.z) * cam.FORWARD[2];
        const ds = [depth(0, 0), depth(scene.width, 0),
                    depth(0, scene.height), depth(scene.width, scene.height)];
        const near = Math.min(...ds);
        const far = Math.max(...ds);
        scene3.fog = new THREE.Fog(SEA_COLUMN, near - (far - near) * 0.35,
                                   far + (far - near) * 0.55);
      } else {
        scene3.fog = null;
      }

      // One pass. The painting is a DOM layer BEHIND this canvas (see
      // `backdropRect`), which is why the canvas is alpha and why the geometry
      // stops drawing colour when a painting exists — it stays as a depth-only
      // proxy so the decals still occlude correctly.
      //
      // It was a second WebGL camera rendering a screen-space quad, and that
      // never drew: the pass was reached with a correct rectangle and a loaded
      // texture, and a plain red quad with no stencil did not appear either.
      // A DOM image needs none of that machinery, and the painting already
      // carries its own alpha, so the corners are handled without a stencil.
      renderer.clear(true, true, true);
      renderer.render(scene3, camera);
  }
}
