import { useEffect, useRef, useState } from "react";
import { connect, type Connection } from "./lib/connection";
import type {
  Ally, ArenaState, CCPayload, CharacterSummary, CombatState, LevelUpData, LexEntry,
  ChronicleData, Locale, RepData, RouteRow, ServerEvent, SheetData, VttOptions,
  VttScene,
} from "./lib/types";
import { Block, isTyped, makeOracleBlock, makeSpeechBlock } from "./components/Narration";
import { CreateFlow } from "./components/CreateFlow";
import { PortraitStep } from "./components/PortraitStep";
import { Landing } from "./components/Landing";
import { Arena, ArenaResult, Quartermaster } from "./components/Arena";
import { LevelUpOverlay } from "./components/LevelUp";
import { ReprepareOverlay } from "./components/Reprepare";
import { PlaySurface } from "./components/PlaySurface";
import { Chronicle } from "./components/Chronicle";
import { ItemInspector, type ItemView } from "./components/ItemInspector";
import { levelChime, rollThunk } from "./lib/sound";
import type { Session } from "./lib/session";

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

type Screen = "landing" | "create" | "portrait" | "play" | "arena";

export default function App({ session }: { session: Session }) {
  const [screen, setScreen] = useState<Screen>("landing");
  // The Proving Grounds: practice bouts. `arenaMode` means the play surface is
  // showing a bout, so "main menu" goes back to the Grounds, not the landing.
  const [arena, setArena] = useState<ArenaState | null>(null);
  const [arenaMode, setArenaMode] = useState(false);
  const arenaSlotRef = useRef<number | null>(null);
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [blocks, setBlocks] = useState<Block[]>([]);
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
    { token_id: number; ok: boolean; cost_ft?: number; opportunity?: string[] } | null>(null);
  const [vttError, setVttError] = useState<string | null>(null);
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
          clearEnterTimer();
          setArenaMode(!!ev.arena);
          setScreen("play");
          setEntering(false);
          break;
        case "arena":
          setArena(ev.state);
          // A slot we were filling has been forged — back to the Grounds.
          if (arenaSlotRef.current !== null && screenRef.current === "create") {
            arenaSlotRef.current = null;
            setScreen("arena");
          }
          break;
        case "cc_done": {
          // Detour through the portrait step before entering the world. We do
          // NOT set pendingEnterRef, so the following `hello` won't auto-enter;
          // PortraitStep triggers the enter when the player is ready.
          const det = ev.detail as { character_id?: number } | undefined;
          const id = det && typeof det.character_id === "number" ? det.character_id : null;
          setNewChar({ name: ev.name, id });
          setScreen("portrait");
          break;
        }
        case "cc_error":
          // On the portrait step this event means the *enter* failed, not CC —
          // surface it there (CreateFlow isn't mounted to show ccError).
          if (screenRef.current === "portrait") {
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
          setBlocks((b) => [...b, { kind: "player", text: ev.text, who: ev.who,
                                    secret: ev.secret }]);
          break;
        case "narration":
          setBlocks((b) => [...b, makeOracleBlock(ev.text, lexRef.current, ev.secret)]);
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
          if (!ev.scene) { setVttOptions(null); setVttError(null); setVttPreview(null); }
          break;
        case "vtt_options":
          setVttOptions({ token_id: ev.token_id, budget_ft: ev.budget_ft,
                          squares: ev.squares });
          break;
        case "vtt_preview":
          setVttPreview({ token_id: ev.token_id, ok: ev.ok, cost_ft: ev.cost_ft,
                          opportunity: ev.opportunity });
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
    }, channel, session.userId, session.username);
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

  return (
    <div className="table">
      <div className={`frame${screen === "play" ? " playing" : ""}`}>
        <Corner pos="tl" /><Corner pos="tr" /><Corner pos="bl" /><Corner pos="br" />

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
          />
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

        {screen === "portrait" && newChar && (
          <PortraitStep
            name={newChar.name}
            characterId={newChar.id}
            entering={entering}
            enterError={enterError}
            onDone={() => beginEnter({ character_name: newChar.name })}
          />
        )}

        {screen === "play" && (
          <>
            {arenaMode && arena && arena.run?.phase === "outfitting" && (
              <Quartermaster
                state={arena}
                onOutfit={(cart, equip) =>
                  connRef.current?.send({ t: "arena_outfit", cart, equip })}
              />
            )}
            {arenaMode && arena && (
              <ArenaResult
                state={arena}
                onAgain={() => connRef.current?.send({ t: "arena_fight" })}
                onElsewhere={(environment) =>
                  connRef.current?.send({ t: "arena_fight", environment })}
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
            <PlaySurface
              blocks={blocks}
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
              onVttOptions={(token_id, dash) =>
                connRef.current?.send({ t: "vtt_options", token_id, dash })}
              onVttPreview={(token_id, x, y) =>
                connRef.current?.send({ t: "vtt_preview", token_id, x, y })}
              onVttMove={(token_id, x, y) =>
                connRef.current?.send({ t: "vtt_move", token_id, x, y })}
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
