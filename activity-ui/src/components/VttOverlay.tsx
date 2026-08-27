import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { ReactNode } from "react";
import type {
  BarAction, CombatState, CoverPreview, VttArea, VttOptions, VttScene,
  VttTarget, VttToken,
} from "../lib/types";
import { SPRITES, loadSprites } from "../lib/boardSprites";
import { useResizable } from "../lib/useResizable";
import { CELL, pathFromCosts, type BoardView, type View } from "../lib/boardView";
import { YAW_DEG, YAW_STEP_DEG, project, wrapYaw } from "../lib/isocam";
import { createCanvasBoardView } from "../lib/canvasBoardView";
import { createIsoBoardView } from "../lib/vttScene3d";

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
  /** The server's answer for the square being hovered: the real route it
   *  would walk, what it costs, and who it provokes. */
  preview: { token_id: number; ok: boolean; cost_ft?: number;
             path?: [number, number][]; opportunity?: string[];
             x?: number; y?: number; cover?: CoverPreview } | null;
  /** The act armed on the action bar, waiting for the board to be aimed. */
  armed?: BarAction | null;
  /** Who that act may legally hit, with reasons for the rest. */
  targets?: { action_id?: string; targets: VttTarget[] } | null;
  /** Where the armed template would land, for the square under the cursor. */
  area?: VttArea | null;
  ping: { x: number; y: number; label?: string; at: number } | null;
  error?: string | null;
  onRequestOptions: (tokenId: number, dash: boolean) => void;
  onPreviewPath: (tokenId: number, x: number, y: number) => void;
  onMove: (tokenId: number, x: number, y: number) => void;
  /** Ask the server what the armed act could hit from here. */
  onRequestTargets: (a: BarAction, tokenId: number) => void;
  /** Ask where the armed template would land if dropped on this square. */
  onPreviewArea: (a: BarAction, tokenId: number, x: number, y: number) => void;
  /** Aim the armed act and take it. */
  onTakeAimed: (a: BarAction, aim: { targetTokenId?: number;
                                     x?: number; y?: number }) => void;
  onPing: (x: number, y: number) => void;
  /** Use the connector under my token — the server checks I'm on one. */
  onTakeStairs: () => void;
  onDismissError: () => void;
  /** Fill the space given rather than owning a height of its own.
   *
   *  On the play surface the board shares a scrolling column with the
   *  narration, so how they split it is a preference the player drags. On the
   *  battle page the board IS the page — there is nothing to trade height
   *  with, so the stored split (and the grip that sets it) would only make it
   *  smaller than its cell. */
  fill?: boolean;
  /** The action bar, rendered INSIDE the board panel. It belongs here rather
   *  than as a sibling in the stage: the stage scrolls, the board is tall, and
   *  a bar below the fold is a bar nobody uses. The two are also in
   *  conversation — you pick on one and the other answers. */
  children?: ReactNode;
}

/** How much cover, in a word narrow enough for a status line. */
const COVER_WORD: Record<string, string> = {
  half: "half", "three-quarters": "¾", total: "total",
};

/** What the hovered square would be worth, as one short phrase.
 *
 *  Silent when it would be noise: no cover from anybody is the ordinary case
 *  and printing "no cover" on every square of the board teaches a player to
 *  stop reading the line. Names the foes while there are few enough to name,
 *  and counts them after that — "¾ from 3 of 5" is the fact a player needs to
 *  decide, and five names is a paragraph. */
function coverPhrase(c?: CoverPreview | null): string {
  if (!c || !c.from?.length || c.best === "none") return "";
  const helped = c.from.filter((f) => f.cover !== "none");
  const word = COVER_WORD[c.best] ?? c.best;
  if (helped.length <= 2) {
    return `${word} cover from ${helped.map((f) => f.name).join(" and ")}`;
  }
  return `${word} cover from ${helped.length} of ${c.from.length}`;
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
  // The canvas is held as STATE rather than a ref because the renderer is built
  // from it: a ref never notifies, so the board would be constructed against
  // whatever the element happened to be on first render (null).
  const [canvasEl, setCanvasEl] = useState<HTMLCanvasElement | null>(null);
  const artRef = useRef<HTMLImageElement | null>(null);

  // Which renderer is drawing. Every other line in this component goes through
  // the `BoardView` interface, so this is the only place that decides.
  //
  // The flat board is kept as a fallback while the isometric one is brought to
  // parity, and retires with it. Note the `key` on the <canvas> below: a canvas
  // element can only ever hand out ONE kind of context, so a 2D canvas can
  // never become a WebGL one — switching has to remount the element.
  const [mode, setMode] = useState<"iso" | "flat">("iso");
  const [board, setBoard] = useState<BoardView | null>(null);
  useEffect(() => {
    // `data-mode` is the guard, and it is not belt-and-braces. Changing `mode`
    // re-renders before the ref callback hands over the replacement element, so
    // for one render the new mode is paired with the OLD canvas — and building
    // a WebGL renderer on a canvas that already returned a 2D context throws,
    // which took the whole board down. Waiting until the element agrees costs
    // one frame and removes the window entirely.
    if (!canvasEl || canvasEl.dataset.mode !== mode) return;
    let made: BoardView;
    try {
      made = mode === "iso" ? createIsoBoardView(canvasEl)
                            : createCanvasBoardView(canvasEl);
    } catch (e) {
      // No usable WebGL — an old phone, a locked-down webview. This is exactly
      // what the flat board is being kept for, so fall back to it rather than
      // showing an empty panel.
      console.warn("[vtt] isometric board unavailable; using the flat board", e);
      setMode("flat");
      return;
    }
    setBoard(made);
    // Cleared before disposal so nothing can draw into a released context.
    return () => { setBoard(null); made.dispose(); };
  }, [canvasEl, mode]);

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
  const [drag, setDrag] = useState<{ kind: "pan"; x: number; y: number; from: View }
    | { kind: "turn"; x: number; from: number }
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

  /** Squares where leaving provokes — server-computed, same rule as the
   *  opportunity check the move itself runs. */
  const threatened = useMemo(() => {
    if (!p.options?.threatened || !selectedToken
        || p.options.token_id !== selectedToken.id) return null;
    return new Set(p.options.threatened.map((s) => `${s.x},${s.y}`));
  }, [p.options, selectedToken]);

  // ---- targeting ----------------------------------------------------------
  // An armed act takes the board over: the movement wash goes away (you are
  // choosing a target, not a destination) and the tokens become the things
  // you click. The legality is entirely the SERVER's — this only draws it.
  //
  // Deliberately NOT re-gated on whose turn it is. The action bar already
  // refuses to arm an act the economy can't pay for, and it decides that from
  // the engine's own turn order; asking `scene.current_token_id` here as well
  // would be a second answer to the same question, and the two drift. Seeing
  // what a spell WOULD reach is also how a player decides whether to walk
  // first, which is a thing worth being able to do while waiting.
  const aiming = !!p.armed;
  const aimingArea = aiming && p.armed!.targeting === "area";

  const targetById = useMemo(() => {
    const m = new Map<number, VttTarget>();
    if (aiming && p.targets && (!p.targets.action_id
        || p.targets.action_id === p.armed!.id)) {
      for (const t of p.targets.targets) m.set(t.token_id, t);
    }
    return m;
  }, [aiming, p.targets, p.armed]);

  // Ask the server who this act can hit, whenever the act or the board changes.
  useEffect(() => {
    if (!p.armed || !myToken || p.armed.targeting !== "creature") return;
    p.onRequestTargets(p.armed, myToken.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.armed?.id, myToken?.id, scene.revision]);

  // …and where the template would land, for the square under the cursor. A
  // template is clipped by line of effect, which only the server can work out,
  // so this is debounced the same way the path preview is.
  useEffect(() => {
    if (!aimingArea || !myToken || !hover) return;
    const [hx, hy] = hover;
    const id = window.setTimeout(
      () => p.onPreviewArea(p.armed!, myToken.id, hx, hy), 90);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [aimingArea, hover?.[0], hover?.[1], myToken?.id, p.armed?.id]);

  const areaSquares = useMemo(() => {
    if (!aimingArea || !p.area) return null;
    if (p.area.action_id && p.area.action_id !== p.armed!.id) return null;
    return p.area.squares;
  }, [aimingArea, p.area, p.armed]);

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
    if (board && size[0] > 0 && size[1] > 0) setView(board.fit(scene, size[0], size[1]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitKey, board]);

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

  // ---- walking ------------------------------------------------------------
  // A creature is drawn moving along the route the SERVER walked, not along the
  // straight line between where it was and where it ended up. The two differ
  // exactly where it matters — going through a door instead of across the
  // corner — and a straight lerp puts a creature inside a wall for half a
  // second. Harmless when walls were flat shading; obvious now they stand up.
  //
  // Keyed on the move event's id rather than on the token's position, so a
  // creature that walks back to where it started still animates, and a board
  // re-render never replays a walk that already happened.
  const [walk, setWalk] = useState<{
    id: number; tokenId: number; path: [number, number][]; at: number } | null>(null);
  const [walkPos, setWalkPos] = useState<[number, number] | null>(null);
  const lastWalkId = useRef<number>(0);

  useEffect(() => {
    const m = scene.last_move;
    if (!m || m.id === lastWalkId.current) return;
    lastWalkId.current = m.id;
    // Don't animate the first frame after a board opens — there is no "before"
    // to walk from, and a creature would slide in from a square it never
    // occupied.
    if (walkPos === null && !walk && scene.revision <= 1) return;
    setWalk({ id: m.id, tokenId: m.token_id, path: m.path, at: Date.now() });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scene.last_move?.id]);

  useEffect(() => {
    if (!walk) return;
    const steps = walk.path.length - 1;
    // ~110ms a square reads as walking rather than teleporting, capped so a
    // dash across a big board doesn't hold up the turn.
    const dur = Math.min(700, Math.max(160, steps * 110));
    let raf = 0;
    const tick = () => {
      const t = Math.min(1, (Date.now() - walk.at) / dur);
      const f = t * steps;
      const i = Math.min(steps - 1, Math.floor(f));
      const k = f - i;
      const [ax, ay] = walk.path[i];
      const [bx, by] = walk.path[i + 1];
      setWalkPos([ax + (bx - ax) * k, ay + (by - ay) * k]);
      if (t < 1) raf = requestAnimationFrame(tick);
      else { setWalk(null); setWalkPos(null); }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [walk]);

  /** Where the painted layer sits this frame, if there is one. */

  /** Turn the camera. Which point it pivots about, and what that costs the
   *  rest of the view, is the RENDERER's business — see `BoardView.turnTo`. */
  const turn = useCallback((byDeg: number) => {
    if (!board || !view || !size[0]) return;
    setView(board.turnTo(view, wrapYaw((view.yaw ?? YAW_DEG) + byDeg),
                         size[0], size[1], floor, level));
  }, [board, view, size, floor, level]);

  /** Where a token should be DRAWN — mid-walk if it is the one walking. */
  const drawnAt = useCallback((t: VttToken): [number, number] =>
    (walk && walkPos && walk.tokenId === t.id ? walkPos : [t.x, t.y]),
    [walk, walkPos]);

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

  /** What the hovered square would be worth, when the server has said. Tied to
   *  the same conditions as the route preview: it is an answer about a square
   *  somebody is considering, and it means nothing about a square nobody is. */
  const previewHere = !!(p.preview?.ok && hover
    && p.preview.token_id === selectedToken?.id
    && (p.preview.x === undefined
        || (p.preview.x === hover[0] && p.preview.y === hover[1])));
  const coverAt = previewHere && !aiming ? coverPhrase(p.preview!.cover) : "";

  const provokes = (p.preview && selectedToken
    && p.preview.token_id === selectedToken.id
    && hover && p.preview.ok
    && (p.preview.opportunity?.length ?? 0) > 0)
    ? p.preview.opportunity ?? []
    : [];

  // The route drawn is the route the server WOULD WALK. `path_preview` returns
  // its own A* result and that is what gets drawn the moment it lands; the
  // cost-map descent below is only the instant stand-in for the ~180 ms before
  // it does. They can genuinely differ — equal-cost neighbours break ties
  // differently, and the descent knows nothing of the extra costs the server
  // applied — so drawing the guess and walking the other one was a "proof of
  // path" that occasionally proved the wrong path.
  const path = useMemo(() => {
    if (!selectedToken || !hover || aiming) return null;
    const served = p.preview;
    if (served && served.ok && served.token_id === selectedToken.id
        && served.path && served.path.length
        && served.path[served.path.length - 1][0] === hover[0]
        && served.path[served.path.length - 1][1] === hover[1]) {
      return served.path;
    }
    if (!reach) return null;
    return pathFromCosts(reach, [selectedToken.x, selectedToken.y], hover);
  }, [reach, selectedToken, hover, p.preview, aiming]);
  const pathCost = hover && reach && !aiming
    ? reach.get(`${hover[0]},${hover[1]}`) : undefined;

  // ---- draw ---------------------------------------------------------------
  const draw = useCallback(() => {
    if (!board || !view || size[0] === 0) return;
    board.draw({
      scene: { ...floor, tokens: floor.tokens.filter(onThisFloor) },
      stairs: stairsHere, levels: floors, level,
      view, art: artRef.current,
      // A movement wash is a plan for a creature standing on YOUR floor; drawn
      // over a storey you are only looking at, it is an invitation to a move
      // that cannot happen. While aiming it goes away entirely — you are
      // choosing a target, and two overlapping washes read as neither.
      reach: level === myLevel && !aiming ? reach : null,
      threatened: level === myLevel && !aiming ? threatened : null,
      path: level === myLevel ? path : null,
      pathCost,
      pathLegal: pathCost !== undefined, pathProvokes: provokes.length > 0,
      area: level === myLevel ? areaSquares : null,
      areaLegal: p.area?.ok !== false,
      hover, measure, show, pings, sprites: SPRITES, now: Date.now(),
      walking: walk && walkPos
        ? { tokenId: walk.tokenId, x: walkPos[0], y: walkPos[1] } : null,
    }, size[0], size[1]);
  }, [board, floor, onThisFloor, stairsHere, floors, level, myLevel, view, size, reach,
      threatened, aiming, areaSquares, p.area?.ok,
      path, pathCost, hover, measure, show, pings, provokes.length, spriteTick,
      walk, walkPos]);

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
    if (!canvasEl || !board || !view) return null;
    const r = canvasEl.getBoundingClientRect();
    return board.squareAt(view, floor, e.clientX - r.left, e.clientY - r.top, level);
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
    // Shift-drag turns instead of panning. Free rather than stepped, because
    // the arithmetic supports any angle and a board you can only see from
    // twenty-four places is a worse board than one you can see from anywhere.
    if (e.shiftKey && board?.canTurn) {
      setDrag({ kind: "turn", x: e.clientX, from: view.yaw ?? YAW_DEG });
      return;
    }
    setDrag({ kind: "pan", x: e.clientX, y: e.clientY, from: view });
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const sq = squareAt(e);
    if (sq) setHover(sq);
    if (measuring && measure) {
      if (sq) setMeasure([measure[0], sq]);
      return;
    }
    if (drag?.kind === "pan" && view && board) {
      // From the view the drag STARTED at, so the gesture is absolute and a
      // renderer whose pan is not a plain translate cannot accumulate error.
      setView(board.panBy(drag.from, e.clientX - drag.x, e.clientY - drag.y,
                          floor, level));
    } else if (drag?.kind === "turn" && view && board) {
      // A third of a degree per pixel: a full turn is about a thousand pixels,
      // which is a deliberate drag rather than a flick.
      setView(board.turnTo(view, wrapYaw(drag.from + (e.clientX - drag.x) * 0.36),
                           size[0], size[1], floor, level));
    }
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const sq = squareAt(e);
    const wasPan = drag?.kind === "pan";
    const moved = wasPan && drag?.kind === "pan"
      && (Math.abs(e.clientX - drag.x) > 4 || Math.abs(e.clientY - drag.y) > 4);
    const turned = drag?.kind === "turn";
    setDrag(null);
    if (measuring) return;
    if (turned || !sq || moved) return;
    // Aiming owns the click. A template is placed on a SQUARE, so it lands
    // here; a creature target is a click on the token itself (below), because
    // clicking the ground near someone is not choosing them.
    if (aimingArea && myToken) {
      if (p.area?.ok === false) return;    // an illegal placement is not a move
      p.onTakeAimed(p.armed!, { x: sq[0], y: sq[1] });
      return;
    }
    if (aiming) return;
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
    if (!view || !board || !canvasEl) return;
    // The wheel is the ZOOM, and only the zoom. Left to bubble it also scrolls
    // whatever the board is sitting in, so zooming in walked the page away
    // underneath the map. (React attaches wheel passively at the root, so the
    // native listener registered below is what can actually cancel it; this
    // stops the bubble, that stops the scroll.)
    e.stopPropagation();
    const r = canvasEl.getBoundingClientRect();
    setView(board.zoomAt(view, e.clientX - r.left, e.clientY - r.top,
                         e.deltaY < 0 ? 1.12 : 1 / 1.12));
  };

  // React's own wheel handler is passive, so `preventDefault` there is a no-op
  // and the browser scrolls anyway. A non-passive native listener is the only
  // thing that can refuse it.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const stop = (e: WheelEvent) => e.preventDefault();
    el.addEventListener("wheel", stop, { passive: false });
    return () => el.removeEventListener("wheel", stop);
  }, [canvasEl]);

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

  const tokenNodes = view && board && scene.tokens.filter(onThisFloor)
    .filter(inSight).map((t) => {
    // Where this creature lands on screen is the renderer's answer, not this
    // component's. That indirection is the whole of what makes a token work on
    // an isometric board: a DOM element over a canvas already faces the camera,
    // so "billboarded character" needs no billboard — only a projection.
    const [dx, dy] = drawnAt(t);
    const at = board.screenOf(view, floor, dx, dy, t.squares, level, t.elevation_ft);
    const active = scene.current_token_id === t.id;
    const vit = t.combatant_id != null ? vitals.get(t.combatant_id) : undefined;
    const hpPct = vit ? Math.max(0, Math.min(100, (100 * vit.hp) / vit.max)) : null;
    const pips = (vit?.conds ?? [])
      .map((c) => COND_PIP[c.toLowerCase()])
      .filter(Boolean)
      .slice(0, 3);
    // While aiming, every token says whether it may be chosen. The illegal
    // ones stay on the board wearing their reason — a target that simply
    // vanished would be indistinguishable from a bug, and the reason is
    // usually the thing the player needs (walk closer, break line of sight).
    const tgt = aiming ? targetById.get(t.id) : undefined;
    const inArea = aimingArea
      && !!p.area?.caught?.some((c) => c.token_id === t.id);
    const cls = [
      "vtt-token", `team-${t.team}`, `kind-${t.kind}`,
      active ? "active" : "", t.defeated ? "down" : "",
      t.prone ? "prone" : "", t.hidden ? "ghost" : "",
      selected === t.id ? "selected" : "", isMine(t) ? "mine" : "",
      tgt ? (tgt.legal ? "targetable" : "untargetable") : "",
      inArea ? "in-area" : "",
      // Something opaque stands between the camera and this square. Drawn as a
      // silhouette rather than hidden — see TokenPlacement. Never set by the
      // flat board, which has nothing to stand in the way.
      at.occluded ? "occluded" : "",
    ].filter(Boolean).join(" ");
    return (
      <div
        key={t.id}
        className={cls}
        style={{
          left: at.left, top: at.top, width: at.size, height: at.size,
          zIndex: Math.round(1000 - at.depth),
          ["--tok" as string]: t.color || undefined,
        }}
        title={tgt
          ? `${t.name} — ${tgt.distance_ft} ft`
            + (tgt.legal
              ? (tgt.cover && tgt.cover !== "none" ? ` · ${tgt.cover} cover` : "")
              : ` · ${tgt.reason}`)
          : `${t.name}` +
            (vit ? ` — ${vit.hp}/${vit.max} HP${vit.temp ? ` (+${vit.temp} temp)` : ""}` : "") +
            (t.elevation_ft ? ` · ${t.elevation_ft} ft up` : "") +
            ` · ${t.moved_ft}/${t.speed_ft} ft moved`}
        onPointerDown={(e) => {
          e.stopPropagation();
          // Aiming: this click CHOOSES, it doesn't select. An illegal target
          // is inert rather than an error — its reason is already on it.
          if (aiming && p.armed!.targeting === "creature") {
            if (tgt?.legal) p.onTakeAimed(p.armed!, { targetTokenId: t.id });
            return;
          }
          setSelected(t.id);
        }}
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
    <div className={`vtt${p.fill ? " fill" : ""}`}>
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
          <button className={mode === "iso" ? "on" : ""}
            title={mode === "iso" ? "Isometric board — switch to flat"
                                  : "Flat board — switch to isometric"}
            onClick={() => setMode((m) => (m === "iso" ? "flat" : "iso"))}>◪</button>
          {board?.canTurn && (
            <>
              <button title="Turn the board left"
                onClick={() => turn(-YAW_STEP_DEG)}>⟲</button>
              <button title="Turn the board right"
                onClick={() => turn(YAW_STEP_DEG)}>⟳</button>
              {Math.round(((view?.yaw ?? YAW_DEG) - YAW_DEG + 540) % 360 - 180) !== 0 && (
                <button className="on" title="Back to the painted view"
                  onClick={() => turn(YAW_DEG - (view?.yaw ?? YAW_DEG))}>◈</button>
              )}
            </>
          )}
          <button title="Fit the board"
            onClick={() => board && size[0]
              && setView(board.fit(scene, size[0], size[1], view?.yaw ?? YAW_DEG))}>⤢</button>
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
        ref={(el) => {
          wrapRef.current = el;
          // Not handed to the resizer when filling, or the height persisted on
          // the play surface would clamp the battle page's board.
          boardR.ref.current = p.fill ? null : el;
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { setHover(null); setDrag(null); }}
        onWheel={onWheel}
        onContextMenu={onContextMenu}
      >
        <canvas key={mode} data-mode={mode} ref={setCanvasEl} />
        <div className="vtt-tokens">{tokenNodes}</div>
        {p.error && (
          <div className="vtt-error" onClick={p.onDismissError}>{p.error}</div>
        )}
      </div>

      {!p.fill && (
        <div
          className="vtt-grip"
          title="Drag to give the board more room, or give it back to the chat"
          onPointerDown={boardR.onGripDown}
        />
      )}

      <footer className="vtt-foot">
        {aiming ? (
          <>
            <span className="vtt-sel">{p.armed!.name}</span>
            {aimingArea ? (
              <span className={`vtt-hint${p.area?.ok === false ? " bad" : ""}`}>
                {p.area?.ok === false
                  ? `⚠ ${p.area.reason}`
                  : (p.area?.caught?.length
                    ? `catches ${p.area.caught.map((c) => c.name).join(", ")}`
                    : "click to place it · it catches nobody yet")}
              </span>
            ) : (
              <span className="vtt-hint">
                {(() => {
                  const legal = [...targetById.values()].filter((t) => t.legal);
                  return legal.length
                    ? `click a ringed target — ${legal.length} in reach`
                    : "nothing is in reach of that — walk closer, or pick something else";
                })()}
              </span>
            )}
          </>
        ) : selectedToken ? (
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
                {coverAt && (
                  <span className="vtt-cover" title={
                    (p.preview?.cover?.from ?? [])
                      .map((f) => `${f.name}: ${f.cover}`).join(" · ")}>
                    ⛊ {coverAt}
                  </span>
                )}
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

      {p.children}
    </div>
  );
}
