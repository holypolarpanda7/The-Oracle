import { useState } from "react";
import type { LevelUpData } from "../lib/types";

export function LevelUpOverlay({ data, onApply }: {
  data: LevelUpData;
  onApply: (subclass?: string) => void;
}) {
  const [picked, setPicked] = useState<string | null>(null);
  const needsPick = !!data.subclass_required;
  const canConfirm = !needsPick || picked !== null;

  return (
    <div className="levelup-veil">
      <div className="levelup">
        <div className="levelup-head">
          <span className="lu-title">Level Up</span>
          <span className="lu-arc">
            {data.class} {data.current_level} <span className="lu-arrow">➤</span>{" "}
            {data.next_level}
            {data.subclass ? ` · ${data.subclass}` : ""}
          </span>
        </div>

        <ul className="lu-notes">
          {data.notes.map((n, i) => <li key={i}>{n}</li>)}
          {data.class_features.map((f, i) => (
            <li key={`cf${i}`}>
              <b className="hl-name">{f.name}</b>
              {f.summary ? ` — ${f.summary.slice(0, 180)}${f.summary.length > 180 ? "…" : ""}` : ""}
            </li>
          ))}
        </ul>

        {needsPick && (
          <>
            <div className="lu-pick-label">
              Choose your {data.subclass_label || "subclass"}
            </div>
            <div className="lu-options">
              {data.subclass_options.map((o) => (
                <button
                  key={o.slug}
                  className={`lu-option ${picked === o.slug ? "picked" : ""}`}
                  onClick={() => setPicked(o.slug)}
                >
                  <div className="lu-opt-name">{o.name}</div>
                  {o.source?.includes("2024") && <div className="lu-opt-tag">PHB 2024</div>}
                  <div className="lu-opt-feats">
                    {(o.features || []).slice(0, 3).map((f) => f.name).join(" · ")}
                  </div>
                </button>
              ))}
            </div>
          </>
        )}

        <div className="lu-actions">
          <button
            className="lu-confirm"
            disabled={!canConfirm}
            onClick={() => onApply(picked ?? undefined)}
          >
            {needsPick && !picked ? "choose above…" : "Take the level"}
          </button>
        </div>
      </div>
    </div>
  );
}
