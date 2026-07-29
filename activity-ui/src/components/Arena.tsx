import { useEffect, useState } from "react";
import type { ArenaEnv, ArenaSlot, ArenaState } from "../lib/types";
import { uiTick } from "../lib/sound";

const DOMAIN_LABEL: Record<string, string> = {
  land: "On land", sea: "At sea", air: "In the air",
};
const DOMAIN_ICON: Record<string, string> = { land: "⛰", sea: "🌊", air: "☁" };
const MODE_TAG: Record<string, string> = { swim: "swim", fly: "fly", walk: "" };

/** The Proving Grounds: pick a fighter, a place, and a level, then fight.
 *  Three slots, overwritable — practice characters are meant to be spent. */
export function Arena({ state, onCreate, onDelete, onBegin, onBack }: {
  state: ArenaState | null;
  onCreate: (slot: number) => void;
  onDelete: (slot: number) => void;
  onBegin: (opts: { slot: number; environment: string; level: number;
                    difficulty: string; reuse: boolean }) => void;
  onBack: () => void;
}) {
  const [slot, setSlot] = useState<number | null>(null);
  const [env, setEnv] = useState<string>("");
  const [level, setLevel] = useState(1);
  const [difficulty, setDifficulty] = useState("medium");
  const [reuse, setReuse] = useState(false);

  const slots = state?.slots ?? [];
  const chosen: ArenaSlot | undefined = slots.find((s) => s.slot === slot);
  const envs = state?.environments ?? [];
  const byDomain = envs.reduce<Record<string, ArenaEnv[]>>((acc, e) => {
    (acc[e.domain] ||= []).push(e);
    return acc;
  }, {});

  // A slot emptied under us (deleted, overwritten) must not stay selected.
  useEffect(() => {
    if (slot !== null && chosen && !chosen.character) setSlot(null);
  }, [slot, chosen]);

  // "Fight on" is only on offer while the leveled copy matches the chosen level.
  const canReuse = !!chosen?.leveled && chosen.leveled.level === level;
  useEffect(() => { if (!canReuse) setReuse(false); }, [canReuse]);

  if (!state) {
    return (
      <div className="arena">
        <div className="arena-head"><h2>The Proving Grounds</h2></div>
        <p className="arena-sub">Opening the gate…</p>
      </div>
    );
  }

  return (
    <div className="arena">
      <div className="arena-head">
        <h2>The Proving Grounds</h2>
        <p className="arena-sub">
          Practice bouts outside the world. Nothing here is remembered —
          fight, fall, and stand up whole.
        </p>
      </div>

      {/* ---- 1. who fights ---- */}
      <div className="arena-step">
        <div className="arena-step-label">① Your fighters</div>
        <div className="arena-slots">
          {slots.map((s) => (
            <div
              key={s.slot}
              className={`arena-slot${slot === s.slot ? " picked" : ""}${
                s.character ? "" : " empty"}`}
              onClick={() => { if (s.character) { uiTick(); setSlot(s.slot); } }}
            >
              <div className="as-num">Slot {s.slot}</div>
              {s.character ? (
                <>
                  <div className="as-name">{s.character.name}</div>
                  <div className="as-sub">
                    Level {s.character.level} {s.character.char_class}
                    {s.character.race ? ` · ${s.character.race}` : ""}
                  </div>
                  {s.leveled && (
                    <div className="as-leveled">
                      advanced copy: level {s.leveled.level}
                    </div>
                  )}
                  <div className="as-actions">
                    <button onClick={(e) => { e.stopPropagation(); uiTick(); onCreate(s.slot); }}>
                      Replace
                    </button>
                    <button onClick={(e) => { e.stopPropagation(); uiTick(); onDelete(s.slot); }}>
                      Clear
                    </button>
                  </div>
                </>
              ) : (
                <button className="as-forge"
                        onClick={() => { uiTick(); onCreate(s.slot); }}>
                  ⚒ Forge a fighter
                </button>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ---- 2. where ---- */}
      <div className={`arena-step${slot === null ? " dim" : ""}`}>
        <div className="arena-step-label">② Where you fight</div>
        {Object.entries(byDomain).map(([domain, list]) => (
          <div key={domain} className="arena-domain">
            <div className="ad-label">
              {DOMAIN_ICON[domain]} {DOMAIN_LABEL[domain] ?? domain}
            </div>
            <div className="arena-envs">
              {list.map((e) => (
                <button
                  key={e.slug}
                  className={`arena-env${env === e.slug ? " picked" : ""}`}
                  onClick={() => { uiTick(); setEnv(e.slug); }}
                >
                  <div className="ae-name">
                    {e.name}
                    {MODE_TAG[e.mode] && <span className="ae-mode">{MODE_TAG[e.mode]}</span>}
                  </div>
                  <div className="ae-blurb">{e.blurb}</div>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* ---- 3. how hard ---- */}
      <div className={`arena-step${slot === null || !env ? " dim" : ""}`}>
        <div className="arena-step-label">③ At what strength</div>
        <div className="arena-dials">
          <label className="arena-dial">
            <span>Fight at level</span>
            <input
              type="range" min={1} max={state.max_level} value={level}
              onChange={(ev) => setLevel(Number(ev.target.value))}
            />
            <b>{level}</b>
          </label>
          <div className="arena-diffs">
            {state.difficulties.map((d) => (
              <button
                key={d}
                className={`arena-diff${difficulty === d ? " picked" : ""}`}
                onClick={() => { uiTick(); setDifficulty(d); }}
              >{d}</button>
            ))}
          </div>
        </div>
        <p className="arena-note">
          {level > 1 && !reuse
            ? `You'll make every level-up choice from 1 to ${level} before the gate opens.`
            : "The bout opens as soon as you step through."}
        </p>
        {canReuse && (
          <label className="arena-reuse">
            <input type="checkbox" checked={reuse}
                   onChange={(e) => setReuse(e.target.checked)} />
            <span>
              Fight on with the copy already advanced to level {level} —
              skip the level-up choices
            </span>
          </label>
        )}
      </div>

      <div className="arena-go">
        <button className="lu-confirm" onClick={onBack}>Back</button>
        <button
          className="lu-confirm"
          disabled={slot === null || !env}
          onClick={() => {
            uiTick();
            if (slot === null || !env) return;
            onBegin({ slot, environment: env, level, difficulty, reuse });
          }}
        >Step through the gate ➤</button>
      </div>
    </div>
  );
}

/** Shown over the play surface once a bout is decided: fight on, or walk out. */
export function ArenaResult({ state, onAgain, onElsewhere, onLeave }: {
  state: ArenaState;
  onAgain: () => void;
  onElsewhere: (environment: string) => void;
  onLeave: () => void;
}) {
  const run = state.run;
  const [picking, setPicking] = useState(false);
  if (!run || run.phase !== "resolved") return null;
  const here = state.environments.find((e) => e.slug === run.environment);
  const won = run.result === "victory";

  return (
    <div className="levelup-veil">
      <div className="levelup arena-result">
        <div className="levelup-head">
          <span className="lu-title">{won ? "The Ward Dims" : "The Ward Catches You"}</span>
          <span className="lu-arc">{run.roster}</span>
        </div>
        <p className="arena-tally">
          {won ? "Every one of them is down." : "You went down first."}
          {" "}Bout {run.fights} in {here?.name ?? "the Grounds"} —
          {" "}{run.wins ?? 0} won of {run.fights ?? 0}.
        </p>

        {picking ? (
          <div className="arena-envs">
            {state.environments.map((e) => (
              <button key={e.slug} className="arena-env"
                      onClick={() => { uiTick(); onElsewhere(e.slug); }}>
                <div className="ae-name">{e.name}</div>
                <div className="ae-blurb">{e.blurb}</div>
              </button>
            ))}
          </div>
        ) : (
          <div className="lu-actions" style={{ gap: 10 }}>
            <button className="lu-confirm" onClick={() => { uiTick(); onAgain(); }}>
              Again, here
            </button>
            <button className="lu-confirm" onClick={() => { uiTick(); setPicking(true); }}>
              Somewhere else
            </button>
            <button className="lu-confirm" onClick={() => { uiTick(); onLeave(); }}>
              Leave the Grounds
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
