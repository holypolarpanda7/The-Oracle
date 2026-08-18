import { useState } from "react";
import { uiTick } from "../lib/sound";

/** Who the likeness is OF, before there is anybody to be of.
 *
 *  The face is chosen during creation rather than after it, so the render has
 *  no character id to hang on — only the draft the wizard is holding. `token`
 *  is that draft's identity: the server files the picture under it and
 *  `register_character` adopts it when the character is sealed. The rest is
 *  what the prompt is built from, and must be the SAME strings the payload will
 *  send, or the sealed character's later renders are of somebody else. */
export interface PortraitDraft {
  token: string;
  race: string;
  char_class: string;
  gender?: string;
}

/** The likeness stage: summon a face from a description, or bring your own.
 *
 *  Two modes, one screen. Against a DRAFT (creation, before the seal) it draws
 *  through `/cc/portrait/draft` and hands the result back to the wizard, which
 *  carries the token in the payload. Against a sealed character it draws
 *  through that character's own endpoints and offers the way into the world.
 *  Fully skippable either way — a portrait can be set in-world later, and it is
 *  optional if the imagery backend is offline. */
export function PortraitStep({ name, characterId, draft, initial, onDone,
                               onChange, entering, enterError }: {
  name: string;
  /** The sealed character to draw against. Null in draft mode. */
  characterId?: number | null;
  /** The unsealed draft to draw against. Set = creation, before the seal. */
  draft?: PortraitDraft;
  /** What the stage was left holding, so stepping back does not lose the face. */
  initial?: { image: string | null; description: string };
  onDone: () => void;
  /** Draft mode: report the face upward so the wizard can show and send it. */
  onChange?: (v: { image: string | null; description: string }) => void;
  /** True while the world-entry round-trip is in flight (LLM intro + scene). */
  entering?: boolean;
  /** Set if the entry attempt failed — shown with the buttons still usable. */
  enterError?: string | null;
}) {
  const [desc, setDesc] = useState(initial?.description ?? "");
  const [preview, setPreview] = useState<string | null>(initial?.image ?? null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const drafting = !!draft;
  const canCall = drafting || characterId != null;

  function keep(image: string | null, description: string) {
    setPreview(image);
    onChange?.({ image, description });
  }

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
      const p = draft
        ? await post(`/cc/portrait/draft`, {
            token: draft.token, race: draft.race, char_class: draft.char_class,
            gender: draft.gender ?? "", description: desc.trim(),
          })
        : await post(`/character/${characterId}/portrait/generate`, {
            character_id: characterId, description: desc.trim(),
          });
      keep(`data:${p.mime || "image/webp"};base64,${p.b64}`, desc.trim());
    } catch (e) {
      setErr(msg(e, "The vision would not take shape. Try again, or skip."));
    } finally { setBusy(false); }
  }

  async function upload(file: File) {
    if (!canCall || busy) return;
    uiTick(); setErr(null); setBusy(true);
    try {
      const b64 = await fileToB64(file);
      const p = draft
        ? await post(`/cc/portrait/draft/upload`, {
            token: draft.token, b64, caption: `${name || "draft"} (portrait)`,
          })
        : await post(`/character/${characterId}/portrait/upload`, {
            character_id: characterId, b64, caption: `${name} (portrait)`,
          });
      keep(`data:${p.mime || "image/webp"};base64,${p.b64}`, desc.trim());
    } catch (e) {
      setErr(msg(e, "That image couldn't be used. Try another, or skip."));
    } finally { setBusy(false); }
  }

  return (
    <div className={`create portrait-step ${drafting ? "ps-inline" : ""}`}>
      <div className="ps-head">
        <h2>{name ? `The face of ${name}` : "The face you will wear"}</h2>
        <p className="ps-sub">
          {drafting
            ? "Summon a likeness from a description, or bring your own. It is "
              + "kept with the character when you seal them — and you can skip "
              + "it and set one in-world later."
            : "Summon a likeness from a description, or bring your own. You can "
              + "also skip and set one later, in-world."}
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
            onChange={(e) => {
              setDesc(e.target.value);
              onChange?.({ image: preview, description: e.target.value });
            }}
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
          {drafting && (
            <p className="ps-note">
              Your words are kept whether or not a picture is drawn — they are
              what every later likeness of this character is built from.
            </p>
          )}
          {err && <p className="cf-error">⚠ {err}</p>}
        </div>
      </div>

      {enterError && (
        <p className="cf-error ps-enter-err">
          ⚠ {enterError} — the Oracle stumbled. Try entering again.
        </p>
      )}

      {/* In draft mode the wizard's own footer walks the stages; a second pair
          of buttons here would be a second way forward that skips it. */}
      {!drafting && (
        <footer className="cf-foot ps-foot">
          <button className="cf-cancel" disabled={entering || busy}
                  onClick={() => { uiTick(); onDone(); }}>
            Skip for now
          </button>
          <button className="lu-confirm" disabled={entering || busy}
                  onClick={() => { uiTick(); onDone(); }}>
            {entering ? "Entering the world…" : "Enter the world ➤"}
          </button>
        </footer>
      )}
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
