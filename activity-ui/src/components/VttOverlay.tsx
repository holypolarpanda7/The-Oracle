import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { CombatState, VttOptions, VttScene, VttToken } from "../lib/types";
import { SPRITES, loadSprites } from "../lib/boardSprites";
import { useResizable } from "../lib/useResizable";
import {
  CELL, fitView, paint, pathFromCosts, toScreen, toSquare, type View,
} from "../lib/vttPaint";

/** The tactical board.
 *
 *  Appears only while the Oracle has a board out — a fight, a spatial puzzle,
 *  the terrain leg of a chase — and gets out of the way the moment it closes.
 *
 *  The canvas draws the world (art, tiles, grid, spell areas, fog, movement);
 *  tokens are DOM elements on top so they can carry portraits, HP, condition
 *  pips and pointer drag without hand-rolled hit-testing.
 *
 *  Authority stays on the server. The blue movement wash is a *server-costed*
 *  set of squares; the path drawn on hover is derived from those costs, and the
 *  move itself is a request the backend can (and will) refuse — "that's 40 ft
 *  and you have 30". */

const SCENE_LABEL: Record<string, string> = {
  combat: "Battle", puzzle: "Puzzle", chase: "Pursuit",
  hazard: "Peril", explore: "Delve", social: "Standoff",
};

/** "+15 ft", or "ground" for the floor everything else is measured from. */
function f_ft(ft: number): string {
  return ft ? `+${ft} ft` : "ground";
}

function monogram(name: string): string {
  const words = name.trim().split(/\s+/);
  const tail = words[words.length - 1];
  if (words.length > 1 && /^\d+$/.test(tail)) return words[0][0].toUpperCase() + tail;
  if (words.length > 1) return (words[0][0] + tail[0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

/** Cover -> its badge. The value is always "cover from whoever is acting", so a
 *  shield on a token reads as "hard to hit, for the creature whose turn it is". */
const COVER_BADGE: Record<string, string> = {
  half: "½", "three-quarters": "¾", total: "✖",
};

/** Condition -> the pip drawn on a token. Kept short; the carousel spells them out. */
const COND_PIP: Record<string, string> = {
  poisoned: "☣", prone: "⤓", grappled: "✊", restrained: "⛓", stunned: "✷",
  frightened: "❢", charmed: "❤", blinded: "◍", deafened: "◔", paralyzed: "✖",
  petrified: "◆", incapacitated: "✖", invisible: "◌", unconscious: "☾",
  exhaustion: "⌛", raging: "▲", shielded: "⛨",
};

export interface VttProps {
  scene: VttScene;
  /** The live fight, so tokens can carry HP and conditions. */
  combat?: CombatState | null;
  /** The viewer's character, so we know which token is theirs to move. */
  myCharacterId?: number | null;
  options: VttOptions | null;
  /** The server's answer for the square being hovered (cost + who it provokes). */
  preview: { token_id: number; ok: boolean; cost_ft?: number;
             opportunity?: string[] } | null;
  ping: { x: number; y: number; label?: string; at: number } | null;
  error?: string | null;
  onRequestOptions: (tokenId: number, dash: boolean) => void;
  onPreviewPath: (tokenId: number, x: number, y: number) => void;
  onMove: (tokenId: number, x: number, y: number) => void;
  onPing: (x: number, y: number) => void;
  /** Use the connector under my token — the server checks I'm on one. */
  onTakeStairs: () => void;
  onDismissError: () => void;
}

export function VttOverlay(p: VttProps) {
  const { scene } = p;
  const wrapRef = useRef<HTMLDivElement | null>(null);
  // How the board and the chat share the column is a preference, not something
  // one default can get right: on a short window a big board buries the
  // narration, on a tall one a small board wastes it. So the player drags the
  // split and it persists. The canvas already repaints on resize (see the
  // ResizeObserver below), so this is honest resizing, not a stretched bitmap.
  const boardR = useResizable("vtt-board", { minH: 220, axis: "y" });
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const artRef = useRef<HTMLImageElement | null>(null);
  const [view, setView] = useState<View | null>(null);
  const [size, setSize] = useState<[number, number]>([0, 0]);
  const [selected, setSelected] = useState<number | null>(null);
  const [hover, setHover] = useState<[number, number] | null>(null);
  const [dash, setDash] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [measuring, setMeasuring] = useState(false);
  const [measure, setMeasure] = useState<[[number, number], [number, number]] | null>(null);
  const [pings, setPings] = useState<{ x: number; y: number; label?: string; at: number }[]>([]);
  const [show, setShow] = useState({ grid: true, terrain: true, effects: true, fog: true });
  const [drag, setDrag] = useState<{ kind: "pan"; x: number; y: number; ox: number; oy: number }
    | { kind: "token"; id: number } | null>(null);

  // Which floor you are STANDING on. Not the same question as which floor is
  // being drawn — you need to be able to look upstairs before you decide to
  // climb, and looking is not moving.
  const myLevel = useMemo(() => {
    const mine = scene.tokens.find((t) => p.myCharacterId != null
      && t.character_id === p.myCharacterId);
    return Math.max(0, Math.min(mine?.level ?? 0,
                                Math.max(0, (scene.levels?.length ?? 1) - 1)));
  }, [scene.tokens, scene.levels, p.myCharacterId]);

  // Which floor is being DRAWN. Null means "wherever I am", which is the right
  // default and the one it snaps back to the moment you actually change floor.
  const [peek, setPeek] = useState<number | null>(null);
  const level = peek ?? myLevel;
  useEffect(() => { setPeek(null); }, [myLevel]);

  const floors = scene.levels ?? [];
  /** Connectors leaving the floor currently drawn. */
  const stairsHere = floors[level]?.stairs ?? [];
  /** The connector my own token is standing on, if any — the way up or down. */
  const standingOn = useMemo(() => {
    const mine = scene.tokens.find((t) => p.myCharacterId != null
      && t.character_id === p.myCharacterId);
    if (!mine || (mine.level ?? 0) !== myLevel) return null;
    return (floors[myLevel]?.stairs ?? []).find(
      (st) => st.x === mine.x && st.y === mine.y) ?? null;
  }, [scene.tokens, floors, myLevel, p.myCharacterId]);

  // The scene as it looks from that floor: its terrain, its creatures. Drawing
  // a gallery over the hall it overlooks is unreadable, and drawing everyone
  // on one grid puts the archer upstairs in the middle of the melee.
  const floor = useMemo(() => {
    if (!level || !scene.levels?.[level]) return scene;
    return {
      ...scene,
      terrain: scene.levels[level].terrain ?? scene.terrain,
      // Everything here is a fact about a STOREY, not about the board: its
      // memory, what is under someone's eye on it, and how lit it is. Drawing
      // the hall's fog and the hall's fireball on the gallery above it was the
      // first thing peeking at another floor made obvious.
      fog: scene.levels[level].fog ?? null,
      sight: scene.levels[level].sight ?? null,
      light: scene.levels[level].light ?? null,
      objects: [], debris: [],
      effects: scene.effects.filter((e) => (e.level ?? 0) === level),
    };
  }, [scene, level]);

  const onThisFloor = useCallback(
    (t: VttToken) => (t.level ?? 0) === level, [level]);

  const myToken = useMemo(
    () => scene.tokens.find((t) => p.myCharacterId != null
      && t.character_id === p.myCharacterId) ?? null,
    [scene.tokens, p.myCharacterId],
  );
  const selectedToken = useMemo(
    () => scene.tokens.find((t) => t.id === selected) ?? null,
    [scene.tokens, selected],
  );
  const isMine = (t: VttToken) => p.myCharacterId != null && t.character_id === p.myCharacterId;
  const myTurn = !scene.current_token_id || (myToken && scene.current_token_id === myToken.id);

  /** Server-costed reachable squares, as a lookup. */
  const reach = useMemo(() => {
    if (!p.options || !selectedToken || p.options.token_id !== selectedToken.id) return null;
    const m = new Map<string, number>();
    for (const s of p.options.squares) m.set(`${s.x},${s.y}`, s.cost);
    return m;
  }, [p.options, selectedToken]);

  // ---- sizing -------------------------------------------------------------
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      setSize([el.clientWidth, el.clientHeight]);
    });
    ro.observe(el);
    setSize([el.clientWidth, el.clientHeight]);
    return () => ro.disconnect();
  }, [collapsed]);

  // Fit the board when it changes identity or the viewport resizes.
  const fitKey = `${scene.id}:${size[0]}x${size[1]}`;
  useEffect(() => {
    if (size[0] > 0 && size[1] > 0) setView(fitView(scene, size[0], size[1]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey]);

  // ---- battlemap art ------------------------------------------------------
  useEffect(() => {
    if (!scene.background_image_id) {
      artRef.current = null;
      return;
    }
    const img = new Image();
    img.src = `/imagery/image/${scene.background_image_id}`;
    img.onload = () => { artRef.current = img; };
    artRef.current = img.complete ? img : artRef.current;
  }, [scene.background_image_id]);

  // ---- object & wreckage sprites ------------------------------------------
  // Shared by kind and cached across boards, so this is usually a no-op: the
  // second room with pillars in it already has the pillar. Each arrival bumps
  // a counter rather than state the draw depends on structurally — the picture
  // fills in when it lands, and the board is correct before it does.
  const [spriteTick, bumpSprites] = useReducer((n: number) => n + 1, 0);
  useEffect(() => {
    const ids: number[] = [];
    for (const o of scene.objects ?? []) if (o.image_id) ids.push(o.image_id);
    for (const d of scene.debris ?? []) if (d.image_id) ids.push(d.image_id);
    loadSprites(ids, bumpSprites);
  }, [scene.objects, scene.debris]);

  // ---- incoming pings -----------------------------------------------------
  useEffect(() => {
    if (p.ping) setPings((ps) => [...ps.filter((q) => Date.now() - q.at < 2500), p.ping!]);
  }, [p.ping]);

  // ---- selection ----------------------------------------------------------
  useEffect(() => {
    // Auto-select your own token when the board opens or your turn comes round.
    if (selected == null && myToken) setSelected(myToken.id);
  }, [myToken, selected]);

  useEffect(() => {
    if (selectedToken && isMine(selectedToken)) p.onRequestOptions(selectedToken.id, dash);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedToken?.id, dash, scene.revision]);

  // ---- path preview -------------------------------------------------------
  // The blue wash and the drawn route come from the cost map (instant, local).
  // Whether the route provokes an opportunity attack is a question only the
  // server can answer, so it's asked on a short debounce once the pointer
  // settles — a warning before you move beats an apology after.
  useEffect(() => {
    if (!hover || !selectedToken || !isMine(selectedToken)) return;
    if (!reach?.has(`${hover[0]},${hover[1]}`)) return;
    const [hx, hy] = hover;
    const id = window.setTimeout(
      () => p.onPreviewPath(selectedToken.id, hx, hy), 180);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hover?.[0], hover?.[1], selectedToken?.id, reach]);

  const provokes = (p.preview && selectedToken
    && p.preview.token_id === selectedToken.id
    && hover && p.preview.ok
    && (p.preview.opportunity?.length ?? 0) > 0)
    ? p.preview.opportunity ?? []
    : [];

  const path = useMemo(() => {
    if (!reach || !selectedToken || !hover) return null;
    return pathFromCosts(reach, [selectedToken.x, selectedToken.y], hover);
  }, [reach, selectedToken, hover]);
  const pathCost = hover && reach ? reach.get(`${hover[0]},${hover[1]}`) : undefined;

  // ---- draw ---------------------------------------------------------------
  const draw = useCallback(() => {
    const c = canvasRef.current;
    if (!c || !view || size[0] === 0) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    if (c.width !== size[0] * dpr || c.height !== size[1] * dpr) {
      c.width = size[0] * dpr;
      c.height = size[1] * dpr;
      c.style.width = `${size[0]}px`;
      c.style.height = `${size[1]}px`;
    }
    const ctx = c.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    paint(ctx, size[0], size[1], {
      scene: { ...floor, tokens: floor.tokens.filter(onThisFloor) },
      stairs: stairsHere, levels: floors, level,
      view, art: artRef.current,
      // A movement wash is a plan for a creature standing on YOUR floor; drawn
      // over a storey you are only looking at, it is an invitation to a move
      // that cannot happen.
      reach: level === myLevel ? reach : null,
      path: level === myLevel ? path : null,
      pathCost,
      pathLegal: pathCost !== undefined, pathProvokes: provokes.length > 0,
      hover, measure, show, pings, sprites: SPRITES, now: Date.now(),
    });
  }, [floor, onThisFloor, stairsHere, floors, level, myLevel, view, size, reach,
      path, pathCost, hover, measure, show, pings, provokes.length, spriteTick]);

  useEffect(() => {
    draw();
  }, [draw]);

  // Keep animating while pings are alive (and once more when art lands).
  useEffect(() => {
    if (!pings.length) return;
    let raf = 0;
    const tick = () => {
      draw();
      if (pings.some((q) => Date.now() - q.at < 2500)) raf = requestAnimationFrame(tick);
      else setPings((ps) => ps.filter((q) => Date.now() - q.at < 2500));
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [pings, draw]);

  useEffect(() => {
    const img = artRef.current;
    if (!img || img.complete) return;
    const on = () => draw();
    img.addEventListener("load", on);
    return () => img.removeEventListener("load", on);
  }, [scene.background_image_id, draw]);

  // ---- pointer ------------------------------------------------------------
  const squareAt = (e: React.PointerEvent | React.MouseEvent): [number, number] | null => {
    const c = canvasRef.current;
    if (!c || !view) return null;
    const r = c.getBoundingClientRect();
    return toSquare(view, e.clientX - r.left, e.clientY - r.top);
  };

  const onPointerDown = (e: React.PointerEvent) => {
    const sq = squareAt(e);
    if (!sq || !view) return;
    if (e.button === 2) return;                    // right-click: ping (onContextMenu)
    if (measuring) {
      setMeasure([sq, sq]);
      return;
    }
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setDrag({ kind: "pan", x: e.clientX, y: e.clientY, ox: view.ox, oy: view.oy });
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const sq = squareAt(e);
    if (sq) setHover(sq);
    if (measuring && measure) {
      if (sq) setMeasure([measure[0], sq]);
      return;
    }
    if (drag?.kind === "pan" && view) {
      setView({ ...view, ox: drag.ox + (e.clientX - drag.x), oy: drag.oy + (e.clientY - drag.y) });
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const sq = squareAt(e);
    const wasPan = drag?.kind === "pan";
    const moved = wasPan && drag.kind === "pan"
      && (Math.abs(e.clientX - drag.x) > 4 || Math.abs(e.clientY - drag.y) > 4);
    setDrag(null);
    if (measuring) return;
    if (!sq || moved) return;
    // A click on a reachable square with your own token selected = a move.
    if (selectedToken && isMine(selectedToken) && reach?.has(`${sq[0]},${sq[1]}`)) {
      const occupied = scene.tokens.some(
        (t) => t.id !== selectedToken.id && !t.defeated
          && sq[0] >= t.x && sq[0] < t.x + t.squares
          && sq[1] >= t.y && sq[1] < t.y + t.squares);
      if (!occupied) p.onMove(selectedToken.id, sq[0], sq[1]);
    }
  };

  const onWheel = (e: React.WheelEvent) => {
    if (!view) return;
    const c = canvasRef.current;
    if (!c) return;
    const r = c.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const my = e.clientY - r.top;
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const scale = Math.max(0.2, Math.min(3, view.scale * factor));
    // Zoom about the cursor so the square under it stays put.
    const k = scale / view.scale;
    setView({ scale, ox: mx - (mx - view.ox) * k, oy: my - (my - view.oy) * k });
  };

  const onContextMenu = (e: React.MouseEvent) => {
    e.preventDefault();
    const sq = squareAt(e);
    if (sq) p.onPing(sq[0], sq[1]);
  };

  /** Tokens borrow their HP and conditions from the initiative tracker. */
  const vitals = useMemo(() => {
    const m = new Map<number, { hp: number; max: number; temp: number;
                                conds: string[]; cover?: string }>();
    for (const c of p.combat?.combatants ?? []) {
      m.set(c.id, {
        hp: c.current_hp, max: Math.max(1, c.max_hp), temp: c.temp_hp || 0,
        conds: c.conditions || [],
        // The server keeps this pinned to the acting creature's line of attack,
        // so it always means "cover from the one who can hit you right now".
        cover: c.cover && c.cover !== "none" ? c.cover : undefined,
      });
    }
    return m;
  }, [p.combat]);

  // ---- token layer --------------------------------------------------------
  // A creature standing in the dark is not on the board. Drawing a foe in a
  // room nobody can see would give away more than any amount of fog hides —
  // the party's own tokens are exempt, since they are what sight is measured
  // FROM. Same rule as the Discord board, so the two views agree.
  const inSight = useCallback((t: VttToken) => {
    if (!show.fog || !scene.sight || t.team === "party") return true;
    for (let yy = t.y; yy < t.y + t.squares; yy++) {
      for (let xx = t.x; xx < t.x + t.squares; xx++) {
        if (scene.sight[yy]?.[xx] === "1") return true;
      }
    }
    return false;
  }, [show.fog, scene.sight]);

  const tokenNodes = view && scene.tokens.filter(onThisFloor)
    .filter(inSight).map((t) => {
    const cell = CELL * view.scale;
    const [sx, sy] = toScreen(view, t.x, t.y);
    const px = cell * t.squares;
    const active = scene.current_token_id === t.id;
    const vit = t.combatant_id != null ? vitals.get(t.combatant_id) : undefined;
    const hpPct = vit ? Math.max(0, Math.min(100, (100 * vit.hp) / vit.max)) : null;
    const pips = (vit?.conds ?? [])
      .map((c) => COND_PIP[c.toLowerCase()])
      .filter(Boolean)
      .slice(0, 3);
    const cls = [
      "vtt-token", `team-${t.team}`, `kind-${t.kind}`,
      active ? "active" : "", t.defeated ? "down" : "",
      t.prone ? "prone" : "", t.hidden ? "ghost" : "",
      selected === t.id ? "selected" : "", isMine(t) ? "mine" : "",
    ].filter(Boolean).join(" ");
    return (
      <div
        key={t.id}
        className={cls}
        style={{
          left: sx, top: sy, width: px, height: px,
          ["--tok" as string]: t.color || undefined,
        }}
        title={`${t.name}` +
          (vit ? ` — ${vit.hp}/${vit.max} HP${vit.temp ? ` (+${vit.temp} temp)` : ""}` : "") +
          (t.elevation_ft ? ` · ${t.elevation_ft} ft up` : "") +
          ` · ${t.moved_ft}/${t.speed_ft} ft moved`}
        onPointerDown={(e) => { e.stopPropagation(); setSelected(t.id); }}
      >
        <div className="vtt-disc">
          {t.image_id
            ? <img src={`/imagery/image/${t.image_id}?thumb=true`} alt="" draggable={false} />
            : <span className="vtt-mono">{t.defeated ? "☠" : monogram(t.name)}</span>}
          {hpPct != null && !t.defeated && (
            <span className={`vtt-hp${hpPct <= 25 ? " dire" : hpPct <= 60 ? " hurt" : ""}`}
              style={{ height: `${hpPct}%` }} />
          )}
        </div>
        {vit?.cover && !active && (
          <span className={`vtt-cover c-${vit.cover.replace(/\W/g, "")}`}
            title={`${vit.cover} cover from the creature whose turn it is`}>
            {COVER_BADGE[vit.cover] ?? "●"}
          </span>
        )}
        {pips.length > 0 && (
          <span className="vtt-pips">{pips.map((c, i) => <i key={i}>{c}</i>)}</span>
        )}
        {t.squares === 1 && <span className="vtt-name">{t.name}</span>}
        {t.moved_ft > 0 && !t.defeated && (
          <span className="vtt-move" style={{
            width: `${Math.min(100, (100 * t.moved_ft) / Math.max(1, t.speed_ft))}%`,
          }} />
        )}
      </div>
    );
  });

  if (collapsed) {
    return (
      <div className="vtt collapsed">
        <button className="vtt-reopen" onClick={() => setCollapsed(false)}>
          ⌗ {scene.name} — open the board
        </button>
      </div>
    );
  }

  const selectedCover = selectedToken?.combatant_id != null
    ? vitals.get(selectedToken.combatant_id)?.cover : undefined;

  const remaining = selectedToken
    ? Math.max(0, selectedToken.speed_ft * (dash ? 2 : 1) - selectedToken.moved_ft)
    : 0;

  return (
    <div className="vtt">
      <header className="vtt-bar">
        <span className="vtt-kind">{SCENE_LABEL[scene.kind] ?? scene.kind}</span>
        <span className="vtt-title">{scene.name}</span>
        {scene.encounter_id ? <span className="vtt-round">Round {scene.round}</span> : null}
        <span className="vtt-spacer" />
        {scene.art_status === "pending" && <span className="vtt-note">painting the map…</span>}
        <div className="vtt-tools">
          <button className={show.grid ? "on" : ""} title="Grid"
            onClick={() => setShow((s) => ({ ...s, grid: !s.grid }))}>⌗</button>
          <button className={show.effects ? "on" : ""} title="Effects"
            onClick={() => setShow((s) => ({ ...s, effects: !s.effects }))}>✳</button>
          <button className={show.terrain ? "on" : ""} title="Terrain"
            onClick={() => setShow((s) => ({ ...s, terrain: !s.terrain }))}>▦</button>
          {scene.fog && (
            <button className={show.fog ? "on" : ""} title="Fog of war"
              onClick={() => setShow((s) => ({ ...s, fog: !s.fog }))}>☁</button>
          )}
          <button className={measuring ? "on" : ""} title="Measure distance"
            onClick={() => { setMeasuring((m) => !m); setMeasure(null); }}>📏</button>
          <button title="Fit the board"
            onClick={() => size[0] && setView(fitView(scene, size[0], size[1]))}>⤢</button>
          <button title="Minimise" onClick={() => setCollapsed(true)}>—</button>
        </div>
      </header>

      {floors.length > 1 && (
        <div className="vtt-floors">
          {/* Top floor first: a building reads upward, and a list that puts the
              cellar above the roof takes a moment to parse every single time. */}
          {floors.map((f, i) => i).reverse().map((i) => (
            <button
              key={i}
              className={[i === level ? "on" : "",
                          i === myLevel ? "here" : ""].filter(Boolean).join(" ")}
              title={i === myLevel ? `You are on ${floors[i].name}`
                                   : `Look at ${floors[i].name}`}
              onClick={() => setPeek(i === myLevel ? null : i)}
            >
              {floors[i].name}
              <em>{f_ft(floors[i].base_ft)}</em>
            </button>
          ))}
          {peek != null && peek !== myLevel && (
            <span className="vtt-peeking">
              looking at {floors[peek].name} — you are on {floors[myLevel].name}
              <button onClick={() => setPeek(null)}>back</button>
            </span>
          )}
          {standingOn && (
            <button
              className="vtt-take-stairs"
              disabled={!myTurn}
              title={myTurn ? "" : "Not your turn"}
              onClick={() => p.onTakeStairs()}
            >
              {standingOn.to > myLevel ? "▲" : "▼"} take the{" "}
              {standingOn.kind ?? "stairs"} to{" "}
              {floors[standingOn.to]?.name ?? `level ${standingOn.to}`}
            </button>
          )}
        </div>
      )}

      <div
        className="vtt-board"
        ref={(el) => { wrapRef.current = el; boardR.ref.current = el; }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { setHover(null); setDrag(null); }}
        onWheel={onWheel}
        onContextMenu={onContextMenu}
      >
        <canvas ref={canvasRef} />
        <div className="vtt-tokens">{tokenNodes}</div>
        {p.error && (
          <div className="vtt-error" onClick={p.onDismissError}>{p.error}</div>
        )}
      </div>

      <div
        className="vtt-grip"
        title="Drag to give the board more room, or give it back to the chat"
        onPointerDown={boardR.onGripDown}
      />

      <footer className="vtt-foot">
        {selectedToken ? (
          <>
            <span className="vtt-sel">{selectedToken.name}</span>
            {isMine(selectedToken) ? (
              <>
                <span className="vtt-budget">
                  {remaining} ft left of {selectedToken.speed_ft * (dash ? 2 : 1)}
                </span>
                <button className={`vtt-dash${dash ? " on" : ""}`}
                  onClick={() => setDash((d) => !d)}
                  title="Preview movement as though you Dash">Dash</button>
                {provokes.length > 0 ? (
                  <span className="vtt-warn">
                    ⚠ that route leaves {provokes.join(", ")} — it provokes
                  </span>
                ) : (
                  <span className="vtt-hint">
                    {myTurn
                      ? "click a lit square to move · right-click to ping · scroll to zoom"
                      : "waiting for your turn"}
                  </span>
                )}
              </>
            ) : (
              <span className="vtt-hint">
                {selectedToken.team === "party" ? "an ally" : "a foe"} ·
                {" "}{selectedToken.size} · speed {selectedToken.speed_ft} ft
                {selectedToken.reach_ft > 5 ? ` · reach ${selectedToken.reach_ft} ft` : ""}
                {selectedCover ? ` · ${selectedCover} cover from the creature acting` : ""}
              </span>
            )}
          </>
        ) : (
          <span className="vtt-hint">{scene.description || "select a token"}</span>
        )}
        {measure && (
          <span className="vtt-measure">
            {Math.max(Math.abs(measure[0][0] - measure[1][0]),
              Math.abs(measure[0][1] - measure[1][1])) * scene.square_ft} ft
          </span>
        )}
      </footer>

      {/* What the coloured patches mean. Without this the board is pretty and
          unreadable — a player shouldn't have to ask what the orange is. */}
      {scene.effects.length > 0 && (
        <div className="vtt-legend">
          {scene.effects.map((e) => (
            <span className="vtt-leg" key={e.id}
              title={[
                e.damage ? `${e.damage}` : null,
                e.save_dc ? `${(e.save_ability || "").toUpperCase()} DC ${e.save_dc}` : null,
                e.difficult_terrain ? "difficult terrain" : null,
                e.blocks_sight ? "blocks sight" : null,
                e.concentration ? "concentration" : null,
              ].filter(Boolean).join(" · ") || e.kind}>
              <i style={{ background: e.color || "#a86bff" }} />
              {e.name}
              {e.difficult_terrain && <b>⤓</b>}
              {e.damage && <b>✸</b>}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
