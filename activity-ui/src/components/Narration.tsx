import { useEffect, useRef, useState } from "react";
import type { LexEntry, RollResult } from "../lib/types";
import { markText, type Span } from "../lib/highlight";

export type Block =
  | { kind: "player"; text: string }
  | { kind: "oracle"; spans: Span[]; done: boolean }
  | { kind: "roll"; roll: RollResult };

export function makeOracleBlock(text: string, lexicon: LexEntry[]): Block {
  return { kind: "oracle", spans: markText(text, lexicon), done: false };
}

/** Reveal pacing: base cadence per character, with breath at punctuation. */
const TICK_MS = 16;
const CHARS_PER_TICK = 1.6;
const PUNCT_PAUSE: Record<string, number> = { ".": 9, "!": 9, "?": 9, ",": 4, ";": 5, "—": 6 };

function RevealedSpans({ spans, done, onDone }: {
  spans: Span[];
  done: boolean;
  onDone: () => void;
}) {
  const total = spans.reduce((n, s) => n + s.text.length, 0);
  const [shown, setShown] = useState(done ? total : 0);
  const pauseRef = useRef(0);

  useEffect(() => {
    if (done || shown >= total) {
      if (!done && shown >= total) onDone();
      return;
    }
    const id = setInterval(() => {
      setShown((n) => {
        if (pauseRef.current > 0) { pauseRef.current -= 1; return n; }
        let step = CHARS_PER_TICK;
        const next = Math.min(total, Math.floor(n + step));
        // breathe at punctuation just revealed
        let idx = 0;
        for (const s of spans) {
          if (next > idx && next <= idx + s.text.length) {
            const ch = s.text[next - idx - 1];
            if (ch && PUNCT_PAUSE[ch]) pauseRef.current = PUNCT_PAUSE[ch];
          }
          idx += s.text.length;
        }
        return next;
      });
    }, TICK_MS);
    return () => clearInterval(id);
  }, [done, shown >= total]);

  useEffect(() => { if (done) setShown(total); }, [done, total]);

  let remaining = shown;
  const out = [];
  for (let i = 0; i < spans.length && remaining > 0; i++) {
    const s = spans[i];
    const take = Math.min(remaining, s.text.length);
    out.push(
      s.cls
        ? <span key={i} className={s.cls}>{s.text.slice(0, take)}</span>
        : <span key={i}>{s.text.slice(0, take)}</span>,
    );
    remaining -= take;
  }
  const typing = shown < total;
  return (
    <>
      {out}
      {typing && <span className="caret" />}
    </>
  );
}

export function NarrationPane({ blocks, onBlockDone, onSkip }: {
  blocks: Block[];
  onBlockDone: (i: number) => void;
  onSkip: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  });

  return (
    <section className="pane narration" onClick={onSkip} title="Click to reveal instantly">
      <div className="pane-title">The Oracle Speaks</div>
      <div className="narration-scroll" ref={scrollRef}>
        {blocks.map((b, i) => {
          if (b.kind === "player") {
            return <p key={i} className="narration-block player">{b.text}</p>;
          }
          if (b.kind === "roll") {
            const r = b.roll;
            const cls =
              r.success === undefined ? "" : r.success ? "success" : "failure";
            return (
              <div key={i} className={`roll-card ${cls}`}>
                <span className="label">{r.label ?? "Roll"}</span>
                <span className="expr">{r.detail ?? r.expr}</span>
                <span className="total">{r.total}</span>
                {r.dc !== undefined && (
                  <span className="stamp">
                    DC {r.dc} · {r.success ? "success" : "failure"}
                  </span>
                )}
              </div>
            );
          }
          return (
            <p key={i} className="narration-block oracle">
              {i === 0 || blocks[i - 1]?.kind !== "oracle" ? (
                <span className="speaker">The Oracle</span>
              ) : null}
              <RevealedSpans
                spans={b.spans}
                done={b.done}
                onDone={() => onBlockDone(i)}
              />
            </p>
          );
        })}
      </div>
    </section>
  );
}
