import type { CombatState, ServerEvent, VttEffect, VttScene } from "./types";

/** Standalone demo feed — lets the whole UI run with no backend, and doubles
    as living documentation of the event protocol. */
const lexicon: ServerEvent = {
  t: "lexicon",
  entries: [
    { text: "Kara Emberfall", kind: "name" },
    { text: "Kara", kind: "name" },
    { text: "Old Marla", kind: "name" },
    { text: "goblin warrior", kind: "name" },
    { text: "Vicious Mockery", kind: "magic" },
    { text: "Bardic Inspiration", kind: "magic" },
    { text: "lute", kind: "item" },
    { text: "Greenfields", kind: "place" },
    { text: "Wispering Mill", kind: "place" },
  ],
};

const sheet: ServerEvent = {
  t: "sheet",
  sheet: {
    character_id: 1,
    name: "Kara Emberfall",
    subtitle: "Level 5 · Dragonborn Bard",
    hp: 32,
    hp_max: 40,
    temp_hp: 12,
    ac: 14,
    stats: { STR: 10, DEX: 16, CON: 14, INT: 12, WIS: 11, CHA: 18 },
    skills: ["Persuasion +7", "Performance +7", "Perception +3", "Deception +7"],
    inventory: [
      { name: "Rapier", qty: 1, type: "Martial", brief: "A finesse blade — quick, precise, a duelist's friend." },
      { name: "Spellbook", qty: 1, type: "Wondrous", interactive: "spellbook",
        brief: "A leather tome of inscribed magic." },
      { name: "Wand of Magic Missiles", qty: 1, type: "Wand", rarity: "Uncommon", interactive: "charged",
        brief: "7 charges; looses glowing darts of force." },
      { name: "Potion of Healing", qty: 2, type: "Potion", rarity: "Common", interactive: "consumable",
        brief: "Regain 2d4 + 2 hit points when drunk." },
      { name: "Potion of Heroism", qty: 1, type: "Potion", rarity: "Rare", interactive: "consumable",
        brief: "10 temporary hit points and fearless resolve for 1 hour." },
      { name: "Ring of Protection", qty: 1, type: "Ring", rarity: "Rare", interactive: "attunement",
        brief: "+1 to AC and saving throws while attuned." },
      { name: "Bag of Holding", qty: 1, type: "Wondrous", rarity: "Uncommon", interactive: "container",
        brief: "An extradimensional storage space." },
      { name: "Cloak of Billowing", qty: 1, type: "Wondrous", rarity: "Common",
        brief: "Billows dramatically as a bonus action." },
      { name: "Lute", qty: 1, type: "Instrument", brief: "A bard's most trusted companion." },
      { name: "Leather Armor", qty: 1, type: "Light Armor" },
      { name: "Rations", qty: 5, type: "Gear", interactive: "consumable" },
    ],
    gold: 37,
    race: "Dragonborn",
    char_class: "Bard",
    subclass: "College of Lore",
    background: "Entertainer",
    spell_slots: [
      { level: 1, total: 4, used: 1 },
      { level: 2, total: 3, used: 1 },
      { level: 3, total: 2, used: 1 },
    ],
    caster_mode: "spellbook",   // demo the wizard-style prepare-from-spellbook flow
    can_reprepare: true,        // demo: fresh out of a long rest
    resources: [{ name: "Bardic Insp.", total: 3, used: 0, die: "d8" }],
    features: [
      { name: "Breath Weapon", note: "2d10 fire · recharge on rest", kind: "fire" },
      { name: "Cutting Words", note: "subtract a Bardic die from a foe's roll", kind: "arcane" },
      { name: "Bardic Inspiration", note: "d8, bonus action", kind: "arcane" },
    ],
  },
};

const party: ServerEvent = {
  t: "party",
  members: [
    { name: "Kara", hp: 21, hp_max: 28 },
    { name: "Brother Aldous", hp: 17, hp_max: 24 },
    { name: "Pip", hp: 9, hp_max: 18, condition: "poisoned" },
  ],
};

/* Demo initiative carousel: first attack opens the fight, the next one downs
   a goblin, the third ends it. Mirrors the backend's {t:"combat"} events. */
let demoCombatStage = 0;

function demoEncounter(stage: number): CombatState {
  const warriorDown = stage >= 2;
  return {
    id: 1,
    name: "Skirmish at the Wispering Mill",
    round: stage >= 2 ? 2 : 1,
    current_combatant_id: stage >= 2 ? 2 : 3,
    combatants: [
      { id: 3, name: "Goblin Warrior", kind: "monster", initiative: 17,
        max_hp: 7, current_hp: warriorDown ? 0 : 7, temp_hp: 0, armor_class: 15,
        position: warriorDown ? null : "melee with Kara",
        conditions: [], defeated: warriorDown },
      { id: 2, name: "Kara", kind: "pc", initiative: 14, character_id: 1,
        max_hp: 28, current_hp: stage >= 1 ? 17 : 21, temp_hp: 12, armor_class: 14,
        position: warriorDown ? "near" : "melee with Goblin Warrior",
        action_used: stage >= 2, bonus_used: false, move_left: stage >= 2 ? 0 : 1,
        conditions: [], defeated: false },
      { id: 4, name: "Brother Aldous", kind: "pc", initiative: 11,
        max_hp: 24, current_hp: 17, temp_hp: 0, armor_class: 16, position: "near",
        conditions: [], concentration: "Bless", defeated: false },
      { id: 5, name: "Goblin Skulker", kind: "monster", initiative: 8,
        max_hp: 7, current_hp: 7, temp_hp: 0, armor_class: 13, position: "far",
        cover: stage >= 2 ? "half" : "none",
        conditions: stage >= 2 ? ["frightened"] : [], defeated: false },
      { id: 6, name: "Pip", kind: "pc", initiative: 6,
        max_hp: 18, current_hp: 9, temp_hp: 0, armor_class: 12, position: "far",
        conditions: ["poisoned"], defeated: false },
    ],
  };
}

/* Demo tactical board: the same skirmish, seen from above. Mirrors the
   backend's {t:"vtt"} frames — one string per row of tile codes (see
   vtt/terrain.py), tokens carrying their footprint and movement, and effects
   whose squares are already resolved. */
const DEMO_TERRAIN = [
  "####################",
  "#..................#",
  "#...oo........~~~..#",
  "/...oo........~~~..#",
  "#.............~~~..#",
  "#.....OO......~~~..#",
  "#.....OO...........#",
  "#..................#",
  "#.......,,,........#",
  "#.......,,,........#",
  "#..........o.......#",
  "#...n..............#",
  "#..................#",
  "####################",
];

function ring(cx: number, cy: number, r: number): [number, number][] {
  const out: [number, number][] = [];
  for (let y = cy - r; y <= cy + r; y++) {
    for (let x = cx - r; x <= cx + r; x++) {
      if (x < 1 || y < 1 || x > 18 || y > 12) continue;
      if (Math.hypot(x - cx, y - cy) <= r + 0.2) out.push([x, y]);
    }
  }
  return out;
}

function demoVtt(stage: number): VttScene {
  const warriorDown = stage >= 2;
  return {
    id: 1,
    session_id: "demo:1",
    encounter_id: 1,
    name: "The Wispering Mill",
    kind: "combat",
    archetype: "dungeon-room",
    width: 20,
    height: 14,
    square_ft: 5,
    lighting: "dim",
    revision: stage + 1,
    active: true,
    round: stage >= 2 ? 2 : 1,
    current_token_id: stage >= 2 ? 11 : 10,
    terrain: DEMO_TERRAIN,
    fog: null,
    doors: [{ x: 0, y: 3, state: "open", name: "mill door" }],
    elevation: {},
    background_image_id: null,
    art_status: "offline",
    description: "the millhouse floor — grain sacks, a dead millstone, water in the race",
    tokens: [
      { id: 10, name: "Goblin Warrior", kind: "monster", team: "foe",
        x: 6, y: 8, size: "small", squares: 1, combatant_id: 3,
        speed_ft: 30, reach_ft: 5, moved_ft: 0, movement_mode: "walk",
        elevation_ft: 0, hidden: false, prone: false, defeated: warriorDown,
        color: "#ff5a5a" },
      { id: 11, name: "Kara", kind: "pc", team: "party",
        x: 5, y: 8, size: "medium", squares: 1, combatant_id: 2, character_id: 1,
        speed_ft: 30, reach_ft: 5, moved_ft: stage >= 2 ? 15 : 0,
        movement_mode: "walk", elevation_ft: 0, hidden: false, prone: false,
        defeated: false, color: "#4fa3ff" },
      { id: 12, name: "Brother Aldous", kind: "pc", team: "party",
        x: 3, y: 9, size: "medium", squares: 1, combatant_id: 4,
        speed_ft: 30, reach_ft: 5, moved_ft: 0, movement_mode: "walk",
        elevation_ft: 0, hidden: false, prone: false, defeated: false,
        color: "#4fa3ff" },
      { id: 13, name: "Pip", kind: "pc", team: "party",
        x: 2, y: 11, size: "small", squares: 1, combatant_id: 6,
        speed_ft: 25, reach_ft: 5, moved_ft: 0, movement_mode: "walk",
        elevation_ft: 0, hidden: false, prone: false, defeated: false,
        color: "#4fa3ff" },
      { id: 14, name: "Goblin Skulker", kind: "monster", team: "foe",
        x: 14, y: 4, size: "small", squares: 1, combatant_id: 5,
        speed_ft: 30, reach_ft: 5, moved_ft: 0, movement_mode: "walk",
        elevation_ft: 0, hidden: false, prone: false, defeated: false,
        color: "#ff5a5a" },
    ],
    effects: [
      { id: 1, name: "Bless", kind: "aura", shape: "emanation", x: 3, y: 9,
        radius_ft: 15, length_ft: 0, width_ft: 5, direction_deg: 0,
        squares: ring(3, 9, 3), color: "#ffe8a3", opacity: 0.16,
        difficult_terrain: false, blocks_sight: false, concentration: true,
        source_token_id: 12, expires_round: null },
      ...(stage >= 2
        ? [{
            id: 2, name: "Faerie Fire", kind: "area", shape: "cube",
            x: 14, y: 4, radius_ft: 0, length_ft: 20, width_ft: 5,
            direction_deg: 0, squares: ring(14, 4, 2), color: "#7fd7ff",
            opacity: 0.28, difficult_terrain: false, blocks_sight: false,
            concentration: true, source_token_id: 11, expires_round: 12,
          } as VttEffect]
        : []),
      { id: 3, name: "spilled grain", kind: "zone", shape: "path", x: 8, y: 8,
        radius_ft: 0, length_ft: 0, width_ft: 5, direction_deg: 0,
        squares: [[8, 8], [9, 8], [10, 8], [8, 9], [9, 9], [10, 9]],
        color: "#b58b3c", opacity: 0.22, difficult_terrain: true,
        blocks_sight: false, concentration: false, expires_round: null },
    ],
    legend: "# wall, o crates, O pillar, ~ shallow water, , rubble",
  };
}

/* The demo board is interactive: positions the player pushes around live here,
   and movement options are costed with the same rules the server uses (5 ft a
   square, 10 through rough ground, no cutting a diagonal between two walls). */
const demoTokenPos = new Map<number, [number, number]>();
const demoMoved = new Map<number, number>();

function demoScene(): VttScene {
  const scene = demoVtt(demoCombatStage);
  scene.tokens = scene.tokens.map((t) => {
    const pos = demoTokenPos.get(t.id);
    const moved = demoMoved.get(t.id);
    return pos ? { ...t, x: pos[0], y: pos[1], moved_ft: moved ?? t.moved_ft } : t;
  });
  return scene;
}

const DEMO_COST: Record<string, number | null> = {
  "#": null, o: null, O: null, n: null, W: null, "+": null,
  "~": 10, ",": 10, '"': 10,
};

function demoTileCost(x: number, y: number): number | null {
  const row = DEMO_TERRAIN[y];
  if (!row || x < 0 || x >= row.length) return null;
  const code = row[x];
  return code in DEMO_COST ? DEMO_COST[code] : 5;
}

/** Dijkstra over the demo grid — the client-side stand-in for the server's
    reachable_costs, so the movement wash and path preview behave identically. */
function demoReach(tokenId: number, dash: boolean) {
  const scene = demoScene();
  const me = scene.tokens.find((t) => t.id === tokenId);
  if (!me) return { token_id: tokenId, budget_ft: 0, squares: [] };
  const blocked = new Set(
    scene.tokens.filter((t) => t.id !== tokenId && !t.defeated)
      .map((t) => `${t.x},${t.y}`));
  const budget = Math.max(0, me.speed_ft * (dash ? 2 : 1) - me.moved_ft);
  const best = new Map<string, number>([[`${me.x},${me.y}`, 0]]);
  const queue: [number, number, number][] = [[0, me.x, me.y]];
  while (queue.length) {
    queue.sort((a, b) => a[0] - b[0]);
    const [cost, x, y] = queue.shift()!;
    if (cost > (best.get(`${x},${y}`) ?? Infinity)) continue;
    for (let dx = -1; dx <= 1; dx++) {
      for (let dy = -1; dy <= 1; dy++) {
        if (!dx && !dy) continue;
        const nx = x + dx;
        const ny = y + dy;
        const step = demoTileCost(nx, ny);
        if (step == null || blocked.has(`${nx},${ny}`)) continue;
        if (dx && dy && demoTileCost(x + dx, y) == null && demoTileCost(x, y + dy) == null) continue;
        const total = cost + step;
        if (total > budget) continue;
        if (total < (best.get(`${nx},${ny}`) ?? Infinity)) {
          best.set(`${nx},${ny}`, total);
          queue.push([total, nx, ny]);
        }
      }
    }
  }
  return {
    token_id: tokenId,
    budget_ft: budget,
    squares: [...best].map(([k, cost]) => {
      const [x, y] = k.split(",").map(Number);
      return { x, y, cost };
    }),
  };
}

export const demoVttApi = {
  scene: demoScene,
  options: demoReach,
  move(tokenId: number, x: number, y: number) {
    const reach = demoReach(tokenId, false);
    const hit = reach.squares.find((s) => s.x === x && s.y === y);
    if (!hit) return { ok: false, reason: "You can't reach that square." };
    demoTokenPos.set(tokenId, [x, y]);
    const scene = demoScene();
    const me = scene.tokens.find((t) => t.id === tokenId);
    demoMoved.set(tokenId, (me?.moved_ft ?? 0) + hit.cost);
    return { ok: true };
  },
};

export const demoScript = {
  hello: {
    t: "hello",
    channel: "demo",
    characters: [
      { id: 1, name: "Kara Emberfall", race: "Dragonborn",
        char_class: "Bard", subclass: "College of Lore", level: 5,
        alive: true, resume_session: "demo:1" },
      { id: 2, name: "Aldric the Bold", race: "Human", char_class: "Fighter",
        level: 4, alive: false },
    ],
  } as Extract<ServerEvent, { t: "hello" }>,
  opening: [
    lexicon,
    sheet,
    party,
    {
      t: "narration",
      text:
        "The road out of Greenfields narrows where the alder trees crowd close, " +
        "and the Wispering Mill rises ahead — sails torn, turning anyway in a wind " +
        "you cannot feel. Old Marla warned you about this place over her cups: " +
        "millers grind no grain at midnight. A goblin warrior's tracks cross the mud " +
        "at your feet, fresh enough that water still seeps into them.",
    } as ServerEvent,
  ],
  respond(action: string): ServerEvent[] {
    if (/level ?up/i.test(action)) {
      return [
        {
          t: "levelup",
          data: {
            character_id: 1,
            current_level: 2, next_level: 3, class: "Ranger",
            subclass: null, subclass_required: true,
            subclass_label: "Ranger Archetype",
            notes: [
              "Gain hit points: roll 1d10+2 or take the fixed average of 8.",
              "You reach the level where your class chooses its subclass (level 3). Pick one now.",
            ],
            class_features: [],
            subclass_options: [
              {
                name: "Gloom Stalker", slug: "gloom-stalker",
                source: "Owned (PHB 2024) — local ingest",
                features: [
                  { level: 3, name: "Dread Ambusher" },
                  { level: 3, name: "Gloom Stalker Spells" },
                  { level: 3, name: "Umbral Sight" },
                ],
              },
              {
                name: "Hunter", slug: "hunter",
                source: "Owned (PHB 2024) — local ingest",
                features: [
                  { level: 3, name: "Hunter's Lore" },
                  { level: 3, name: "Hunter's Prey" },
                ],
              },
              {
                name: "Beast Master", slug: "beast-master",
                source: "Owned (PHB 2024) — local ingest",
                features: [{ level: 3, name: "Primal Companion" }],
              },
              {
                name: "Horizon Walker", slug: "horizon-walker",
                source: "Owned (Xanathar's Guide) — local ingest",
                features: [
                  { level: 3, name: "Detect Portal" },
                  { level: 3, name: "Planar Warrior" },
                ],
              },
            ],
            spells_due: {
              cantrips: 0, spells: 1, mode: "known", max_spell_level: 1,
              cantrip_options: [],
              spell_options: [
                { slug: "cure-wounds", name: "Cure Wounds", level: 1, school: "Abjuration" },
                { slug: "hunters-mark", name: "Hunter's Mark", level: 1, school: "Divination", concentration: true },
                { slug: "goodberry", name: "Goodberry", level: 1, school: "Conjuration" },
                { slug: "ensnaring-strike", name: "Ensnaring Strike", level: 1, school: "Conjuration", concentration: true },
              ],
              can_swap: true,
              current_spells: [
                { slug: "fog-cloud", name: "Fog Cloud" },
                { slug: "speak-with-animals", name: "Speak with Animals" },
              ],
            },
          },
        },
      ];
    }
    if (/sneak|stealth|hide|quiet/i.test(action)) {
      return [
        {
          t: "roll",
          roll: {
            expr: "1d20+7", label: "Stealth", dc: 13,
            total: 19, detail: "d20:12 +7", success: true,
          },
        },
        {
          t: "narration",
          text:
            "Kara melts into the treeline, Umbral Sight drinking the dark. " +
            "The mill door hangs open, and inside, two shapes hunch over something " +
            "that gleams — a goblin warrior and its mate, arguing in whispers over " +
            "a longbow far too fine for either of them.",
        },
      ];
    }
    if (/shoot|attack|fire|loose|stab|strike|swing|kill/i.test(action)) {
      if (demoCombatStage === 0) {
        demoCombatStage = 1;
        return [
          {
            t: "roll",
            roll: {
              expr: "1d20+7", label: "Longbow attack", dc: 15,
              total: 9, detail: "d20:2 +7", success: false,
            },
          },
          {
            t: "narration",
            text:
              "The arrow skips off the doorframe with a crack like a snapped branch. " +
              "Both goblins spin. The nearer one snarls something ugly and hurls a " +
              "rusted hatchet — Kara takes 4 damage as it grazes her shoulder before " +
              "burying itself in the alder behind her.\n\n" +
              "⚔ Initiative — Goblin Warrior 17, Kara 14, Brother Aldous 11, " +
              "Goblin Skulker 8, Pip 6",
          },
          { t: "combat", encounter: demoEncounter(1) },
          { t: "vtt", scene: demoVtt(1) },
          { t: "sheet", sheet: { ...(sheet as any).sheet, hp: 17 } },
          {
            t: "party",
            members: [
              { name: "Kara", hp: 17, hp_max: 28 },
              { name: "Brother Aldous", hp: 17, hp_max: 24 },
              { name: "Pip", hp: 9, hp_max: 18, condition: "poisoned" },
            ],
          },
        ];
      }
      if (demoCombatStage === 1) {
        demoCombatStage = 2;
        return [
          {
            t: "roll",
            roll: {
              expr: "1d20+7", label: "Rapier attack", dc: 15,
              total: 22, detail: "d20:15 +7", success: true,
            },
          },
          {
            t: "narration",
            text:
              "Kara's rapier finds the gap under the warrior's chin and it drops " +
              "where it stands. The skulker's eyes go wide — it backs toward the " +
              "millstone, blade shaking.",
          },
          { t: "combat", encounter: demoEncounter(2) },
          { t: "vtt", scene: demoVtt(2) },
        ];
      }
      demoCombatStage = 0;
      return [
        {
          t: "narration",
          text:
            "The skulker bolts through a gap in the mill's boards and is gone " +
            "into the dark. The wheel creaks on, indifferent.\n\n⚔ The fight is over.",
        },
        { t: "combat", encounter: null },
        { t: "vtt", scene: null },
      ];
    }
    return [
      {
        t: "narration",
        text:
          "The wind shifts. Somewhere above the millworks, a chain clinks — " +
          "once, deliberately, like a thing testing its own weight. Old Marla's " +
          "words come back to you: the miller pays his debts in millstones.",
      },
    ];
  },
};
