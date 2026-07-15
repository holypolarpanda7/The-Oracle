import type { ClientEvent, ServerEvent } from "./types";
import { demoScript } from "./demo";

export interface Connection {
  send(ev: ClientEvent): void;
  close(): void;
}

/** Connect to the backend session socket; if unreachable, fall back to the
    scripted demo feed so the UI is explorable standalone. */
export function connect(
  onEvent: (ev: ServerEvent) => void,
  channel: string,
  userId: string,
  username: string,
): Connection {
  let demo = false;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const q = new URLSearchParams({ user_id: userId, username });
  const ws = new WebSocket(
    `${proto}://${location.host}/ws/activity/${channel}?${q}`);
  let opened = false;

  ws.onopen = () => { opened = true; };
  ws.onmessage = (m) => {
    try {
      onEvent(JSON.parse(m.data) as ServerEvent);
    } catch { /* ignore malformed frames */ }
  };
  ws.onerror = ws.onclose = () => {
    if (!opened && !demo) {
      demo = true;
      runDemo(onEvent);
    }
  };

  return {
    send(ev) {
      if (demo) {
        demoRespond(ev, onEvent);
      } else if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify(ev));
      }
    },
    close() { ws.close(); },
  };
}

function runDemo(onEvent: (ev: ServerEvent) => void) {
  setTimeout(() => onEvent(demoScript.hello), 150);
}

function demoRespond(ev: ClientEvent, onEvent: (ev: ServerEvent) => void) {
  if (ev.t === "levelup_apply") {
    onEvent({ t: "levelup", data: null });
    onEvent({
      t: "narration",
      text: "Kara rises to level 3 — new strength settles into old scars.",
    });
    return;
  }
  if (ev.t === "cc_register") {
    onEvent({ t: "cc_done", name: ev.payload.name });
    onEvent({ t: "hello", channel: "demo", characters: [
      ...demoScript.hello.characters,
      { id: 99, name: ev.payload.name, race: ev.payload.race,
        char_class: ev.payload.char_class, level: 1, alive: true },
    ] });
    return;
  }
  if (ev.t === "enter") {
    onEvent({ t: "entered", resumed: false });
    let delay = 300;
    for (const e of demoScript.opening) {
      setTimeout(() => onEvent(e), delay);
      delay += e.t === "narration" ? 400 : 120;
    }
    return;
  }
  if (ev.t !== "action") return;
  onEvent({ t: "player", text: ev.text });
  onEvent({ t: "busy", on: true });
  let delay = 700;
  for (const e of demoScript.respond(ev.text)) {
    setTimeout(() => onEvent(e), delay);
    delay += e.t === "narration" ? 350 : 150;
  }
  setTimeout(() => onEvent({ t: "busy", on: false }), delay);
}
