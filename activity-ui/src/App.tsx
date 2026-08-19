import { useEffect, useRef, useState } from "react";
import { connect, type ConnStatus, type Connection } from "./lib/connection";
import type {
  ActionBarData, Ally, ArenaState, BarAction, CCPayload, CharacterSummary,
  CombatState, LevelUpData, LexEntry,
  ChronicleData, Locale, RepData, RouteRow, ServerEvent, SheetData, VttArea,
  VttOptions, VttScene, VttTargets, WorldShop, BastionPlan,
} from "./lib/types";
import { Block, isTyped, makeOracleBlock, makeSpeechBlock } from "./components/Narration";
import { CreateFlow } from "./components/CreateFlow";
import { Landing } from "./components/Landing";
import { Arena, ArenaResult, Quartermaster } from "./components/Arena";
import { LevelUpOverlay } from "./components/LevelUp";
import { ReprepareOverlay } from "./components/Reprepare";
import { PlaySurface } from "./components/PlaySurface";
import { BattleSurface } from "./components/BattleSurface";
import { Stall } from "./components/Stall";
import { BastionBuilder } from "./components/BastionBuilder";
import { Chronicle } from "./components/Chronicle";
import { ItemInspector, type ItemView } from "./components/ItemInspector";
import { levelChime, rollThunk } from "./lib/sound";
import { closeActivity, type Session } from "./lib/session";

/** Ornamental corner bracket — bold keylines with a brass stud. */
function Corner({ pos }: { pos: string }) {
  return (
    <svg className={`corner ${pos}`} viewBox="0 0 34 34" fill="none">
      <path d="M2 32 V10 Q2 2 10 2 H32" stroke="currentColor" strokeWidth="3" />
      <path d="M8 32 V14 Q8 8 14 8 H32" stroke="currentColor" strokeWidth="1" opacity="0.5" />
      <circle cx="7" cy="7" r="2.6" fill="currentColor" />
    </svg>
  );
}

type Screen = "landing" | "create" | "play" | "arena";

export default function App({ session }: { session: Session }) {
  const [screen, setScreen] = useState<Screen>("landing");
  // The way OUT. "ask" is the confirmation (leaving mid-session by a stray tap
  // would be worse than having no button at all); "bye" is the honest ending
  // for a plain browser tab, which a script may not close.
  const [exiting, setExiting] = useState<null | "ask" | "bye">(null);
  // The Proving Grounds: practice bouts. `arenaMode` means the play surface is
  // showing a bout, so "main menu" goes back to the Grounds, not the landing.
  const [arena, setArena] = useState<ArenaState | null>(null);
  const [arenaMode, setArenaMode] = useState(false);
  const arenaSlotRef = useRef<number | null>(null);
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [blocks, setBlocks] = useState<Block[]>([]);
  // The DM writing, live. Not a Block: it is a preview the server has not
  // finished with, and it is thrown away rather than kept (see the
  // narration_delta case).
  const [draft, setDraft] = useState("");
  // Somebody here sells something. Asked for whenever the scene changes, so
  // the button only exists where a stall does; null closes it.
  const [shop, setShop] = useState<WorldShop | null>(null);
  const [stallOpen, setStallOpen] = useState(false);
  // The bastion builder. Asked for on demand rather than pushed: raising a
  // stronghold is a deliberate act, not something the play surface nags about.
  const [bastion, setBastion] = useState<BastionPlan | null>(null);
  const [bastionOpen, setBastionOpen] = useState(false);
  const [bastionBusy, setBastionBusy] = useState(false);
  const [bastionError, setBastionError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<SheetData | null>(null);
  const [locale, setLocale] = useState<Locale | null>(null);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [routes, setRoutes] = useState<RouteRow[]>([]);
  const [chronicle, setChronicle] = useState<ChronicleData | null>(null);
  const [party, setParty] = useState<Ally[]>([]);
  const [combat, setCombat] = useState<CombatState | null>(null);
  // The tactical board: present only while the Oracle has one out.
  const [vtt, setVtt] = useState<VttScene | null>(null);
  const [vttOptions, setVttOptions] = useState<VttOptions | null>(null);
  const [vttPing, setVttPing] = useState<{ x: number; y: number; label?: string; at: number } | null>(null);
  const [vttPreview, setVttPreview] = useState<
    { token_id: number; ok: boolean; cost_ft?: number;
      path?: [number, number][]; opportunity?: string[] } | null>(null);
  const [vttError, setVttError] = useState<string | null>(null);
  // The action bar, and what is armed on it. `armed` is deliberately client
  // state: choosing an act is not doing one, and nothing is sent until the
  // board has been aimed.
  const [actions, setActions] = useState<ActionBarData | null>(null);
  const [armed, setArmed] = useState<BarAction | null>(null);
  const [armedSlot, setArmedSlot] = useState<number | null>(null);
  const [vttTargets, setVttTargets] = useState<VttTargets | null>(null);
  const [vttArea, setVttArea] = useState<VttArea | null>(null);
  const [sceneUrl, setSceneUrl] = useState<string | null>(null);
  const [levelUp, setLevelUp] = useState<LevelUpData | null>(null);
  const [repData, setRepData] = useState<RepData | null>(null);
  const [busy, setBusy] = useState(false);
  const [ccError, setCcError] = useState<string | null>(null);
  // Portrait-step → world entry: the enter round-trip runs an LLM intro + scene
  // render and can take a while (or fail), so the portrait step needs its own
  // progress + error feedback — otherwise the button looks like it does nothing.
  const [entering, setEntering] = useState(false);
  const [enterError, setEnterError] = useState<string | null>(null);
  const [notice, setNotice] = useState<
    | { kind: "join_blocked"; reason: string; charName: string }
    | { kind: "invite"; place: string; channel: string }
    | null>(null);
  const [rateWait, setRateWait] = useState(0);
  // Whether the Oracle is still on the other end of the wire. A dropped socket
  // used to be silent, and silence reads as a frozen screen.
  const [link, setLink] = useState<ConnStatus>("open");
  // What the table is waiting for, when the wait is long enough to look like a
  // hang. Opening a bout rosters an encounter, generates a board and may draw
  // it; that is tens of seconds, and it used to happen behind a screen that
  // simply stopped responding.
  const [waiting, setWaiting] = useState<string | null>(null);
  const waitTimersRef = useRef<number[]>([]);
  const clearWaitTimers = () => {
    for (const t of waitTimersRef.current) window.clearTimeout(t);
    waitTimersRef.current = [];
  };
  const awaitOracle = (label: string) => {
    clearWaitTimers();
    waitTimersRef.current.push(
      // Shown only if the answer is actually SLOW. An answer that lands in a
      // few frames needs no veil, and a veil that flashes for 30ms is noise —
      // offline, where the Grounds answer synchronously, it never appears.
      window.setTimeout(() => setWaiting(label), 250),
      // A failsafe, not a schedule: if the answer never comes, the veil must
      // not become the frozen screen it exists to replace.
      window.setTimeout(() => setWaiting(null), 120000),
    );
  };
  const doneWaiting = () => {
    clearWaitTimers();
    setWaiting(null);
  };
  const [newChar, setNewChar] = useState<{ name: string; id: number | null } | null>(null);
  const [input, setInput] = useState("");
  const [itemView, setItemView] = useState<ItemView | null>(null);
  const lastEnterRef = useRef<string>("");
  const lexRef = useRef<LexEntry[]>([]);
  const connRef = useRef<Connection | null>(null);
  const pendingEnterRef = useRef<string | null>(null);
  const enterTimerRef = useRef<number | null>(null);
  const screenRef = useRef<Screen>("landing");
  screenRef.current = screen;
  // The sealed character, for handlers that run outside React's render.
  const newCharRef = useRef<typeof newChar>(null);
  newCharRef.current = newChar;

  const clearEnterTimer = () => {
    if (enterTimerRef.current) {
      window.clearTimeout(enterTimerRef.current);
      enterTimerRef.current = null;
    }
  };

  /** Fire an `enter`, with progress + a failsafe timeout so the world-entry
   *  button never dead-ends (the round-trip runs an LLM intro + scene render). */
  const beginEnter = (opts: { character_name: string; solo?: boolean }) => {
    lastEnterRef.current = opts.character_name;
    // A socket that dropped during creation would swallow this send silently —
    // fail fast with an actionable message instead of a dead button.
    if (connRef.current && !connRef.current.isOpen()) {
      setEnterError("Lost the link to the Oracle — reload the Activity to reconnect.");
      setEntering(false);
      return;
    }
    setEnterError(null);
    setEntering(true);
    clearEnterTimer();
    enterTimerRef.current = window.setTimeout(() => {
      enterTimerRef.current = null;
      setEntering(false);
      setEnterError("The world is slow to open — the Oracle may be busy.");
    }, 90000);
    connRef.current?.send({ t: "enter", ...opts });
  };

  // A board coming out is the moment the bar becomes useful. The server also
  // pushes a fresh one after every turn; this is what fills it the FIRST time,
  // and it is how the offline demo gets one at all.
  useEffect(() => {
    if (vtt) connRef.current?.send({ t: "actions" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vtt?.id]);

  useEffect(() => {
    const channel = session.channel;
    const conn = connect((ev: ServerEvent) => {
      switch (ev.t) {
        case "hello":
          setCharacters(ev.characters);
          // A cc_done→enter round trip refreshes hello; don't yank the
          // player back to the landing mid-flow.
          if (screenRef.current === "play") break;
          if (pendingEnterRef.current) {
            const nm = pendingEnterRef.current;
            pendingEnterRef.current = null;
            connRef.current?.send({ t: "enter", character_name: nm });
          }
          break;
        case "entered":
          doneWaiting();
          clearEnterTimer();
          setArenaMode(!!ev.arena);
          setScreen("play");
          setEntering(false);
          break;
        case "arena":
          setArena(ev.state);
          // The Grounds answered — whatever we were waiting on has landed.
          doneWaiting();
          // A slot we were filling has been forged — back to the Grounds.
          if (arenaSlotRef.current !== null && screenRef.current === "create") {
            arenaSlotRef.current = null;
            setScreen("arena");
          }
          break;
        case "cc_done": {
          // The wizard stays mounted after the seal: the likeness was chosen
          // before it, and Name & Seal becomes the way into the world. We do
          // NOT set pendingEnterRef, so the following `hello` won't auto-enter
          // — the player steps through when they are ready.
          const det = ev.detail as { character_id?: number } | undefined;
          const id = det && typeof det.character_id === "number" ? det.character_id : null;
          setNewChar({ name: ev.name, id });
          break;
        }
        case "cc_error":
          // Once the character is sealed, a cc_error is the ENTER failing, not
          // creation — it belongs on the sealed panel, beside its button.
          if (newCharRef.current) {
            clearEnterTimer();
            setEnterError(ev.detail);
            setEntering(false);
          } else {
            setCcError(ev.detail);
          }
          break;
        case "join_blocked":
          clearEnterTimer();
          setEntering(false);
          setNotice({ kind: "join_blocked", reason: ev.reason,
                      charName: lastEnterRef.current });
          break;
        case "table_invite":
          setNotice({ kind: "invite", place: ev.place, channel: ev.channel });
          break;
        case "rate_limited":
          setRateWait(ev.wait);
          setTimeout(() => setRateWait(0), ev.wait * 1000);
          break;
        case "lexicon":
          lexRef.current = ev.entries;
          break;
        case "player":
          // A new turn starts clean. A preview whose `narration_end` never
          // arrived (a socket that dropped mid-generation) must not sit under
          // the next reply.
          setDraft("");
          setBlocks((b) => [...b, { kind: "player", text: ev.text, who: ev.who,
                                    secret: ev.secret }]);
          break;
        case "narration":
          setBlocks((b) => [...b, makeOracleBlock(ev.text, lexRef.current, ev.secret)]);
          break;
        // The DM writing, live. Held apart from `blocks` on purpose: it is a
        // preview of text the server has not finished with — the dice in it are
        // still hooks and its dialogue has not been split into speech cards —
        // so it is DISCARDED on `narration_end` and the authoritative blocks
        // arrive behind it. Appending it would leave the same paragraph on
        // screen twice, once wrong.
        case "narration_delta":
          setDraft((d) => d + ev.text);
          break;
        case "narration_end":
          setDraft("");
          break;
        case "speech":
          setBlocks((b) => [...b, makeSpeechBlock(ev.text, lexRef.current, ev.who,
                                                  ev.portrait, ev.script,
                                                  ev.secret)]);
          break;
        case "whisper":
          setBlocks((b) => [...b, { kind: "whisper", text: ev.text }]);
          break;
        case "roll":
          rollThunk(ev.roll.success);
          setBlocks((b) => [...b, { kind: "roll", roll: ev.roll }]);
          break;
        case "sheet":
          setSheet(ev.sheet);
          break;
        case "locale":
          setLocale(ev.locale);
          // Who is standing here decides whether there is anything to buy.
          connRef.current?.send({ t: "shop" });
          break;
        case "shop":
          setShop(ev.shop);
          if (!ev.shop) setStallOpen(false);
          break;
        case "bastion":
          setBastion(ev.plan);
          setBastionBusy(false);
          break;
        case "bastion_built":
          setBastionBusy(false);
          setBastionError(ev.ok ? null : (ev.detail || "It could not be raised."));
          if (ev.ok) setBastionOpen(false);
          break;
        // Ordering the work does NOT close the screen: the point of the
        // builders being in is that you can see it, and a stronghold you are
        // still adding to is the normal state of one.
        case "bastion_works":
          setBastionBusy(false);
          setBastionError(ev.ok ? null : (ev.detail || "The work could not begin."));
          break;
        case "suggest":
          setSuggestions(ev.actions);
          break;
        case "routes":
          setRoutes(ev.routes);
          break;
        case "chronicle_data":
          setChronicle({ entries: ev.entries, quests: ev.quests,
                         bonds: ev.bonds, standing: ev.standing ?? [],
                         codex: ev.codex ?? [], error: ev.error });
          break;
        case "party":
          setParty(ev.members);
          break;
        case "combat":
          setCombat(ev.encounter);
          break;
        case "vtt":
          setVtt(ev.scene);
          if (!ev.scene) {
            setVttOptions(null); setVttError(null); setVttPreview(null);
            // The board going away disarms whatever was aimed at it — an act
            // waiting for a click it can no longer receive is a trap.
            setArmed(null); setArmedSlot(null);
            setVttTargets(null); setVttArea(null);
          }
          break;
        case "vtt_options":
          setVttOptions({ token_id: ev.token_id, budget_ft: ev.budget_ft,
                          level: ev.level, squares: ev.squares,
                          threatened: ev.threatened });
          break;
        case "vtt_preview":
          setVttPreview({ token_id: ev.token_id, ok: ev.ok, cost_ft: ev.cost_ft,
                          path: ev.path, opportunity: ev.opportunity });
          break;
        case "vtt_targets":
          setVttTargets({ action_id: ev.action_id, ok: ev.ok,
                          actor: ev.actor, actor_token_id: ev.actor_token_id,
                          range_ft: ev.range_ft, targets: ev.targets });
          break;
        case "vtt_area":
          setVttArea({ action_id: ev.action_id, ok: ev.ok, reason: ev.reason,
                       shape: ev.shape, origin: ev.origin, level: ev.level,
                       distance_ft: ev.distance_ft, squares: ev.squares,
                       caught: ev.caught });
          break;
        case "actions":
          setActions(ev.data);
          break;
        case "vtt_ping":
          setVttPing({ x: ev.x, y: ev.y, label: ev.label, at: Date.now() });
          break;
        case "vtt_error":
          setVttError(ev.detail);
          window.setTimeout(() => setVttError(null), 4000);
          break;
        case "scene":
          setSceneUrl(ev.url);
          break;
        case "item_detail":
          setItemView((v) => {
            const prevImg = v?.detail?.image;
            return {
              name: ev.item.name,
              detail: { ...ev.item, image: ev.item.image ?? prevImg ?? null },
              loading: false,
            };
          });
          break;
        case "item_image":
          setItemView((v) =>
            v && (v.name === ev.name || v.renaming)
              ? { ...v, name: ev.name, drawing: false, artState: undefined,
                  detail: { ...(v.detail ?? { name: ev.name }), image: ev.url } }
              : v);
          break;
        case "item_art_state":
          // No picture, and the server is telling us WHY: either the catalog
          // pre-render has not reached this one, or it is the player's own
          // invention and only they can describe it.
          setItemView((v) =>
            v && v.name === ev.name
              ? { ...v, loading: false, artState: ev.state }
              : v);
          break;
        case "item_error":
          setItemView((v) => (v ? { ...v, loading: false, drawing: false,
                                    error: ev.detail } : v));
          break;
        case "item_gone":
          setItemView((v) => (v && v.name === ev.name ? null : v));
          break;
        case "levelup":
          if (ev.data) levelChime();
          setLevelUp(ev.data);
          break;
        case "reprepare_data":
          setRepData({ count: ev.count, max_spell_level: ev.max_spell_level,
            class: ev.class, current: ev.current, options: ev.options,
            source: ev.source, no_spellbook: ev.no_spellbook });
          break;
        case "busy":
          setBusy(ev.on);
          break;
      }
    }, channel, session.userId, session.username, (st) => {
      setLink(st);
      // A fresh socket is bound to no session, so coming back means entering
      // again — the same path `hello` already uses when creation hands over.
      if (st === "open" && screenRef.current === "play" && lastEnterRef.current) {
        pendingEnterRef.current = lastEnterRef.current;
      }
    });
    connRef.current = conn;
    return () => conn.close();
  }, []);

  const submit = (secret?: boolean, override?: string) => {
    const text = (override ?? input).trim();
    if (!text || busy) return;
    if (override === undefined) setInput("");
    // The chips and roads describe the scene that just ended; the next reply
    // brings its own.
    setSuggestions([]);
    setRoutes([]);
    connRef.current?.send({ t: "action", text, private: !!secret });
  };

  const inspectItem = (name: string) => {
    setItemView({ name, loading: true });
    connRef.current?.send({ t: "inspect_item", name });
  };
  const inscribeSpell = (book: string, spell: string) => {
    setItemView((v) => (v ? { ...v, loading: true, error: undefined } : v));
    connRef.current?.send({ t: "inscribe_spell", spell, book });
  };
  const itemAction = (name: string, action: string, target?: string) => {
    setItemView((v) => (v ? { ...v, loading: true, error: undefined } : v));
    connRef.current?.send({ t: "item_action", name, action, target });
  };
  const temperItem = (name: string, affix: string) => {
    setItemView((v) => (v ? { ...v, loading: true, error: undefined } : v));
    connRef.current?.send({ t: "temper_item", name, affix });
  };
  const describeItem = (name: string, text: string, title?: string) => {
    // A rename means the next item_image arrives under a DIFFERENT name, so the
    // inspector has to know to accept it.
    setItemView((v) => (v ? { ...v, drawing: true, error: undefined,
                              renaming: !!title && title !== name } : v));
    connRef.current?.send({ t: "describe_item", name, text, title });
  };
  const portraitAction = (
    action: "regear" | "select" | "delete",
    opts?: { context?: string; replace_context?: string; detail?: string },
  ) => {
    connRef.current?.send({ t: "portrait_action", action, ...opts });
  };

  const setDnr = (dnr: boolean) => {
    setSheet((s) => (s ? { ...s, dnr } : s));  // optimistic; server confirms via sheet push
    connRef.current?.send({ t: "set_dnr", dnr });
  };

  const skipAll = () =>
    setBlocks((bs) => bs.map((b) => (isTyped(b) ? { ...b, done: true } : b)));

  const markDone = (i: number) =>
    setBlocks((bs) => bs.map((b, j) => (j === i && isTyped(b) ? { ...b, done: true } : b)));

  // A tactical fight owns the screen: same props, a layout built around the
  // board and the acts you can take on it. A fight with no board stays on the
  // play surface — theatre of the mind wants the rail and the sheet, and there
  // is no map to make room for. See BattleSurface.
  const Surface = (combat && vtt) ? BattleSurface : PlaySurface;

  return (
    <div className="table">
      <div className={`frame${screen === "play" ? " playing" : ""}`}>
        <Corner pos="tl" /><Corner pos="tr" /><Corner pos="bl" /><Corner pos="br" />

        {/* The link, when it isn't there. Every panel in this app is driven by
            the socket, so a dropped one used to present as whatever screen you
            were holding simply refusing to respond — no error, no way out. */}
        {/* A long wait, said out loud. Opening a bout takes tens of seconds
            (roster, board, art) and used to look exactly like a hang. */}
        {waiting && (
          <div className="oracle-wait">
            <div className="ow-box">
              <div className="ow-ring"><i /><i /><i /></div>
              <div className="ow-label">{waiting}</div>
              <div className="ow-sub">
                the ground is being laid out and the roster drawn up
              </div>
            </div>
          </div>
        )}

        {(link === "lost" || link === "reconnecting") && (
          <div className="link-lost">
            <span className="link-dot" />
            {link === "lost"
              ? "Lost the link to the Oracle — reconnecting…"
              : "Still reconnecting…"}
            <button onClick={() => location.reload()}>Reload</button>
          </div>
        )}

        {screen === "landing" && (
          <Landing
            characters={characters}
            onEnter={(name) => beginEnter({ character_name: name })}
            onCreate={() => {
              setCcError(null);
              arenaSlotRef.current = null;
              setScreen("create");
            }}
            onArena={() => {
              connRef.current?.send({ t: "arena_state" });
              setScreen("arena");
            }}
            onExit={() => setExiting("ask")}
          />
        )}

        {exiting && (
          <div className="levelup-veil"
               onClick={() => exiting === "ask" && setExiting(null)}>
            <div className="levelup" onClick={(e) => e.stopPropagation()}>
              <div className="levelup-head">
                <span className="lu-title">
                  {exiting === "ask" ? "Leave the Oracle?" : "Until next time"}
                </span>
              </div>
              {exiting === "ask" ? (
                <>
                  <p style={{ lineHeight: 1.6 }}>
                    The world keeps its own time — your character, your place and
                    everything you've done stay exactly as they are, and you can
                    come back to them whenever you like.
                  </p>
                  <div className="lu-actions" style={{ gap: 10 }}>
                    <button className="lu-confirm" onClick={async () => {
                      if (!(await closeActivity())) setExiting("bye");
                    }}>Leave the table</button>
                    <button className="lu-confirm" onClick={() => setExiting(null)}>
                      Stay
                    </button>
                  </div>
                </>
              ) : (
                <p style={{ lineHeight: 1.6 }}>
                  You've left the table. This tab can be closed — a page a script
                  didn't open, it can't close by itself.
                </p>
              )}
            </div>
          </div>
        )}

        {screen === "arena" && (
          <Arena
            state={arena}
            onCreate={(slot) => {
              setCcError(null);
              arenaSlotRef.current = slot;
              setScreen("create");
            }}
            onDelete={(slot) => connRef.current?.send({ t: "arena_delete", slot })}
            onBegin={({ slot, environment, level, difficulty, reuse }) => {
              setBlocks([]);   // a new bout starts on a clean surface
              awaitOracle("The Grounds are being made ready…");
              connRef.current?.send({ t: "arena_begin", slot, environment, level,
                                      difficulty, reuse });
            }}
            onBack={() => setScreen("landing")}
          />
        )}

        {notice && (
          <div className="levelup-veil" onClick={() => setNotice(null)}>
            <div className="levelup" onClick={(e) => e.stopPropagation()}>
              {notice.kind === "join_blocked" ? (
                <>
                  <div className="levelup-head">
                    <span className="lu-title">The Road Is Long</span>
                  </div>
                  <p style={{ lineHeight: 1.6 }}>{notice.reason}</p>
                  <div className="lu-actions" style={{ gap: 10 }}>
                    <button className="lu-confirm" onClick={() => {
                      setNotice(null);
                      beginEnter({ character_name: notice.charName, solo: true });
                    }}>Travel on your own tale</button>
                    <button className="lu-confirm" onClick={() => setNotice(null)}>
                      Back
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="levelup-head">
                    <span className="lu-title">A Familiar Fire</span>
                  </div>
                  <p style={{ lineHeight: 1.6 }}>
                    Another party's tale is unfolding at {notice.place}. Join
                    their channel in Discord to sit at their table.
                  </p>
                  <div className="lu-actions">
                    <button className="lu-confirm" onClick={() => setNotice(null)}>
                      Understood
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {screen === "create" && (
          <CreateFlow
            ccError={ccError}
            sealed={newChar}
            entering={entering}
            enterError={enterError}
            onEnterWorld={() => newChar && beginEnter({ character_name: newChar.name })}
            onCancel={() => setScreen(arenaSlotRef.current !== null ? "arena" : "landing")}
            onDone={(payload: CCPayload) => {
              setCcError(null);
              const slot = arenaSlotRef.current;
              if (slot !== null) {
                connRef.current?.send({ t: "arena_create", slot, payload });
              } else {
                connRef.current?.send({ t: "cc_register", payload });
              }
            }}
          />
        )}

        {screen === "play" && (
          <>
            {arenaMode && arena && arena.run?.phase === "outfitting" && (
              <Quartermaster
                state={arena}
                onOutfit={(cart, equip) => {
                  awaitOracle("The wards are closing…");
                  connRef.current?.send({ t: "arena_outfit", cart, equip });
                }}
              />
            )}
            {arenaMode && arena && (
              <ArenaResult
                state={arena}
                onAgain={() => {
                  awaitOracle("The wards are closing…");
                  connRef.current?.send({ t: "arena_fight" });
                }}
                onElsewhere={(environment) => {
                  awaitOracle("The wards are closing…");
                  connRef.current?.send({ t: "arena_fight", environment });
                }}
                onOutfit={() => connRef.current?.send({ t: "arena_shop" })}
                onLeave={() => {
                  connRef.current?.send({ t: "arena_leave" });
                  setArenaMode(false);
                  setBlocks([]);
                  setScreen("arena");
                }}
              />
            )}
            {levelUp && (
              <LevelUpOverlay
                data={levelUp}
                onApply={({ subclass, cantrips, spells, swap_out, swap_in,
                            ability_increases, feat, feat_choices }) =>
                  connRef.current?.send({ t: "levelup_apply", subclass, cantrips, spells,
                    swap_out, swap_in, ability_increases, feat, feat_choices })}
              />
            )}
            {bastionOpen && bastion && (
              <BastionBuilder
                plan={bastion}
                busy={bastionBusy}
                error={bastionError}
                onClose={() => { setBastionOpen(false); setBastionError(null); }}
                onBuild={(choice) => {
                  setBastionBusy(true);
                  setBastionError(null);
                  connRef.current?.send({ t: "bastion_build", choice });
                }}
                onEnlarge={(facility_id) => {
                  setBastionBusy(true);
                  setBastionError(null);
                  connRef.current?.send({ t: "bastion_enlarge", facility_id });
                }}
              />
            )}
            {stallOpen && shop && (
              <Stall
                shop={shop}
                onBuy={(item) => connRef.current?.send({ t: "shop_buy", item })}
                onClose={() => setStallOpen(false)}
              />
            )}
            <Surface
              blocks={blocks}
              draft={draft}
              hasStall={!!shop}
              onOpenStall={() => setStallOpen(true)}
              onOpenBastion={() => {
                setBastionOpen(true);
                connRef.current?.send({ t: "bastion_plan" });
              }}
              sheet={sheet}
              locale={locale}
              suggestions={suggestions}
              routes={routes}
              sceneUrl={sceneUrl}
              party={party}
              combat={combat}
              vtt={vtt}
              vttOptions={vttOptions}
              vttPing={vttPing}
              vttPreview={vttPreview}
              vttError={vttError}
              actions={actions}
              armed={armed}
              armedSlot={armedSlot}
              vttTargets={vttTargets}
              vttArea={vttArea}
              onArm={(a) => {
                setArmed(a);
                // A fresh act starts at its own level; the previous act's
                // upcast choice must not ride along onto a different spell.
                setArmedSlot(a?.slots?.length ? a.slots[0] : null);
                setVttTargets(null);
                setVttArea(null);
              }}
              onArmedSlot={setArmedSlot}
              onTakeAction={(a, aim) => {
                connRef.current?.send({
                  t: "board_action", action_id: a.id,
                  target_token_id: aim?.targetTokenId,
                  x: aim?.x, y: aim?.y,
                  slot: armedSlot ?? a.slots?.[0],
                });
                setArmed(null);
                setArmedSlot(null);
                setVttTargets(null);
                setVttArea(null);
              }}
              onVttTargets={(a, token_id) =>
                connRef.current?.send({
                  t: "vtt_targets", token_id, action_id: a.id,
                  range_ft: a.range_ft, needs_sight: a.needs_sight !== false })}
              onVttArea={(a, token_id, x, y) =>
                connRef.current?.send({
                  t: "vtt_area", token_id, action_id: a.id, x, y,
                  shape: a.shape || "sphere", radius_ft: a.radius_ft,
                  length_ft: a.length_ft, width_ft: a.width_ft,
                  range_ft: a.range_ft })}
              onVttOptions={(token_id, dash) =>
                connRef.current?.send({ t: "vtt_options", token_id, dash })}
              onVttPreview={(token_id, x, y) =>
                connRef.current?.send({ t: "vtt_preview", token_id, x, y })}
              onVttMove={(token_id, x, y) =>
                connRef.current?.send({ t: "vtt_move", token_id, x, y })}
              onVttStairs={() =>
                connRef.current?.send({ t: "vtt_stairs" })}
              onVttPing={(x, y) => connRef.current?.send({ t: "vtt_ping", x, y })}
              onVttDismissError={() => setVttError(null)}
              input={input}
              setInput={setInput}
              submit={submit}
              busy={busy}
              rateWait={rateWait}
              onSkip={skipAll}
              onBlockDone={markDone}
              onMainMenu={() => {
                if (arenaMode) {
                  connRef.current?.send({ t: "arena_leave" });
                  setArenaMode(false);
                  setBlocks([]);
                  setScreen("arena");
                } else {
                  setScreen("landing");
                }
              }}
              onExit={() => setExiting("ask")}
              onInspect={inspectItem}
              onItemAction={(name, action) => itemAction(name, action)}
              onPortrait={portraitAction}
              onSetDnr={setDnr}
              onReprepare={() => connRef.current?.send({ t: "reprepare" })}
              onChronicle={() => {
                setChronicle({ entries: [], quests: [], bonds: [], standing: [], codex: [] });
                connRef.current?.send({ t: "chronicle" });
              }}
            />
            <Chronicle
              data={chronicle}
              onClose={() => setChronicle(null)}
              onInspect={inspectItem}
            />
            {repData && (
              <ReprepareOverlay
                data={repData}
                onClose={() => setRepData(null)}
                onApply={(spells) => {
                  connRef.current?.send({ t: "reprepare_apply", spells });
                  setRepData(null);
                }}
              />
            )}
            <ItemInspector
              view={itemView}
              onClose={() => setItemView(null)}
              onInscribe={inscribeSpell}
              onAction={itemAction}
              onDescribe={describeItem}
              onTemper={temperItem}
              inventory={sheet?.inventory.map((it) => (typeof it === "string" ? it : it.name))}
            />
          </>
        )}
      </div>
    </div>
  );
}
