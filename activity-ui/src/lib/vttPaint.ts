/** Canvas painting for the tactical board.
 *
 *  Kept out of the component so the draw pass is a pure function of
 *  (scene, view, interaction state) — easy to reason about, and cheap to call
 *  on every animation frame while someone drags a token around.
 *
 *  The server's tile grid is the truth. When a diffusion battlemap exists it is
 *  stretched across the board rectangle as *texture*, and blockers are still
 *  outlined faintly on top so nobody has to guess which painted rock is really
 *  a wall. */
import type { VttScene } from "./types";

export interface View { scale: number; ox: number; oy: number }

type TileStyle = { fill: string; edge?: string; family: "floor" | "solid" | "water" | "rough" | "hazard" | "void" };

/** Tile code -> how it paints. Mirrors vtt/terrain.py's TILES table. */
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

/** Screen pixels per square at the current zoom. */
export const CELL = 44;

export function toScreen(v: View, x: number, y: number): [number, number] {
  return [v.ox + x * CELL * v.scale, v.oy + y * CELL * v.scale];
}

export function toSquare(v: View, px: number, py: number): [number, number] {
  return [
    Math.floor((px - v.ox) / (CELL * v.scale)),
    Math.floor((py - v.oy) / (CELL * v.scale)),
  ];
}

/** Fit the whole board into a viewport, with a little breathing room. */
export function fitView(scene: VttScene, w: number, h: number): View {
  const pad = 16;
  const scale = Math.max(
    0.28,
    Math.min(
      (w - pad * 2) / (scene.width * CELL),
      (h - pad * 2) / (scene.height * CELL),
      1.6,
    ),
  );
  return {
    scale,
    ox: (w - scene.width * CELL * scale) / 2,
    oy: (h - scene.height * CELL * scale) / 2,
  };
}

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

/** How much of a square a door panel fills ACROSS its wall. Mirrors
 *  vtt/render_image.py's _PANEL_THICKNESS — a door is a plank in a wall, and
 *  drawn square and centred it reads as furniture parked on the floor. */
const PANEL_THICKNESS = 0.46;

function readySprite(st: PaintState, id?: number | null): HTMLImageElement | null {
  if (id == null) return null;
  const img = st.sprites?.get(id);
  return img && img.complete && img.naturalWidth > 0 ? img : null;
}

/** Draw an aperture as a panel lying IN its wall, with jamb ticks. */
function panel(ctx: CanvasRenderingContext2D, img: HTMLImageElement | null,
               sx: number, sy: number, cell: number, axis: string, color: string) {
  const thick = Math.max(3, Math.round(cell * PANEL_THICKNESS));
  const inset = (cell - thick) / 2;
  const ns = axis === "ns";
  const [px, py, pw, ph] = ns
    ? [sx + inset, sy, thick, cell]
    : [sx, sy + inset, cell, thick];

  if (img) {
    ctx.save();
    if (ns) {
      // The sprite is painted as a horizontal panel; stand it on end rather
      // than squashing it sideways, or its planks run the wrong way.
      ctx.translate(px + pw / 2, py + ph / 2);
      ctx.rotate(Math.PI / 2);
      ctx.drawImage(img, -ph / 2, -pw / 2, ph, pw);
    } else {
      ctx.drawImage(img, px, py, pw, ph);
    }
    ctx.restore();
  } else {
    ctx.fillStyle = "rgba(96,74,41,0.92)";
    ctx.fillRect(px, py, pw, ph);
  }

  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.strokeRect(px + 1, py + 1, pw - 2, ph - 2);
  const tick = Math.max(2, cell / 8);
  ctx.beginPath();
  if (ns) {
    for (const yy of [sy, sy + cell]) {
      ctx.moveTo(px - tick, yy);
      ctx.lineTo(px + pw + tick, yy);
    }
  } else {
    for (const xx of [sx, sx + cell]) {
      ctx.moveTo(xx, py - tick);
      ctx.lineTo(xx, py + ph + tick);
    }
  }
  ctx.stroke();
}

export function paint(ctx: CanvasRenderingContext2D, w: number, h: number, st: PaintState): void {
  const { scene, view: v } = st;
  const cell = CELL * v.scale;
  ctx.clearRect(0, 0, w, h);

  // --- the board plate ---
  const [bx, by] = toScreen(v, 0, 0);
  const bw = scene.width * cell;
  const bh = scene.height * cell;
  ctx.save();
  ctx.fillStyle = "#080b13";
  ctx.fillRect(bx, by, bw, bh);

  if (st.art && st.art.complete && st.art.naturalWidth > 0) {
    ctx.globalAlpha = 1;
    ctx.drawImage(st.art, bx, by, bw, bh);
  }

  // --- tiles ---
  const hasArt = !!(st.art && st.art.complete && st.art.naturalWidth > 0);
  if (st.show.terrain) {
    for (let y = 0; y < scene.height; y++) {
      const row = scene.terrain[y] ?? "";
      for (let x = 0; x < scene.width; x++) {
        const style = tileStyle(row[x] ?? ".");
        const [sx, sy] = toScreen(v, x, y);
        if (hasArt) {
          // Over art, only the things that change the rules get marked.
          if (style.family === "solid" || style.family === "hazard") {
            ctx.globalAlpha = style.family === "hazard" ? 0.4 : 0.55;
            ctx.fillStyle = style.fill;
            ctx.fillRect(sx, sy, cell, cell);
            ctx.globalAlpha = 0.9;
            ctx.strokeStyle = style.edge ?? "#3c4569";
            ctx.lineWidth = 1;
            ctx.strokeRect(sx + 0.5, sy + 0.5, cell - 1, cell - 1);
          } else if (style.family === "rough" || style.family === "water") {
            ctx.globalAlpha = 0.22;
            ctx.fillStyle = style.fill;
            ctx.fillRect(sx, sy, cell, cell);
          }
        } else {
          ctx.globalAlpha = 1;
          ctx.fillStyle = style.fill;
          ctx.fillRect(sx, sy, cell, cell);
          if (style.edge) {
            ctx.strokeStyle = style.edge;
            ctx.lineWidth = 1;
            ctx.strokeRect(sx + 0.5, sy + 0.5, cell - 1, cell - 1);
          }
          if (style.family === "rough") hatch(ctx, sx, sy, cell, "rgba(255,255,255,0.07)");
          if (style.family === "water") hatch(ctx, sx, sy, cell, "rgba(120,200,255,0.10)");
        }
      }
    }
  }
  ctx.globalAlpha = 1;

  // --- objects and wreckage ---
  // The same two passes the Discord board draws, in the same order, from the
  // same server payload. A diffusion battlemap cannot put a pillar on square
  // 6,5 and cannot turn one into rubble, so both are sprites on their own
  // squares — and if this view skipped them, the two boards would disagree
  // about what is standing in the room.
  const labels = new Map<string, string>();
  for (const obj of scene.objects ?? []) {
    const [sx, sy] = toScreen(v, obj.x, obj.y);
    if (obj.label) labels.set(`${obj.x},${obj.y}`, obj.label);
    const img = readySprite(st, obj.image_id);
    const axis = obj.axis ?? "";
    if (axis) {
      panel(ctx, img, sx, sy, cell, axis, tileStyle(obj.code).edge ?? "#a07c3c");
    } else if (img) {
      ctx.drawImage(img, sx, sy, cell, cell);
    }
    // No sprite and no axis: the tile pass already coloured the square.
  }
  for (const deb of scene.debris ?? []) {
    const [sx, sy] = toScreen(v, deb.x, deb.y);
    if (deb.label) labels.set(`${deb.x},${deb.y}`, deb.label);
    // Scuff the square first. Stone rubble on a flagstone floor is the
    // low-contrast case a sprite can lose, and a square that BROKE has to read
    // as changed whether or not the picture carried it.
    ctx.fillStyle = "rgba(38,30,22,0.30)";
    ctx.fillRect(sx, sy, cell, cell);
    const img = readySprite(st, deb.image_id);
    if (img) ctx.drawImage(img, sx, sy, cell, cell);
    else {
      ctx.fillStyle = "rgba(90,78,62,0.62)";
      ctx.fillRect(sx, sy, cell, cell);
    }
    ctx.strokeStyle = "rgba(210,150,90,0.8)";
    ctx.lineWidth = 2;
    ctx.strokeRect(sx + 1, sy + 1, cell - 2, cell - 2);
  }

  // --- movement wash for the selected token (under the effects: where you
  //     could stand matters less than what is standing there) ---
  if (st.reach && st.reach.size) {
    const budget = budgetOf(st.reach);
    for (const [key, cost] of st.reach) {
      const [x, y] = key.split(",").map(Number);
      const [sx, sy] = toScreen(v, x, y);
      // The far edge of your speed fades out, so the shape reads as a reach.
      const t = Math.min(1, cost / Math.max(5, budget));
      ctx.globalAlpha = 0.22 - 0.12 * t;
      ctx.fillStyle = "#4fa3ff";
      ctx.fillRect(sx, sy, cell, cell);
    }
    // A crisp border makes "this is how far I get" readable at a glance.
    ctx.globalAlpha = 0.55;
    ctx.strokeStyle = "#7cc0ff";
    ctx.lineWidth = 1.5;
    outline(ctx, v, [...st.reach.keys()].map((k) => k.split(",").map(Number) as [number, number]), cell);
    ctx.globalAlpha = 1;

    // Threatened ground, hatched over the wash. Only where you could actually
    // GO — hatching the whole board would be noise, and the question this
    // answers is "does the square I am considering cost me an attack".
    if (st.threatened && st.threatened.size) {
      ctx.save();
      ctx.globalAlpha = 0.5;
      ctx.strokeStyle = "#ff7a5c";
      ctx.lineWidth = 1;
      const step = Math.max(4, cell / 4);
      for (const key of st.reach.keys()) {
        if (!st.threatened.has(key)) continue;
        const [x, y] = key.split(",").map(Number);
        const [sx, sy] = toScreen(v, x, y);
        ctx.save();
        ctx.beginPath();
        ctx.rect(sx, sy, cell, cell);
        ctx.clip();
        ctx.beginPath();
        for (let d = -cell; d < cell * 2; d += step) {
          ctx.moveTo(sx + d, sy);
          ctx.lineTo(sx + d + cell, sy + cell);
        }
        ctx.stroke();
        ctx.restore();
      }
      ctx.restore();
    }
  }

  // --- effects (spell areas, zones, auras, light) ---
  if (st.show.effects) {
    for (const eff of scene.effects) {
      const color = eff.color || "#a86bff";
      // An aura or a light source is a glow, not a slab of paint.
      const soft = eff.kind === "aura" || eff.kind === "light";
      // A marker is an annotation — outline it, never paint over the ground.
      const marker = eff.kind === "marker";
      if (!marker) {
        ctx.globalAlpha = soft
          ? Math.min(0.18, Math.max(0.06, (eff.opacity ?? 0.2) * 0.6))
          : Math.min(0.8, Math.max(0.10, eff.opacity ?? 0.35));
        ctx.fillStyle = color;
        for (const [x, y] of eff.squares) {
          const [sx, sy] = toScreen(v, x, y);
          ctx.fillRect(sx, sy, cell, cell);
        }
      }
      // Outline the footprint so overlapping areas stay legible.
      ctx.globalAlpha = soft ? 0.5 : 0.9;
      ctx.strokeStyle = color;
      ctx.lineWidth = soft ? 1 : 1.8;
      if (soft) ctx.setLineDash([5, 4]);
      outline(ctx, v, eff.squares, cell);
      ctx.setLineDash([]);
      if (eff.difficult_terrain) {
        ctx.globalAlpha = 0.4;
        for (const [x, y] of eff.squares) {
          const [sx, sy] = toScreen(v, x, y);
          hatch(ctx, sx, sy, cell, color);
        }
      }
    }
    ctx.globalAlpha = 1;
  }

  // --- grid ---
  if (st.show.grid) {
    ctx.globalAlpha = hasArt ? 0.34 : 0.5;
    ctx.strokeStyle = "#8091a6";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= scene.width; x++) {
      const [sx] = toScreen(v, x, 0);
      ctx.moveTo(Math.round(sx) + 0.5, by);
      ctx.lineTo(Math.round(sx) + 0.5, by + bh);
    }
    for (let y = 0; y <= scene.height; y++) {
      const [, sy] = toScreen(v, 0, y);
      ctx.moveTo(bx, Math.round(sy) + 0.5);
      ctx.lineTo(bx + bw, Math.round(sy) + 0.5);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  }

  // --- stairs ---
  // Drawn over the grid and under the fog: a way off this floor is a feature
  // of the room, and a player deciding whether to climb has to be able to SEE
  // it, and to know which way it goes, before they walk onto it.
  const here = st.stairs ?? [];
  if (here.length) {
    const base = (i: number) => st.levels?.[i]?.base_ft ?? 0;
    for (const conn of here) {
      const [sx, sy] = toScreen(v, conn.x, conn.y);
      const goesUp = base(conn.to) > base(st.level ?? 0);
      ctx.save();
      ctx.strokeStyle = "#7cd6a0";
      ctx.fillStyle = "rgba(52,150,102,0.28)";
      ctx.lineWidth = 2;
      ctx.fillRect(sx, sy, cell, cell);
      ctx.setLineDash([4, 3]);
      ctx.strokeRect(sx + 1, sy + 1, cell - 2, cell - 2);
      ctx.setLineDash([]);
      ctx.fillStyle = "#bff0d4";
      ctx.font = `700 ${Math.max(11, Math.round(cell * 0.5))}px system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(goesUp ? "▲" : "▼", sx + cell / 2, sy + cell / 2);
      ctx.restore();
      if (cell >= 26) {
        chip(ctx, sx + cell / 2, sy + cell - 2,
             st.levels?.[conn.to]?.name ?? `level ${conn.to}`);
      }
    }
  }

  // --- labels ---
  // Over the grid so a gridline never cuts a word in half, under the fog so a
  // square nobody has seen doesn't announce what's on it. One chip per RUN,
  // not per square: two crates side by side are two squares and one fact, and
  // labelling both prints each over the other.
  if (labels.size && cell >= 26) {
    for (const [key, text] of labels) {
      const [x, y] = key.split(",").map(Number);
      if (labels.get(`${x - 1},${y}`) === text) continue;
      if (labels.get(`${x},${y - 1}`) === text) continue;
      const [sx, sy] = toScreen(v, x, y);
      // A chip is wider than the square it names, so two DIFFERENT labels side
      // by side overlap even after the run-dedupe. Stagger: a labelled
      // neighbour to the west pushes this one to the top of its square.
      const top = labels.has(`${x - 1},${y}`);
      chip(ctx, sx + cell / 2, sy + (top ? 14 : cell - 3), text);
    }
  }

  // --- fog of war, in two tiers ---
  // Never seen is black. Seen once but not under anyone's eye right now is a
  // cold veil — you remember the room, you are not watching it. One tier alone
  // gets a door wrong in both directions: with memory only, closing one behind
  // you changes nothing; with sight only, the party forgets the map every time
  // they turn around.
  if (!(st.show.fog && scene.fog) && scene.light) {
    // No fog: light still decides what can be fought, and nothing else on the
    // board shows it. Only in this branch — with fog on, the veil below already
    // carries it, and stacking the two buries the art.
    for (let y = 0; y < scene.height; y++) {
      const lrow = scene.light[y] ?? "";
      for (let x = 0; x < scene.width; x++) {
        const lv = lrow[x];
        if (!lv || lv === "b") continue;
        const [sx, sy] = toScreen(v, x, y);
        ctx.fillStyle = lv === "d" ? "rgba(6,9,20,0.32)" : "rgba(6,9,20,0.66)";
        ctx.fillRect(sx, sy, cell, cell);
      }
    }
  }
  if (st.show.fog && scene.fog) {
    for (let y = 0; y < scene.height; y++) {
      const row = scene.fog[y] ?? "";
      const litRow = scene.sight?.[y];
      for (let x = 0; x < scene.width; x++) {
        const seen = row[x] === "1";
        const lit = litRow?.[x] === "1";
        if (seen && lit) continue;
        const [sx, sy] = toScreen(v, x, y);
        ctx.fillStyle = seen ? "rgba(7,11,22,0.62)" : "rgba(4,6,12,0.88)";
        ctx.fillRect(sx, sy, cell, cell);
      }
    }
  }

  // --- path preview ---
  if (st.path && st.path.length > 1) {
    const legal = st.pathLegal !== false;
    const warn = legal && st.pathProvokes;
    const stroke = !legal ? "#d23843" : warn ? "#f59a3c" : "#ffd479";
    ctx.strokeStyle = stroke;
    ctx.lineWidth = Math.max(2, cell * 0.09);
    ctx.lineJoin = "round";
    ctx.lineCap = "round";
    ctx.setLineDash([cell * 0.32, cell * 0.22]);
    ctx.beginPath();
    st.path.forEach(([x, y], i) => {
      const [sx, sy] = toScreen(v, x, y);
      const cx = sx + cell / 2;
      const cy = sy + cell / 2;
      if (i === 0) ctx.moveTo(cx, cy);
      else ctx.lineTo(cx, cy);
    });
    ctx.stroke();
    ctx.setLineDash([]);
    const [ex, ey] = st.path[st.path.length - 1];
    const [sx, sy] = toScreen(v, ex, ey);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = 2;
    ctx.strokeRect(sx + 1.5, sy + 1.5, cell - 3, cell - 3);
    if (st.pathCost != null) {
      pill(ctx, sx + cell / 2, sy - 8,
        warn ? `${st.pathCost} ft · provokes` : `${st.pathCost} ft`, stroke);
    }
  }

  // --- the armed spell's template, on the cursor ---
  // Drawn above the board's own effects on purpose: this is the thing being
  // decided right now, and it has to be legible over whatever is already
  // burning on the floor. The squares come from the SERVER — clipped by line
  // of effect, so a fireball visibly stops at a wall rather than leaking
  // through it and being silently trimmed after the slot is spent.
  if (st.area && st.area.length) {
    const legal = st.areaLegal !== false;
    const fill = legal ? "rgba(255,166,64,0.30)" : "rgba(255,80,80,0.22)";
    const edge = legal ? "#ffb457" : "#ff6a6a";
    ctx.save();
    ctx.fillStyle = fill;
    for (const [x, y] of st.area) {
      const [sx, sy] = toScreen(v, x, y);
      ctx.fillRect(sx, sy, cell, cell);
    }
    ctx.globalAlpha = 0.95;
    ctx.strokeStyle = edge;
    ctx.lineWidth = 2;
    if (!legal) ctx.setLineDash([6, 4]);
    outline(ctx, v, st.area, cell);
    ctx.setLineDash([]);
    ctx.restore();
  }

  // --- ruler ---
  if (st.measure) {
    const [[ax, ay], [bx2, by2]] = st.measure;
    const [asx, asy] = toScreen(v, ax, ay);
    const [bsx, bsy] = toScreen(v, bx2, by2);
    ctx.strokeStyle = "#24e0b8";
    ctx.lineWidth = 2;
    ctx.setLineDash([6, 5]);
    line(ctx, asx + cell / 2, asy + cell / 2, bsx + cell / 2, bsy + cell / 2);
    ctx.setLineDash([]);
    const dist = Math.max(Math.abs(ax - bx2), Math.abs(ay - by2)) * scene.square_ft;
    pill(ctx, (asx + bsx) / 2 + cell / 2, (asy + bsy) / 2 + cell / 2 - 10,
      `${dist} ft`, "#24e0b8");
  }

  // --- hovered square ---
  if (st.hover) {
    const [hx, hy] = st.hover;
    if (hx >= 0 && hy >= 0 && hx < scene.width && hy < scene.height) {
      const [sx, sy] = toScreen(v, hx, hy);
      ctx.strokeStyle = "rgba(230,188,100,0.75)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(sx + 0.75, sy + 0.75, cell - 1.5, cell - 1.5);
    }
  }

  // --- pings (fade over ~2.5s) ---
  const now = st.now ?? Date.now();
  for (const p of st.pings ?? []) {
    const age = (now - p.at) / 2500;
    if (age > 1) continue;
    const [sx, sy] = toScreen(v, p.x, p.y);
    ctx.globalAlpha = 1 - age;
    ctx.strokeStyle = "#f59a3c";
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(sx + cell / 2, sy + cell / 2, cell * (0.25 + age * 0.7), 0, Math.PI * 2);
    ctx.stroke();
    if (p.label) pill(ctx, sx + cell / 2, sy - 6, p.label, "#f59a3c");
    ctx.globalAlpha = 1;
  }

  // --- board frame ---
  ctx.strokeStyle = "rgba(230,188,100,0.35)";
  ctx.lineWidth = 2;
  ctx.strokeRect(bx - 1, by - 1, bw + 2, bh + 2);
  ctx.restore();
}

function budgetOf(reach: Map<string, number>): number {
  let m = 5;
  for (const c of reach.values()) if (c > m) m = c;
  return m;
}

/** Stroke only the outer boundary of a set of squares. */
function outline(ctx: CanvasRenderingContext2D, v: View,
                 squares: [number, number][], cell: number) {
  const inSet = new Set(squares.map(([x, y]) => `${x},${y}`));
  ctx.beginPath();
  for (const [x, y] of squares) {
    const [sx, sy] = toScreen(v, x, y);
    if (!inSet.has(`${x},${y - 1}`)) { ctx.moveTo(sx, sy); ctx.lineTo(sx + cell, sy); }
    if (!inSet.has(`${x},${y + 1}`)) { ctx.moveTo(sx, sy + cell); ctx.lineTo(sx + cell, sy + cell); }
    if (!inSet.has(`${x - 1},${y}`)) { ctx.moveTo(sx, sy); ctx.lineTo(sx, sy + cell); }
    if (!inSet.has(`${x + 1},${y}`)) { ctx.moveTo(sx + cell, sy); ctx.lineTo(sx + cell, sy + cell); }
  }
  ctx.stroke();
}

function line(ctx: CanvasRenderingContext2D, x1: number, y1: number, x2: number, y2: number) {
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
}

function hatch(ctx: CanvasRenderingContext2D, x: number, y: number, cell: number, color: string) {
  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, cell, cell);
  ctx.clip();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  for (let i = -cell; i < cell; i += 7) {
    ctx.beginPath();
    ctx.moveTo(x + i, y);
    ctx.lineTo(x + i + cell, y + cell);
    ctx.stroke();
  }
  ctx.restore();
}

function pill(ctx: CanvasRenderingContext2D, cx: number, cy: number, text: string, color: string) {
  ctx.save();
  ctx.font = "600 11px ui-monospace, SFMono-Regular, Menlo, monospace";
  const w = ctx.measureText(text).width + 12;
  ctx.fillStyle = "rgba(9,12,21,0.9)";
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  const x = cx - w / 2;
  const y = cy - 9;
  roundRect(ctx, x, y, w, 18, 5);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = color;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, cx, y + 9);
  ctx.restore();
}

/** A small dark chip naming what's on a square. Sibling of pill(), but sized
 *  to sit inside a five-foot square rather than to annotate a route. */
function chip(ctx: CanvasRenderingContext2D, cx: number, baseY: number, text: string) {
  ctx.save();
  ctx.font = "600 10px ui-monospace, SFMono-Regular, Menlo, monospace";
  const w = ctx.measureText(text).width + 8;
  const h = 14;
  const x = cx - w / 2;
  const y = baseY - h;
  ctx.fillStyle = "rgba(9,12,21,0.82)";
  ctx.strokeStyle = "rgba(230,188,100,0.5)";
  ctx.lineWidth = 1;
  roundRect(ctx, x, y, w, h, 3);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#dbe7e8";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, cx, y + h / 2 + 0.5);
  ctx.restore();
}

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/** Walk back down a cost map from a destination to build the route the server
 *  would take. Lets the overlay draw a live path on hover with no round trip —
 *  the authoritative path still comes from the server when the move is made. */
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
