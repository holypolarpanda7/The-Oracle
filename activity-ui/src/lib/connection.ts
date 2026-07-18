import type { ClientEvent, ItemDetail, ServerEvent } from "./types";
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

const demoSpells = [
  { name: "Mage Hand", level: 0 }, { name: "Detect Magic", level: 1 },
  { name: "Shield", level: 1 }, { name: "Misty Step", level: 2 },
];
const DEMO_BOOK_DESC =
  "A leather-bound tome of 100 vellum pages. Its inscribed spells can be cast, " +
  "and a trained hand may write more into the blank leaves.";

const demoState = { wandCharges: 7, potions: 2, ringAttuned: false, armorEquipped: false };

function demoItemDetail(name: string): ItemDetail {
  const n = name.toLowerCase();
  if (/spellbook/.test(n)) {
    return { name, type: "Wondrous Item", description: DEMO_BOOK_DESC,
      interactive: "spellbook", spells: [...demoSpells], can_inscribe: true };
  }
  if (/wand of magic missiles/.test(n)) {
    return { name, type: "Wand", rarity: "Uncommon",
      description: "While holding this wand you can expend charges to cast Magic Missile. " +
        "It regains 1d6+1 expended charges each dawn.",
      stats: ["Cost: 500 gp"],
      charges: { current: demoState.wandCharges, max: 7 },
      actions: [
        ...(demoState.wandCharges > 0 ? [{ id: "expend", label: "Expend a charge" }] : []),
        { id: "recharge", label: "Recharge" },
      ] };
  }
  if (/potion of healing/.test(n)) {
    return { name, type: "Potion", rarity: "Common",
      description: "You regain 2d4 + 2 hit points when you drink this potion. " +
        "Its red liquid glimmers when agitated.",
      actions: [{ id: "use", label: "Drink" }] };
  }
  if (/ring of protection/.test(n)) {
    return { name, type: "Ring", rarity: "Rare", attunement: true, attuned: demoState.ringAttuned,
      description: "You gain a +1 bonus to AC and saving throws while wearing this ring.",
      actions: [{ id: demoState.ringAttuned ? "unattune" : "attune",
                  label: demoState.ringAttuned ? "Break Attunement" : "Attune" }] };
  }
  if (/leather armor/.test(n)) {
    return { name, type: "Light Armor", equipped: demoState.armorEquipped,
      description: "Supple boiled leather. Base AC 11 + your Dexterity modifier.",
      stats: ["Base AC: 11", "Weight: 10 lb"],
      actions: [{ id: demoState.armorEquipped ? "unequip" : "equip",
                  label: demoState.armorEquipped ? "Unequip" : "Equip" }] };
  }
  return { name, type: "Gear",
    description: `${name} — a fine example of its kind, worn smooth by the road. ` +
      "In a real session the Oracle fills this from the rules library and conjures its likeness.",
    stats: ["Weight: 1 lb"] };
}

function demoRespond(ev: ClientEvent, onEvent: (ev: ServerEvent) => void) {
  if (ev.t === "inspect_item") {
    onEvent({ t: "item_detail", item: demoItemDetail(ev.name) });
    return;
  }
  if (ev.t === "item_action") {
    const n = ev.name.toLowerCase();
    if (ev.action === "expend" && /wand/.test(n)) demoState.wandCharges = Math.max(0, demoState.wandCharges - 1);
    else if (ev.action === "recharge" && /wand/.test(n)) demoState.wandCharges = 7;
    else if (ev.action === "attune") demoState.ringAttuned = true;
    else if (ev.action === "unattune") demoState.ringAttuned = false;
    else if (ev.action === "equip") demoState.armorEquipped = true;
    else if (ev.action === "unequip") demoState.armorEquipped = false;
    else if (ev.action === "use" && /potion/.test(n)) {
      demoState.potions -= 1;
      if (demoState.potions <= 0) { onEvent({ t: "item_gone", name: ev.name }); return; }
    }
    onEvent({ t: "item_detail", item: demoItemDetail(ev.name) });
    return;
  }
  if (ev.t === "inscribe_spell") {
    if (!demoSpells.some((s) => s.name.toLowerCase() === ev.spell.toLowerCase())) {
      demoSpells.push({ name: ev.spell, level: 1 });
    }
    onEvent({ t: "item_detail", item: demoItemDetail(ev.book || "Spellbook") });
    return;
  }
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
