import { useState } from "react";
import type { CharacterSummary } from "../lib/types";
import { setSoundEnabled, soundEnabled, uiTick } from "../lib/sound";

export function Landing({ characters, onEnter, onCreate }: {
  characters: CharacterSummary[];
  onEnter: (name: string) => void;
  onCreate: () => void;
}) {
  const [sound, setSound] = useState(soundEnabled());
  const living = characters.filter((c) => c.alive);
  const fallen = characters.filter((c) => !c.alive);

  return (
    <div className="landing">
      <div className="landing-title">
        <div className="lt-rule" />
        <h1>The Oracle</h1>
        <p className="lt-sub">a living world, remembered</p>
        <div className="lt-rule" />
      </div>

      {living.length > 0 && (
        <div className="landing-chars">
          {living.map((c) => (
            <button
              key={c.id}
              className="char-card"
              onClick={() => { uiTick(); onEnter(c.name); }}
            >
              <div className="cc-name">{c.name}</div>
              <div className="cc-sub">
                Level {c.level} {c.char_class}
                {c.subclass ? ` (${c.subclass})` : ""}
                {c.race ? ` · ${c.race}` : ""}
              </div>
              <div className="cc-go">
                {c.resume_session ? "Resume the tale ➤" : "Begin the tale ➤"}
              </div>
            </button>
          ))}
        </div>
      )}

      <button className="landing-create" onClick={() => { uiTick(); onCreate(); }}>
        ⚒ Forge a new character
      </button>

      {fallen.length > 0 && (
        <p className="landing-fallen">
          In memoriam: {fallen.map((c) => c.name).join(", ")}
        </p>
      )}

      <label className="landing-sound">
        <input
          type="checkbox"
          checked={sound}
          onChange={(e) => {
            setSoundEnabled(e.target.checked);
            setSound(e.target.checked);
            if (e.target.checked) uiTick();
          }}
        />
        <span>{sound ? "🔊" : "🔇"} table sounds</span>
      </label>
    </div>
  );
}
