import type { ClientEvent, ItemDetail, ServerEvent } from "./types";
import { demoArenaApi, demoScript, demoVttApi } from "./demo";

export interface Connection {
  send(ev: ClientEvent): void;
  close(): void;
  /** True when a send will actually be delivered (open socket, or demo feed).
   *  Lets callers fail fast instead of no-op'ing into a silent dead-end. */
  isOpen(): boolean;
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
    isOpen() { return demo || ws.readyState === WebSocket.OPEN; },
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

const demoState = { wandCharges: 7, potions: 2, ringAttuned: false,
                    armorEquipped: false, rapierGrip: "main" as "main" | "off" };
const demoBag: { name: string; qty: number }[] = [{ name: "Torch", qty: 3 }, { name: "Grappling Hook", qty: 1 }];

function demoItemDetail(name: string): ItemDetail {
  const n = name.toLowerCase();
  if (/keen rapier/.test(n)) {
    return {
      name, type: "Martial", rarity: "Rare",
      description: "A finesse blade, ground thin and warm to the touch.",
      stats: ["Damage: 1d8 piercing", "Properties: finesse", "Weight: 2 lb"],
      equipped: true,
      // A rapier is one-handed and not versatile, so the only grip on offer is
      // the other hand — the server decides that, and the demo mirrors it.
      grip: demoState.rapierGrip,
      actions: [
        { id: "unequip", label: "Unequip" },
        demoState.rapierGrip === "main"
          ? { id: "grip_off", label: "Off Hand" }
          : { id: "grip_main", label: "Main Hand" },
      ],
      affixes: [
        { slug: "keen", name: "Keen", kind: "prefix", tier: 1,
          text: "Ground to a wicked edge. +1 to attack rolls.", temper_gp: 87 },
        { slug: "of-the-ember", name: "of the Ember", kind: "suffix", tier: 1,
          text: "Warm to the touch. Deals an extra 1d4 fire damage on a hit.",
          temper_gp: 87 },
      ],
      bonuses: { attack: 1, damage_dice: ["1d4 fire"] },
    };
  }
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
  if (/potion of heroism/.test(n)) {
    return { name, type: "Potion", rarity: "Rare",
      description: "For 1 hour you gain 10 temporary hit points and are immune to being " +
        "frightened. Golden light wells up within you as you drink.",
      actions: [{ id: "use", label: "Drink" }] };
  }
  if (/ring of protection/.test(n)) {
    return { name, type: "Ring", rarity: "Rare", attunement: true, attuned: demoState.ringAttuned,
      description: "You gain a +1 bonus to AC and saving throws while wearing this ring.",
      actions: [{ id: demoState.ringAttuned ? "unattune" : "attune",
                  label: demoState.ringAttuned ? "Break Attunement" : "Attune" }] };
  }
  if (/bag of holding/.test(n)) {
    return { name, type: "Wondrous Item", rarity: "Uncommon",
      description: "This bag has an interior space considerably larger than its outside " +
        "dimensions — roughly 2 feet in diameter and 4 feet deep, holding up to 500 pounds.",
      interactive: "container", contents: demoBag.map((c) => ({ ...c })) };
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
  if (ev.t === "chronicle") {
    onEvent({ t: "chronicle_data", ...demoScript.chronicle });
    return;
  }
  // ---- the Proving Grounds (offline): the same contract, scripted ----
  if (ev.t === "arena_state") {
    onEvent({ t: "arena", state: demoArenaApi.state() });
    return;
  }
  if (ev.t === "arena_create") {
    demoArenaApi.create(ev.slot, ev.payload.name, ev.payload.race,
                        ev.payload.char_class);
    onEvent({ t: "arena", state: demoArenaApi.state() });
    return;
  }
  if (ev.t === "arena_delete") {
    demoArenaApi.remove(ev.slot);
    onEvent({ t: "arena", state: demoArenaApi.state() });
    return;
  }
  if (ev.t === "arena_begin") {
    for (const e of demoArenaApi.begin(ev)) onEvent(e);
    return;
  }
  if (ev.t === "arena_fight") {
    for (const e of demoArenaApi.fight(ev.environment)) onEvent(e);
    return;
  }
  if (ev.t === "arena_shop") {
    for (const e of demoArenaApi.stall()) onEvent(e);
    return;
  }
  if (ev.t === "arena_outfit") {
    for (const e of demoArenaApi.outfit(ev.cart, ev.equip)) onEvent(e);
    return;
  }
  if (ev.t === "arena_leave") {
    for (const e of demoArenaApi.leave()) onEvent(e);
    return;
  }
  // ---- tactical board (offline): the same contract the backend implements ----
  if (ev.t === "vtt_options") {
    onEvent({ t: "vtt_options", ...demoVttApi.options(ev.token_id, !!ev.dash) });
    return;
  }
  if (ev.t === "vtt_move") {
    const res = demoVttApi.move(ev.token_id, ev.x, ev.y);
    if (!res.ok) onEvent({ t: "vtt_error", detail: res.reason ?? "You can't move there." });
    else {
      onEvent({ t: "vtt", scene: demoVttApi.scene() });
      onEvent({ t: "vtt_options", ...demoVttApi.options(ev.token_id, false) });
    }
    return;
  }
  if (ev.t === "vtt_ping") {
    onEvent({ t: "vtt_ping", x: ev.x, y: ev.y, label: ev.label ?? "here" });
    return;
  }
  if (ev.t === "vtt_preview") {
    onEvent({ t: "vtt_preview", ...demoVttApi.preview(ev.token_id, ev.x, ev.y) });
    return;
  }
  if (ev.t === "actions") {
    onEvent({ t: "actions", data: demoVttApi.actions() });
    return;
  }
  if (ev.t === "vtt_targets") {
    onEvent({ t: "vtt_targets", action_id: ev.action_id,
              ...demoVttApi.targets(ev.token_id, ev.range_ft) });
    return;
  }
  if (ev.t === "vtt_area") {
    onEvent({ t: "vtt_area", action_id: ev.action_id,
              ...demoVttApi.area(ev.token_id, ev.x, ev.y, ev.shape,
                                 ev.radius_ft ?? 0, ev.length_ft ?? 0,
                                 ev.range_ft) });
    return;
  }
  if (ev.t === "board_action") {
    // Offline there is no engine to resolve into, so an act is narrated and
    // the bar is re-sent. Enough to exercise the picking flow end to end.
    onEvent({ t: "narration",
              text: `*You commit to ${ev.action_id.split(":").pop()}.*` });
    onEvent({ t: "actions", data: demoVttApi.actions() });
    return;
  }
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
    else if (ev.action === "grip_main") demoState.rapierGrip = "main";
    else if (ev.action === "grip_off") demoState.rapierGrip = "off";
    else if (ev.action === "equip") demoState.armorEquipped = true;
    else if (ev.action === "unequip") demoState.armorEquipped = false;
    else if (ev.action === "take_out" && ev.target) {
      const ci = demoBag.findIndex((c) => c.name.toLowerCase() === ev.target!.toLowerCase());
      if (ci >= 0) { demoBag[ci].qty -= 1; if (demoBag[ci].qty <= 0) demoBag.splice(ci, 1); }
    }
    else if (ev.action === "store" && ev.target) {
      const ex = demoBag.find((c) => c.name.toLowerCase() === ev.target!.toLowerCase());
      if (ex) ex.qty += 1; else demoBag.push({ name: ev.target, qty: 1 });
    }
    else if (ev.action === "use" && /potion of healing/.test(n)) {
      onEvent({ t: "roll", roll: { expr: "2d4+2", label: ev.name, total: 7, detail: "2d4+2 → 3  2 +2 = 7" } });
      onEvent({ t: "narration", text: "Kara drinks the Potion of Healing and regains 7 hit points." });
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
    // In the Grounds the last level-up opens the Quartermaster's stall.
    if (demoArenaApi.climbing()) {
      for (const e of demoArenaApi.stall()) onEvent(e);
    }
    return;
  }
  if (ev.t === "reprepare") {
    const sp = (slug: string, name: string) => ({ slug, name, level: 1, school: "Evocation" });
    // Demo the wizard path: prepare a subset FROM the spellbook (limited list).
    onEvent({
      t: "reprepare_data", count: 4, max_spell_level: 3, class: "Wizard",
      source: "spellbook", no_spellbook: false,
      current: ["magic-missile", "shield", "detect-magic", "mage-armor"],
      options: [
        sp("magic-missile", "Magic Missile"), sp("shield", "Shield"),
        sp("detect-magic", "Detect Magic"), sp("mage-armor", "Mage Armor"),
        sp("burning-hands", "Burning Hands"), sp("sleep", "Sleep"),
        sp("thunderwave", "Thunderwave"),
      ],
    });
    return;
  }
  if (ev.t === "reprepare_apply") {
    onEvent({ t: "narration", text: "*Kara prepares a fresh set of spells.*" });
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
  // A practice bout is decided on the first swing in the offline feed.
  const bout = demoArenaApi.resolve();
  if (bout.length) {
    let d = 500;
    for (const e of bout) { setTimeout(() => onEvent(e), d); d += 250; }
    return;
  }
  onEvent({ t: "busy", on: true });
  let delay = 700;
  for (const e of demoScript.respond(ev.text)) {
    setTimeout(() => onEvent(e), delay);
    delay += e.t === "narration" ? 350 : 150;
  }
  setTimeout(() => onEvent({ t: "busy", on: false }), delay);
}
