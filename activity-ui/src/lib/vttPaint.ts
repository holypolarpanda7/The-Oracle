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
  x: { fill: "#05070d", edge: "#2a2f45", family: "hazard" },
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
  hover?: [number, number] | null;
  /** Measurement in progress: [from, to]. */
  measure?: [[number, number], [number, number]] | null;
  show: { grid: boolean; terrain: boolean; effects: boolean; fog: boolean };
  /** Transient ping markers with their spawn time. */
  pings?: { x: number; y: number; label?: string; at: number }[];
  now?: number;
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
  }

  // --- effects (spell areas, zones, auras, light) ---
  if (st.show.effects) {
    for (const eff of scene.effects) {
      const color = eff.color || "#a86bff";
      // An aura or a light source is a glow, not a slab of paint.
      const soft = eff.kind === "aura" || eff.kind === "light";
      ctx.globalAlpha = soft
        ? Math.min(0.18, Math.max(0.06, (eff.opacity ?? 0.2) * 0.6))
        : Math.min(0.8, Math.max(0.10, eff.opacity ?? 0.35));
      ctx.fillStyle = color;
      for (const [x, y] of eff.squares) {
        const [sx, sy] = toScreen(v, x, y);
        ctx.fillRect(sx, sy, cell, cell);
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

  // --- fog of war ---
  if (st.show.fog && scene.fog) {
    ctx.fillStyle = "rgba(4,6,12,0.86)";
    for (let y = 0; y < scene.height; y++) {
      const row = scene.fog[y] ?? "";
      for (let x = 0; x < scene.width; x++) {
        if (row[x] !== "1") {
          const [sx, sy] = toScreen(v, x, y);
          ctx.fillRect(sx, sy, cell, cell);
        }
      }
    }
  }

  // --- path preview ---
  if (st.path && st.path.length > 1) {
    const legal = st.pathLegal !== false;
    ctx.strokeStyle = legal ? "#ffd479" : "#d23843";
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
    ctx.strokeStyle = legal ? "#ffd479" : "#d23843";
    ctx.lineWidth = 2;
    ctx.strokeRect(sx + 1.5, sy + 1.5, cell - 3, cell - 3);
    if (st.pathCost != null) {
      pill(ctx, sx + cell / 2, sy - 8, `${st.pathCost} ft`,
        legal ? "#ffd479" : "#d23843");
    }
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
