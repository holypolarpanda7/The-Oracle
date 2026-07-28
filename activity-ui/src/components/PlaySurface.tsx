import { useEffect, useRef, useState } from "react";
import { Frame } from "./Frame";
import { CharacterSheet } from "./CharacterSheet";
import { IconDefs } from "./icons";
import { RevealedSpans, type Block } from "./Narration";
import { useResizable, resetAllPanels } from "../lib/useResizable";
import { InitiativeCarousel } from "./InitiativeCarousel";
import { VttOverlay } from "./VttOverlay";
import type {
  Ally, CombatState, SheetData, VttOptions, VttScene,
} from "../lib/types";

const SCROLL = "/assets/scrolls/parchment.webp";

function hpMood(hp: number, max: number): string {
  const f = hp / Math.max(1, max);
  return f <= 0.25 ? "dire" : f <= 0.6 ? "hurt" : "";
}

function renderBlock(b: Block, i: number, onBlockDone: (i: number) => void) {
  if (b.kind === "player") {
    return (
      <p className={`player${b.secret ? " secret" : ""}`} key={i}>
        {b.secret && <span className="secret-tag">🔒 </span>}
        {b.who && <span className="hl-name">{b.who} · </span>}{b.text}
      </p>
    );
  }
  if (b.kind === "whisper") {
    return (
      <p className="whisper" key={i}>
        <span className="secret-tag">🤫 </span>{b.text}
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
    <p key={i} className={b.secret ? "secret" : undefined}>
      {b.secret && <span className="secret-tag">🔒 to you · </span>}
      <RevealedSpans spans={b.spans} done={b.done} onDone={() => onBlockDone(i)} />
    </p>
  );
}

export interface PlayProps {
  blocks: Block[];
  sheet: SheetData | null;
  sceneUrl: string | null;
  party: Ally[];
  combat: CombatState | null;
  /** The tactical board — non-null only while the Oracle has one out. */
  vtt: VttScene | null;
  vttOptions: VttOptions | null;
  vttPing: { x: number; y: number; label?: string; at: number } | null;
  vttPreview: { token_id: number; ok: boolean; cost_ft?: number;
                opportunity?: string[] } | null;
  vttError: string | null;
  onVttOptions: (tokenId: number, dash: boolean) => void;
  onVttPreview: (tokenId: number, x: number, y: number) => void;
  onVttMove: (tokenId: number, x: number, y: number) => void;
  onVttPing: (x: number, y: number) => void;
  onVttDismissError: () => void;
  input: string;
  setInput: (v: string) => void;
  submit: (secret?: boolean) => void;
  busy: boolean;
  rateWait: number;
  onSkip: () => void;
  onBlockDone: (i: number) => void;
  onMainMenu: () => void;
  onInspect: (name: string) => void;
  onPortrait: (action: "regear" | "select" | "delete",
               opts?: { context?: string; replace_context?: string; detail?: string }) => void;
  onSetDnr: (dnr: boolean) => void;
  onReprepare: () => void;
}

export function PlaySurface(p: PlayProps) {
  const scene = useResizable("scene", { minW: 280, minH: 160 });
  const scroll = useResizable("scroll", { minW: 300, minH: 150, fillImg: true });
  const sheetR = useResizable("sheet", { minW: 260, minH: 320 });
  const txtRef = useRef<HTMLDivElement>(null);
  // "Secret" input: the action + the Oracle's answer stay private to you — your
  // tablemates never see it (cheat, lie, a hidden roll).
  const [secret, setSecret] = useState(false);
  const send = () => { p.submit(secret); setSecret(false); };

  useEffect(() => {
    const el = txtRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  });

  return (
    <div className="play">
      <IconDefs />
      {p.combat && <InitiativeCarousel combat={p.combat} />}
      <div className="play-surface">
        <div className="stage">
          {/* While a board is out it IS the picture of the moment; the rendered
              scene art returns the instant the Oracle puts the grid away. */}
          {p.vtt ? (
            <VttOverlay
              scene={p.vtt}
              combat={p.combat}
              myCharacterId={p.sheet?.character_id ?? null}
              options={p.vttOptions}
              preview={p.vttPreview}
              ping={p.vttPing}
              error={p.vttError}
              onRequestOptions={p.onVttOptions}
              onPreviewPath={p.onVttPreview}
              onMove={p.onVttMove}
              onPing={p.onVttPing}
              onDismissError={p.onVttDismissError}
            />
          ) : (
            <Frame className="scene" panel={scene}>
              <div className="in">{p.sceneUrl && <img src={p.sceneUrl} alt="Scene" />}</div>
              <span className="tag">Scene{p.sceneUrl ? " · rendered" : ""}</span>
            </Frame>
          )}

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

          <div className={`promptbar${secret ? " secret" : ""}`}>
            <button
              className={`psecret${secret ? " on" : ""}`}
              onClick={() => setSecret((s) => !s)}
              title={secret ? "Secret: only you will see this" : "Act in secret (hidden from your table)"}
              aria-pressed={secret}
            >{secret ? "🔒" : "🎭"}</button>
            <input
              value={p.input}
              onChange={(e) => p.setInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && send()}
              placeholder={
                p.rateWait > 0 ? `the table needs a breath — ${p.rateWait}s…`
                  : secret ? "whisper a secret deed — only you will see the Oracle's reply…"
                  : p.busy ? "the Oracle is weaving…"
                  : "Speak your deed, and the Oracle shall answer…"
              }
              disabled={p.busy || p.rateWait > 0}
            />
            <button className="psend" onClick={send} disabled={p.busy || !p.input.trim()} aria-label="Send">➤</button>
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
          <CharacterSheet sheet={p.sheet} panel={sheetR} onInspect={p.onInspect}
                          onPortrait={p.onPortrait} onSetDnr={p.onSetDnr}
                          onReprepare={p.onReprepare} />
          <div className="menu">
            <button className="mbtn" onClick={resetAllPanels}>⟲ Reset Layout</button>
            <button className="mbtn" onClick={p.onMainMenu}>☰ Main Menu</button>
          </div>
        </aside>
      </div>
    </div>
  );
}
