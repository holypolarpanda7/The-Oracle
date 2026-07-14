import { useEffect, useRef, useState } from "react";
import { connect, type Connection } from "./lib/connection";
import type {
  Ally, CCPayload, CharacterSummary, LevelUpData, LexEntry, ServerEvent,
  SheetData,
} from "./lib/types";
import { Block, makeOracleBlock, NarrationPane } from "./components/Narration";
import { CreateFlow } from "./components/CreateFlow";
import { Landing } from "./components/Landing";
import { LevelUpOverlay } from "./components/LevelUp";
import { PartyStrip, Rail } from "./components/Rail";
import { levelChime, rollThunk } from "./lib/sound";

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

function param(name: string, dflt: string): string {
  return new URLSearchParams(location.search).get(name) ?? dflt;
}

type Screen = "landing" | "create" | "play";

export default function App() {
  const [screen, setScreen] = useState<Screen>("landing");
  const [characters, setCharacters] = useState<CharacterSummary[]>([]);
  const [blocks, setBlocks] = useState<Block[]>([]);
  const [sheet, setSheet] = useState<SheetData | null>(null);
  const [party, setParty] = useState<Ally[]>([]);
  const [sceneUrl, setSceneUrl] = useState<string | null>(null);
  const [levelUp, setLevelUp] = useState<LevelUpData | null>(null);
  const [busy, setBusy] = useState(false);
  const [ccError, setCcError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const lexRef = useRef<LexEntry[]>([]);
  const connRef = useRef<Connection | null>(null);
  const pendingEnterRef = useRef<string | null>(null);
  const screenRef = useRef<Screen>("landing");
  screenRef.current = screen;

  useEffect(() => {
    const channel = param("channel", "1447775459533262868");
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
        case "cc_done":
          pendingEnterRef.current = ev.name;
          break;
        case "cc_error":
          setCcError(ev.detail);
          break;
        case "lexicon":
          lexRef.current = ev.entries;
          break;
        case "player":
          setBlocks((b) => [...b, { kind: "player", text: ev.text }]);
          break;
        case "narration":
          setBlocks((b) => [...b, makeOracleBlock(ev.text, lexRef.current)]);
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
        case "scene":
          setSceneUrl(ev.url);
          break;
        case "levelup":
          if (ev.data) levelChime();
          setLevelUp(ev.data);
          break;
        case "busy":
          setBusy(ev.on);
          break;
      }
    }, channel);
    connRef.current = conn;
    return () => conn.close();
  }, []);

  const submit = () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    connRef.current?.send({ t: "action", text });
  };

  const skipAll = () =>
    setBlocks((bs) => bs.map((b) => (b.kind === "oracle" ? { ...b, done: true } : b)));

  const markDone = (i: number) =>
    setBlocks((bs) => bs.map((b, j) => (j === i && b.kind === "oracle" ? { ...b, done: true } : b)));

  return (
    <div className="table">
      <div className="frame">
        <Corner pos="tl" /><Corner pos="tr" /><Corner pos="bl" /><Corner pos="br" />

        {screen === "landing" && (
          <Landing
            characters={characters}
            onEnter={(name) => connRef.current?.send({ t: "enter", character_name: name })}
            onCreate={() => { setCcError(null); setScreen("create"); }}
          />
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

        {screen === "play" && (
          <>
            {levelUp && (
              <LevelUpOverlay
                data={levelUp}
                onApply={(subclass) =>
                  connRef.current?.send({ t: "levelup_apply", subclass })}
              />
            )}
            <NarrationPane blocks={blocks} onBlockDone={markDone} onSkip={skipAll} />
            <Rail sheet={sheet} sceneUrl={sceneUrl} />
            <PartyStrip members={party} />
            <div className="input-bar">
              <input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && submit()}
                placeholder={busy ? "the Oracle is weaving…" : "What do you do?"}
                disabled={busy}
              />
              <button onClick={submit} disabled={busy || !input.trim()}>Act</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
