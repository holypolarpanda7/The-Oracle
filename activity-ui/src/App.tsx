import { useEffect, useRef, useState } from "react";
import { connect, type Connection } from "./lib/connection";
import type {
  Ally, CCPayload, CharacterSummary, CombatState, LevelUpData, LexEntry,
  ServerEvent, SheetData,
} from "./lib/types";
import { Block, makeOracleBlock } from "./components/Narration";
import { CreateFlow } from "./components/CreateFlow";
import { PortraitStep } from "./components/PortraitStep";
import { Landing } from "./components/Landing";
import { LevelUpOverlay } from "./components/LevelUp";
import { PlaySurface } from "./components/PlaySurface";
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

type Screen = "landing" | "create" | "portrait" | "play";

export default function App({ session }: { session: Session }) {
  const [screen, setScreen] = useState<Screen>("landing");
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [sheet, setSheet] = useState<SheetData | null>(null);
  const [party, setParty] = useState<Ally[]>([]);
  const [combat, setCombat] = useState<CombatState | null>(null);
  const [sceneUrl, setSceneUrl] = useState<string | null>(null);
  const [levelUp, setLevelUp] = useState<LevelUpData | null>(null);
  const [busy, setBusy] = useState(false);
  const [ccError, setCcError] = useState<string | null>(null);
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
  const screenRef = useRef<Screen>("landing");
  screenRef.current = screen;

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
          setScreen("play");
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
          setCcError(ev.detail);
          break;
        case "join_blocked":
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
        case "party":
          setParty(ev.members);
          break;
        case "combat":
          setCombat(ev.encounter);
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
            v && v.name === ev.name
              ? { ...v, detail: { ...(v.detail ?? { name: ev.name }), image: ev.url } }
              : v);
          break;
        case "item_error":
          setItemView((v) => (v ? { ...v, loading: false, error: ev.detail } : v));
          break;
        case "item_gone":
          setItemView((v) => (v && v.name === ev.name ? null : v));
          break;
        case "levelup":
          if (ev.data) levelChime();
          setLevelUp(ev.data);
          break;
        case "busy":
          setBusy(ev.on);
          break;
      }
    }, channel, session.userId, session.username);
    connRef.current = conn;
    return () => conn.close();
  }, []);

  const submit = (secret?: boolean) => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
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
  const portraitAction = (
    action: "regear" | "select" | "delete",
    opts?: { context?: string; replace_context?: string; detail?: string },
  ) => {
    connRef.current?.send({ t: "portrait_action", action, ...opts });
  };

  const skipAll = () =>
    setBlocks((bs) => bs.map((b) => (b.kind === "oracle" ? { ...b, done: true } : b)));

  const markDone = (i: number) =>
    setBlocks((bs) => bs.map((b, j) => (j === i && b.kind === "oracle" ? { ...b, done: true } : b)));

  return (
    <div className="table">
      <div className={`frame${screen === "play" ? " playing" : ""}`}>
        <Corner pos="tl" /><Corner pos="tr" /><Corner pos="bl" /><Corner pos="br" />

        {screen === "landing" && (
          <Landing
            characters={characters}
            onEnter={(name) => {
              lastEnterRef.current = name;
              connRef.current?.send({ t: "enter", character_name: name });
            }}
            onCreate={() => { setCcError(null); setScreen("create"); }}
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
                      connRef.current?.send({
                        t: "enter", character_name: notice.charName, solo: true });
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
            onCancel={() => setScreen("landing")}
            onDone={(payload: CCPayload) => {
              setCcError(null);
              connRef.current?.send({ t: "cc_register", payload });
            }}
          />
        )}

        {screen === "portrait" && newChar && (
          <PortraitStep
            name={newChar.name}
            characterId={newChar.id}
            onDone={() => {
              lastEnterRef.current = newChar.name;
              connRef.current?.send({ t: "enter", character_name: newChar.name });
            }}
          />
        )}

        {screen === "play" && (
          <>
            {levelUp && (
              <LevelUpOverlay
                data={levelUp}
                onApply={(subclass) =>
                  connRef.current?.send({ t: "levelup_apply", subclass })}
              />
            )}
            <PlaySurface
              blocks={blocks}
              sheet={sheet}
              sceneUrl={sceneUrl}
              party={party}
              combat={combat}
              input={input}
              setInput={setInput}
              submit={submit}
              busy={busy}
              rateWait={rateWait}
              onSkip={skipAll}
              onBlockDone={markDone}
              onMainMenu={() => setScreen("landing")}
              onInspect={inspectItem}
              onPortrait={portraitAction}
            />
            <ItemInspector
              view={itemView}
              onClose={() => setItemView(null)}
              onInscribe={inscribeSpell}
              onAction={itemAction}
              inventory={sheet?.inventory.map((it) => (typeof it === "string" ? it : it.name))}
            />
          </>
        )}
      </div>
    </div>
  );
}
