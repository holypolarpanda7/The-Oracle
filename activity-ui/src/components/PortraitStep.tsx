import { useState } from "react";
import { uiTick } from "../lib/sound";

/** Post-creation portrait step: summon a likeness from a description (diffusion)
 *  or upload one, then enter the world. Fully skippable — a portrait can be set
 *  in-world later, and it's optional if the imagery backend is offline. */
export function PortraitStep({ name, characterId, onDone }: {
  name: string;
  characterId: number | null;
  onDone: () => void;
}) {
  const [desc, setDesc] = useState("");
  const [preview, setPreview] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const canCall = characterId != null;

  async function post(path: string, body: unknown) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const detail = await r.json().catch(() => null);
      throw new Error((detail && detail.detail) || `HTTP ${r.status}`);
    }
    return r.json();
  }

  async function generate() {
    if (!canCall || busy) return;
    uiTick(); setErr(null); setBusy(true);
    try {
      const p = await post(`/character/${characterId}/portrait/generate`, {
        character_id: characterId, description: desc.trim(),
      });
      setPreview(`data:${p.mime || "image/webp"};base64,${p.b64}`);
    } catch (e) {
      setErr(msg(e, "The vision would not take shape. Try again, or skip."));
    } finally { setBusy(false); }
  }

  async function upload(file: File) {
    if (!canCall || busy) return;
    uiTick(); setErr(null); setBusy(true);
    try {
      const b64 = await fileToB64(file);
      const p = await post(`/character/${characterId}/portrait/upload`, {
        character_id: characterId, b64, caption: `${name} (portrait)`,
      });
      setPreview(`data:${p.mime || "image/webp"};base64,${p.b64}`);
    } catch (e) {
      setErr(msg(e, "That image couldn't be used. Try another, or skip."));
    } finally { setBusy(false); }
  }

  return (
    <div className="create portrait-step">
      <div className="ps-head">
        <h2>The face of {name}</h2>
        <p className="ps-sub">
          Summon a likeness from a description, or bring your own. You can also
          skip and set one later, in-world.
        </p>
      </div>

      <div className="ps-body">
        <div className="ps-frame">
          {preview
            ? <img src={preview} alt={`${name} portrait`} />
            : busy
              ? <div className="ps-spin">the ink takes shape…</div>
              : <div className="ps-empty">no likeness yet</div>}
        </div>

        <div className="ps-controls">
          <textarea
            className="ps-desc"
            placeholder="weathered half-elf ranger, green cloak, a scar over one brow, wary eyes…"
            value={desc}
            maxLength={300}
            disabled={!canCall || busy}
            onChange={(e) => setDesc(e.target.value)}
          />
          <div className="ps-actions">
            <button className="lu-confirm" disabled={!canCall || busy} onClick={generate}>
              {busy ? "Summoning…" : preview ? "Summon another" : "🔮 Summon portrait"}
            </button>
            <label className={`ps-upload ${!canCall || busy ? "disabled" : ""}`}>
              Upload an image
              <input
                type="file" accept="image/*" hidden
                disabled={!canCall || busy}
                onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }}
              />
            </label>
          </div>
          {!canCall && (
            <p className="ps-note">Portraits need a live backend — you can set one in-world later.</p>
          )}
          {err && <p className="cf-error">⚠ {err}</p>}
        </div>
      </div>

      <footer className="cf-foot ps-foot">
        <button className="cf-cancel" onClick={() => { uiTick(); onDone(); }}>
          Skip for now
        </button>
        <button className="lu-confirm" onClick={() => { uiTick(); onDone(); }}>
          Enter the world ➤
        </button>
      </footer>
    </div>
  );
}

function msg(e: unknown, fallback: string): string {
  return e instanceof Error && e.message ? e.message : fallback;
}

async function fileToB64(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}
