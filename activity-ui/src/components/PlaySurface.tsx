import { useEffect, useRef } from "react";
import { Frame } from "./Frame";
import { CharacterSheet } from "./CharacterSheet";
import { IconDefs } from "./icons";
import { RevealedSpans, type Block } from "./Narration";
import { useResizable, resetAllPanels } from "../lib/useResizable";
import type { Ally, SheetData } from "../lib/types";

const SCROLL = "/assets/scrolls/parchment.webp";

function hpMood(hp: number, max: number): string {
  const f = hp / Math.max(1, max);
  return f <= 0.25 ? "dire" : f <= 0.6 ? "hurt" : "";
}

function renderBlock(b: Block, i: number, onBlockDone: (i: number) => void) {
  if (b.kind === "player") {
    return (
      <p className="player" key={i}>
        {b.who && <span className="hl-name">{b.who} · </span>}{b.text}
      </p>
    );
  }
  if (b.kind === "roll") {
    const r = b.roll;
    const fail = r.success === false;
    return (
      <div className={`roll ${fail ? "failure" : ""}`} key={i}>
        <span className="die">{r.total}</span>
        <span className="rmeta">
          <b>{r.label ?? "Roll"}</b> {r.detail ?? r.expr}
          {r.dc !== undefined && <> · {r.success ? "success" : "failure"}</>}
        </span>
      </div>
    );
  }
  return (
    <p key={i}>
      <RevealedSpans spans={b.spans} done={b.done} onDone={() => onBlockDone(i)} />
    </p>
  );
}

export interface PlayProps {
  blocks: Block[];
  sheet: SheetData | null;
  sceneUrl: string | null;
  party: Ally[];
  input: string;
  setInput: (v: string) => void;
  submit: () => void;
  busy: boolean;
  rateWait: number;
  onSkip: () => void;
  onBlockDone: (i: number) => void;
  onMainMenu: () => void;
  onInspect: (name: string) => void;
}

export function PlaySurface(p: PlayProps) {
  const scene = useResizable("scene", { minW: 280, minH: 160 });
  const scroll = useResizable("scroll", { minW: 300, minH: 150, fillImg: true });
  const sheetR = useResizable("sheet", { minW: 260, minH: 320 });
  const txtRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = txtRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  });

  return (
    <div className="play">
      <IconDefs />
      <div className="play-surface">
        <div className="stage">
          <Frame className="scene" panel={scene}>
            <div className="in">{p.sceneUrl && <img src={p.sceneUrl} alt="Scene" />}</div>
            <span className="tag">Scene{p.sceneUrl ? " · rendered" : ""}</span>
          </Frame>

          <div className="scroll" ref={scroll.ref}>
            <img src={SCROLL} alt="" />
            <div className="txt" ref={txtRef} onClick={p.onSkip} title="Click to reveal instantly">
              <div className="who">The Oracle Speaks</div>
              {p.blocks.length
                ? p.blocks.map((b, i) => renderBlock(b, i, p.onBlockDone))
                : <p style={{ color: "#7a5e2a", fontStyle: "italic" }}>The tale awaits your first deed…</p>}
            </div>
            <div className="grip" title="Drag to resize" onPointerDown={scroll.onGripDown} />
          </div>

          <div className="promptbar">
            <input
              value={p.input}
              onChange={(e) => p.setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && p.submit()}
              placeholder={
                p.rateWait > 0 ? `the table needs a breath — ${p.rateWait}s…`
                  : p.busy ? "the Oracle is weaving…"
                  : "Speak your deed, and the Oracle shall answer…"
              }
              disabled={p.busy || p.rateWait > 0}
            />
            <button className="psend" onClick={p.submit} disabled={p.busy || !p.input.trim()} aria-label="Send">➤</button>
          </div>

          {p.party.length > 0 && (
            <div className="party">
              {p.party.map((a) => (
                <div className="ally" key={a.name}>
                  <div className="nm">{a.name}</div>
                  <div className={`abar ${hpMood(a.hp, a.hp_max)}`}>
                    <span style={{ width: `${(100 * a.hp) / Math.max(1, a.hp_max)}%` }} />
                  </div>
                  {a.condition && <div className="cond">{a.condition}</div>}
                </div>
              ))}
            </div>
          )}
        </div>

        <aside>
          <CharacterSheet sheet={p.sheet} panel={sheetR} onInspect={p.onInspect} />
          <div className="menu">
            <button className="mbtn" onClick={resetAllPanels}>⟲ Reset Layout</button>
            <button className="mbtn" onClick={p.onMainMenu}>☰ Main Menu</button>
          </div>
        </aside>
      </div>
    </div>
  );
}
