import type { ActionBarData, ArenaEnv, ArenaEquipLine, ArenaOutfitLine,
              ArenaShop, ArenaState,
              ArenaStockItem, ChronicleData, CombatState, ServerEvent, VttEffect,
              VttScene } from "./types";

/** Standalone demo feed — lets the whole UI run with no backend, and doubles
    as living documentation of the event protocol. */
const lexicon: ServerEvent = {
  t: "lexicon",
  entries: [
    // Cultural hands: a dragonborn bard, a dwarven miller, an elven ranger and
    // a god of the Choir do not read in the same face.
    { text: "Kara Emberfall", kind: "name", script: "draconic" },
    { text: "Kara", kind: "name", script: "draconic" },
    { text: "Old Marla", kind: "name", script: "dwarven" },
    { text: "Sylvaine", kind: "name", script: "elven" },
    { text: "Vashra the Unlit", kind: "name", script: "infernal" },
    { text: "Aurelion", kind: "name", script: "celestial" },
    { text: "Thistlewick", kind: "name", script: "fey" },
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
      { name: "Keen Rapier of the Ember", qty: 1, type: "Martial", rarity: "Rare",
        weight: 2, equipped: true, grip: "main",
        action: { id: "unequip", label: "Unequip" },
        affixes: [
          { slug: "keen", name: "Keen", kind: "prefix", tier: 1,
            text: "Ground to a wicked edge. +1 to attack rolls.", temper_gp: 87 },
          { slug: "of-the-ember", name: "of the Ember", kind: "suffix", tier: 1,
            text: "Warm to the touch. Deals an extra 1d4 fire damage on a hit.",
            temper_gp: 87 },
        ],
        brief: "A finesse blade — quick, precise, a duelist's friend." },
      { name: "Rapier", qty: 1, type: "Martial", weight: 2,
        action: { id: "equip", label: "Equip" },
        brief: "A finesse blade — quick, precise, a duelist's friend." },
      { name: "Spellbook", qty: 1, type: "Wondrous", interactive: "spellbook",
        brief: "A leather tome of inscribed magic." },
      { name: "Wand of Magic Missiles", qty: 1, type: "Wand", rarity: "Uncommon", interactive: "charged",
        weight: 1, action: { id: "expend", label: "Expend a charge" },
        brief: "7 charges; looses glowing darts of force." },
      { name: "Potion of Healing", qty: 2, type: "Potion", rarity: "Common", interactive: "consumable",
        weight: 0.5, action: { id: "use", label: "Drink" },
        brief: "Regain 2d4 + 2 hit points when drunk." },
      { name: "Potion of Heroism", qty: 1, type: "Potion", rarity: "Rare", interactive: "consumable",
        weight: 0.5, action: { id: "use", label: "Drink" },
        brief: "10 temporary hit points and fearless resolve for 1 hour." },
      { name: "Ring of Protection", qty: 1, type: "Ring", rarity: "Rare", interactive: "attunement",
        attuned: true, action: { id: "unattune", label: "Break Attunement" },
        brief: "+1 to AC and saving throws while attuned." },
      { name: "Bag of Holding", qty: 1, type: "Wondrous", rarity: "Uncommon", interactive: "container",
        brief: "An extradimensional storage space." },
      { name: "Cloak of Billowing", qty: 1, type: "Wondrous", rarity: "Common",
        brief: "Billows dramatically as a bonus action." },
      { name: "Lute", qty: 1, type: "Instrument", brief: "A bard's most trusted companion." },
      { name: "Leather Armor", qty: 1, type: "Light Armor", weight: 10, equipped: true,
        action: { id: "unequip", label: "Unequip" } },
      { name: "Rations", qty: 5, type: "Gear", interactive: "consumable", weight: 2,
        action: { id: "use", label: "Use" } },
    ],
    gold: 37,
    carried: 24.5,
    capacity: 150,
    // What is on the body: the rapier is in a hand, the armour is worn, and
    // one hand is still free — which is the difference between casting and not.
    loadout: {
      hands: [{ name: "Keen Rapier of the Ember", grip: "main" }],
      free_hands: 1,
      armor: "Leather Armor",
      rings: ["Ring of Protection"],
      worn: [],
      text: "main hand: Keen Rapier of the Ember; 1 hand free; wearing Leather Armor",
    },
    race: "Dragonborn",
    script: "draconic",
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

/* The "here and now" strip. Mirrors the backend's `_activity_locale` — place,
   world clock, weather and who's standing here, but never a map. */
const locale: ServerEvent = {
  t: "locale",
  locale: {
    place: "Wispering Mill",
    place_kind: "poi",
    region: "The Greenfields",
    date: "12 Aestral, 1247 AF",
    time_of_day: "dusk",
    day: 341,
    weather: "Overcast and still, a cold drizzle setting in before dark.",
    hazards: ["dim light"],
    present: [
      { name: "Old Marla", kind: "npc", role: "miller", attitude: "friendly" },
      { name: "Brother Aldous", kind: "pc" },
      { name: "Pip", kind: "pc" },
      { name: "Hooded Stranger", kind: "npc", role: "traveller" },
    ],
  },
};

/* The Chronicle, offline. Mirrors `_activity_journal` + `_activity_bonds`. */
const demoChronicle: ChronicleData = {
  entries: [
    { day: 341, text: "Quest begun: The Mill That Grinds No Grain", place: "Wispering Mill" },
    { day: 341, text: "Kara traded a silver ring to Old Marla for directions.", place: "Greenfields" },
    { day: 340, text: "Brother Aldous was wounded driving off a goblin raid.", place: "Greenfields" },
    { day: 338, text: "The party arrived in Greenfields from the eastern road.", place: "Greenfields" },
    { day: 337, text: "Pip drank from a still pool and has not been right since." },
  ],
  quests: [
    {
      name: "The Mill That Grinds No Grain", state: "active", tier: "main",
      conflict: "Someone is working the mill at midnight, and the village needs it stopped.",
      stakes: "Every night it turns, another field goes to blight.",
      patron: "Old Marla", reward: "The miller's stake in the harvest",
      objectives: ["Get inside the mill unseen", "Find out who is grinding, and what"],
      leads: ["The torn sails were cut, not weathered"],
    },
    {
      name: "A Longbow Too Fine", state: "offered", tier: "rumor",
      conflict: "Goblins are carrying gear no goblin could have made.",
    },
    {
      name: "The Goblin Raid on Greenfields", state: "completed", tier: "side",
      conflict: "Raiders bled the village's stores through the autumn.",
    },
  ],
  bonds: [
    { name: "Brother Aldous", slug: "brother-aldous", role: "cleric", companion: true,
      sentiment: 6.2, feeling: "allied", reason: "Kara carried him out of the burning barn." },
    { name: "Old Marla", slug: "old-marla", role: "miller", sentiment: 3.1,
      feeling: "warm", attitude: "friendly", reason: "Paid honestly, and listened." },
    { name: "Garrick Vane", slug: "garrick-vane", role: "reeve", sentiment: -4.4,
      feeling: "hostile", reason: "Kara named him a coward in front of the whole hall." },
    { name: "Pip", slug: "pip", role: "scout", companion: true, sentiment: 1.8,
      feeling: "warm" },
    { name: "The Hooded Stranger", slug: "hooded-stranger", sentiment: -0.3,
      feeling: "neutral" },
  ],
  standing: [
    { faction: "The Greenfields Watch", slug: "greenfields-watch", renown: 7,
      standing: "accepted", next: "respected", needed: 3,
      perks: "A trusted associate; access to a safehouse, gear at fair prices.",
      note: "They still speak of the barn fire." },
    { faction: "The Coinwright's Hall", slug: "coinwrights-hall", renown: 2,
      standing: "known", next: "accepted", needed: 3,
      perks: "Recognised by rank-and-file; minor favours and rumours." },
  ],
  codex: [
    { name: "Serath the Dawnmother", slug: "serath", kind: "deity",
      script: "celestial", group: "The Sovereign Powers",
      note: "The kindly mother of dawn and the fields; the common faith of farming folk." },
    { name: "Vashra the Unlit", slug: "vashra", kind: "deity",
      script: "infernal", group: "The Lords of the Nine",
      note: "Named in a cult-scrawl beneath the mill." },
    { name: "Greenfields", slug: "greenfields", kind: "place", subtype: "village",
      note: "A farming village on the eastern road; the mill feeds it." },
    { name: "Wispering Mill", slug: "wispering-mill", kind: "place", subtype: "poi",
      note: "Sails torn, turning anyway in a wind you cannot feel." },
    { name: "The Greenfields Watch", slug: "greenfields-watch", kind: "faction",
      note: "Farmhands with spears who muster when the horn sounds." },
    { name: "Old Marla", slug: "old-marla", kind: "npc", subtype: "miller",
      script: "dwarven", note: "Keeps the mill, and her own counsel." },
    { name: "The goblins carry gear no goblin made", slug: "fine-longbow",
      kind: "lore", note: "A longbow far too fine for either of them." },
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

/** Tile code -> the word the board writes on it. Mirrors terrain.sprite_label. */
const DEMO_OBJECT_LABELS: Record<string, string> = {
  O: "pillar", o: "crates", n: "table", T: "tree", A: "altar",
  w: "low wall", "+": "door", "/": "doorway", p: "gate",
};

function isSolid(rows: string[], x: number, y: number): boolean {
  const c = rows[y]?.[x];
  return c === undefined || c === "#" || c === "R" || c === "w";
}

/** The gallery over the mill floor. Blank squares are open air: you can see —
 *  and fall — through them, which is what lets the two floors share a fight. */
const DEMO_GALLERY = [
  "                    ",
  "#..................#",
  "#..................#",
  "                   #",
  "                   #",
  "                    ",
  "                    ",
  "                    ",
  "                    ",
  "                    ",
  "                    ",
  "                    ",
  "                    ",
  "                    ",
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
    sight: null,
    // Two floors, so the offline demo exercises the storey switcher: a gallery
    // along the north wall over the mill floor, reached by the stair at 17,2.
    // The blanks are open air — the hall below shows through them.
    levels: [
      { name: "Mill floor", base_ft: 0, terrain: DEMO_TERRAIN,
        stairs: [{ x: 17, y: 2, to: 1, tx: 17, ty: 1, kind: "stair" }] },
      { name: "Gallery", base_ft: 15, terrain: DEMO_GALLERY,
        stairs: [{ x: 17, y: 1, to: 0, tx: 17, ty: 2, kind: "stair" }] },
    ],
    doors: [{ x: 0, y: 3, state: "open", name: "mill door" }],
    elevation: {},
    // Objects are read off the terrain by the server, exactly as the engine
    // does — so the offline demo shows the same board the live one would, minus
    // the sprite pictures (there is no image store here, and the tile colours
    // plus the labels carry it perfectly well).
    objects: DEMO_TERRAIN.flatMap((row, y) =>
      [...row].flatMap((code, x) => {
        const label = DEMO_OBJECT_LABELS[code];
        if (!label) return [];
        const axis = code === "/" || code === "+" || code === "p"
          ? (isSolid(DEMO_TERRAIN, x, y - 1) && isSolid(DEMO_TERRAIN, x, y + 1)
            ? "ns" : "ew")
          : "";
        return [{ x, y, code, name: label, label, axis, image_id: null }];
      })),
    // A LANDMARK, so the offline demo exercises the one thing on the board
    // that is somebody else's model rather than derived geometry. Its square
    // is already a pillar in DEMO_TERRAIN and the piece stamps `O` — a set
    // piece owns no rules, so the picture and the grid say the same thing
    // here as everywhere. `scale` and `pivot` are what the server measured off
    // the file (vtt/setpieces.py mesh_fit); a browser must never recompute
    // them, so they are pasted rather than derived.
    setpieces: [
      { slug: "broken-pillar", name: "broken pillar", x: 7, y: 6, yaw: 0,
        w: 1, d: 1, height_ft: 12, up: "y", yaw_fix: 0, code: "O",
        words: "a snapped stone column, its top long gone",
        mesh: "/assets/setpieces/broken-pillar.obj",
        scale: 0.6015202, pivot: [0, 0, 0] },
    ],
    debris: stage >= 2
      ? [{ x: 6, y: 5, code: ",", was: "pillar", material: "stone",
           label: "broken pillar", image_id: null }]
      : [],
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

/* The level-up overlay payload, shared by the scripted world demo and
   the offline Proving Grounds. */
const demoLevelUp: ServerEvent = {
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
};

/** Chebyshev feet between two tokens' squares — the demo's stand-in for the
 *  server's real 5-5-5 measurement with height folded in. */
function demoGapFt(a: { x: number; y: number }, b: { x: number; y: number }): number {
  return Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y)) * 5;
}

export const demoVttApi = {
  scene: demoScene,
  options: demoReach,
  /** Who an act could hit, offline. The same CONTRACT as the backend's
   *  `targets_for`: every creature comes back, and the illegal ones carry a
   *  reason. The demo has no line-of-sight model, so only range is judged —
   *  enough to drive the picker, and honest about what it is. */
  targets(tokenId: number, rangeFt: number | null | undefined) {
    const scene = demoScene();
    const me = scene.tokens.find((t) => t.id === tokenId);
    if (!me) return { ok: false, targets: [] };
    const targets = scene.tokens
      .filter((t) => t.id !== tokenId && !t.defeated)
      .map((t) => {
        const d = demoGapFt(me, t);
        const over = rangeFt != null && d > rangeFt;
        return {
          token_id: t.id, name: t.name, team: t.team, kind: t.kind,
          x: t.x, y: t.y, level: t.level ?? 0, squares: t.squares,
          distance_ft: d, cover: "none",
          legal: !over,
          reason: over ? `out of range — ${d} ft away, reaches ${rangeFt} ft` : "",
        };
      })
      .sort((a, b) => Number(!a.legal) - Number(!b.legal)
        || a.distance_ft - b.distance_ft);
    return { ok: true, actor: me.name, actor_token_id: me.id,
             range_ft: rangeFt ?? null, targets };
  },
  /** Where a template would land, offline. Walls are respected only as
   *  impassable tiles, not by line of effect. */
  area(tokenId: number, x: number, y: number, shape: string,
       radiusFt: number, lengthFt: number, rangeFt: number | null | undefined) {
    const scene = demoScene();
    const me = scene.tokens.find((t) => t.id === tokenId);
    if (!me) return { ok: false, reason: "no token", squares: [], caught: [] };
    const dist = demoGapFt(me, { x, y });
    const size = radiusFt || lengthFt || 10;
    const r = Math.max(1, Math.round(size / 5));
    const from = shape === "cone" || shape === "line" ? me : { x, y };
    const squares: [number, number][] = [];
    for (let yy = from.y - r; yy <= from.y + r; yy++) {
      for (let xx = from.x - r; xx <= from.x + r; xx++) {
        if (demoTileCost(xx, yy) == null) continue;
        const dx = xx - from.x;
        const dy = yy - from.y;
        if (shape === "cube") {
          if (Math.abs(dx) > r || Math.abs(dy) > r) continue;
        } else if (Math.hypot(dx, dy) > r + 0.001) continue;
        if (shape === "cone" && Math.sign(dx || 1) !== Math.sign((x - me.x) || 1)) continue;
        squares.push([xx, yy]);
      }
    }
    const hit = new Set(squares.map(([sx, sy]) => `${sx},${sy}`));
    const caught = scene.tokens
      .filter((t) => !t.defeated && hit.has(`${t.x},${t.y}`))
      .map((t) => ({ token_id: t.id, name: t.name, team: t.team, x: t.x, y: t.y }));
    const over = rangeFt != null && dist > rangeFt;
    return {
      ok: !over,
      reason: over
        ? `out of range — that point is ${dist} ft away, the spell reaches ${rangeFt} ft`
        : "",
      shape, origin: [x, y] as [number, number], level: 0,
      distance_ft: dist, squares, caught,
    };
  },
  /** A small action bar, offline. The real one is built from the loadout and
   *  the prepared list by `_activity_actions`; this is enough to drive the
   *  screens — one melee weapon, one bolt, one template, and the verbs. */
  actions(): ActionBarData {
    return {
      character_id: 1,
      slots: { "1": 3, "2": 2 },
      economy: {
        in_combat: true, my_turn: true, action: true, bonus: true,
        reaction: true, move_left_ft: 30, speed_ft: 30, attacks_made: 0,
        attacks_per_action: 1, whose_turn: "Kara Emberfall",
      },
      actions: [
        { id: "attack:0:Rapier", kind: "attack", verb: "attack", arg: "Rapier",
          name: "Rapier", detail: "1d8+3 piercing · main hand", cost: "action",
          targeting: "creature", team: "enemy", range_ft: 5, needs_sight: false,
          enabled: true },
        { id: "attack:1:Shortbow", kind: "attack", verb: "attack", arg: "Shortbow",
          name: "Shortbow", detail: "1d6+3 piercing · two-handed", cost: "action",
          targeting: "creature", team: "enemy", range_ft: 80, ranged: true,
          needs_sight: false, enabled: true },
        { id: "cast:fire-bolt", kind: "cast", verb: "cast", arg: "Fire Bolt",
          name: "Fire Bolt", detail: "cantrip · 120 feet", cost: "action",
          targeting: "creature", team: "any", range_ft: 120, needs_sight: true,
          level: 0, slots: [], enabled: true },
        { id: "cast:burning-hands", kind: "cast", verb: "cast", arg: "Burning Hands",
          name: "Burning Hands", detail: "level 1 · Self · 15-ft cone",
          cost: "action", targeting: "area", team: "any", range_ft: 15,
          needs_sight: false, level: 1, slots: [1, 2], shape: "cone",
          radius_ft: 0, length_ft: 15, width_ft: 5, origin: "self",
          enabled: true },
        { id: "cast:shatter", kind: "cast", verb: "cast", arg: "Shatter",
          name: "Shatter", detail: "level 2 · 60 feet · 10-ft sphere",
          cost: "action", targeting: "area", team: "any", range_ft: 60,
          needs_sight: false, level: 2, slots: [2], shape: "sphere",
          radius_ft: 10, length_ft: 0, width_ft: 5, origin: "point",
          enabled: true },
        { id: "verb:dash", kind: "verb", verb: "dash", name: "Dash",
          detail: "double your movement this turn", cost: "action",
          targeting: "none", enabled: true },
        { id: "verb:dodge", kind: "verb", verb: "dodge", name: "Dodge",
          detail: "attacks against you have disadvantage", cost: "action",
          targeting: "none", enabled: true },
        { id: "verb:hide", kind: "verb", verb: "hide", name: "Hide",
          detail: "a Stealth check against those who can't see you",
          cost: "action", targeting: "none", enabled: true },
        { id: "verb:end_turn", kind: "verb", verb: "end_turn", name: "End turn",
          detail: "pass the turn along", cost: "free", targeting: "none",
          enabled: true },
      ],
    };
  },
  /** Mirrors the server's path preview: cost plus whose reach the route leaves. */
  preview(tokenId: number, x: number, y: number) {
    const scene = demoScene();
    const me = scene.tokens.find((t) => t.id === tokenId);
    const hit = demoReach(tokenId, true).squares.find((s) => s.x === x && s.y === y);
    if (!me || !hit) return { token_id: tokenId, ok: false, reason: "no route" };
    const near = (ax: number, ay: number, t: { x: number; y: number }) =>
      Math.max(Math.abs(ax - t.x), Math.abs(ay - t.y)) <= 1;
    const opportunity = scene.tokens
      .filter((t) => t.team !== me.team && !t.defeated
        && near(me.x, me.y, t) && !near(x, y, t))
      .map((t) => t.name);
    return { token_id: tokenId, ok: true, cost_ft: hit.cost, opportunity };
  },
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

/* ---- The Proving Grounds, offline ---------------------------------------
   A trimmed copy of the backend's catalog (two places per domain) so the
   practice screens are explorable with no backend running. The real list lives
   in arena/environments.py. */
const demoEnvs: ArenaEnv[] = [
  { slug: "training-yard", name: "The Sand Ring", domain: "land", mode: "walk",
    archetype: "arena",
    blurb: "A bare sand pit under a hot white sky. No cover, no excuses." },
  { slug: "old-forest", name: "Blackroot Wood", domain: "land", mode: "walk",
    archetype: "forest",
    blurb: "Close trunks and tangled undergrowth. Sight lines die at twenty feet." },
  { slug: "ship-deck", name: "The Rolling Deck", domain: "sea", mode: "walk",
    archetype: "ship",
    blurb: "A ship's deck at sea — rigging, lashed crates, water past the rail." },
  { slug: "coral-reef", name: "The Sunlit Shelf", domain: "sea", mode: "swim",
    archetype: "reef",
    blurb: "A coral shelf beneath the waves. Sand flats, deep channels, coral heads." },
  { slug: "sky-islands", name: "The Hanging Stones", domain: "air", mode: "fly",
    archetype: "sky-islands",
    blurb: "Islands of broken rock adrift in open sky, cloud far below." },
  { slug: "skyship", name: "The Skyship Argent", domain: "air", mode: "fly",
    archetype: "skyship",
    blurb: "The deck of a flying ship under sail. Past the rail there is nothing." },
];

const demoArenaState: ArenaState = {
  slots: [
    { slot: 1, character: { id: 901, name: "Practice Kara", race: "Human",
        char_class: "Fighter", subclass: null, level: 1, hp: 12, hp_max: 12 },
      leveled: null },
    { slot: 2, character: null, leveled: null },
    { slot: 3, character: null, leveled: null },
  ],
  environments: demoEnvs,
  difficulties: ["easy", "medium", "hard", "deadly"],
  max_level: 20,
  max_slots: 3,
  run: null,
};

/* The Quartermaster's stall, offline: a handful of the real catalog's shapes —
   priced mundane gear, magic gated by rarity, one thing that wants attunement. */
const demoStock: ArenaStockItem[] = [
  { slug: "rope-hempen", name: "Rope, Hempen (50 feet)", cost_gp: 1, kind: "gear",
    category: "adventuring-gear", item_type: "Standard Gear", equippable: false },
  { slug: "shield", name: "Shield", cost_gp: 10, kind: "gear",
    category: "armor", item_type: "Shield", equippable: true },
  { slug: "longsword", name: "Longsword", cost_gp: 15, kind: "gear",
    category: "weapon", item_type: "Martial", equippable: true },
  { slug: "chain-mail", name: "Chain Mail", cost_gp: 75, kind: "gear",
    category: "armor", item_type: "Heavy", equippable: true },
  { slug: "plate-armor", name: "Plate Armor", cost_gp: 1500, kind: "gear",
    category: "armor", item_type: "Heavy", equippable: true },
  { slug: "potion-of-healing", name: "Potion of Healing", cost_gp: 100,
    kind: "magic", category: "magic-item", item_type: "Potion", rarity: "common",
    equippable: false, brief: "You regain 2d4 + 2 hit points when you drink it." },
  { slug: "cloak-of-protection", name: "Cloak of Protection", cost_gp: 600,
    kind: "magic", category: "magic-item", item_type: "Wondrous Item",
    rarity: "uncommon", attunement: true, equippable: true,
    brief: "+1 bonus to AC and saving throws while you wear it." },
  { slug: "longsword-1", name: "Longsword +1", cost_gp: 600, kind: "magic",
    category: "magic-item", item_type: "Weapon", rarity: "uncommon",
    equippable: true, brief: "+1 bonus to attack and damage rolls." },
  { slug: "boots-of-striding", name: "Boots of Striding and Springing",
    cost_gp: 600, kind: "magic", category: "magic-item",
    item_type: "Wondrous Item", rarity: "uncommon", attunement: true,
    equippable: true, brief: "Your walking speed becomes 30 feet and you leap far." },
];

let demoCart: ArenaShop["cart"] = [];

function demoShop(): ArenaShop {
  const level = demoArenaState.run?.target_level ?? 1;
  const purse = level <= 1 ? 125 : level <= 4 ? 600 : level <= 10 ? 4800 : 11000;
  const spent = demoCart.reduce((sum, l) => sum + l.line_gp, 0);
  return {
    level, purse, spent, remaining: purse - spent,
    attunement_limit: 3,
    attuned: demoCart.filter((l) => l.attuned).length,
    items: demoStock.filter((i) => i.cost_gp <= purse),
    cart: demoCart,
    pack: [
      { name: "Chain Mail", quantity: 1, equipped: false, attuned: false,
        attunement: false },
      { name: "Longsword", quantity: 1, equipped: false, attuned: false,
        attunement: false },
      { name: "Shield", quantity: 1, equipped: false, attuned: false,
        attunement: false },
    ],
    rejected: [],
  };
}

export const demoArenaApi = {
  state(): ArenaState {
    const state = JSON.parse(JSON.stringify(demoArenaState)) as ArenaState;
    if (state.run?.phase === "outfitting") state.shop = demoShop();
    return state;
  },
  create(slot: number, name: string, race?: string | null, cls?: string | null) {
    const row = demoArenaState.slots.find((s) => s.slot === slot);
    if (row) {
      row.character = { id: 900 + slot, name, race: race ?? null,
                        char_class: cls ?? null, subclass: null, level: 1,
                        hp: 12, hp_max: 12 };
      row.leveled = null;
    }
  },
  remove(slot: number) {
    const row = demoArenaState.slots.find((s) => s.slot === slot);
    if (row) { row.character = null; row.leveled = null; }
  },
  /** True while the run is still climbing to its chosen level. */
  climbing(): boolean {
    return demoArenaState.run?.phase === "leveling";
  },
  begin(o: { slot: number; environment: string; level: number; difficulty: string;
             reuse?: boolean }): ServerEvent[] {
    const row = demoArenaState.slots.find((s) => s.slot === o.slot);
    demoArenaState.run = {
      slot: o.slot, character_id: row?.character?.id ?? 901,
      target_level: o.level, environment: o.environment,
      difficulty: o.difficulty, phase: "leveling", fights: 0, wins: 0,
    };
    // The play surface needs a fighter to draw before the bout arrives.
    const out: ServerEvent[] = [
      { t: "entered", resumed: false, arena: true }, lexicon, sheet, party,
    ];
    if (o.level > 1 && !o.reuse) {
      out.push({ t: "narration",
                 text: `*The Grounds raise ${row?.character?.name ?? "your fighter"} `
                       + `toward level ${o.level}. Choose what they become.*` });
      out.push(demoLevelUp);
      return out;
    }
    return [...out, ...this.stall()];
  },
  /** The Quartermaster, between the climb and the sand. */
  stall(): ServerEvent[] {
    const run = demoArenaState.run;
    if (!run) return [];
    run.phase = "outfitting";
    run.result = null;
    return [
      { t: "narration",
        text: "*The Quartermaster's stall stands between you and the sand. "
              + "Conjured coin is yours to spend — buy what this build is meant "
              + "to be holding, strap it on, and step through.*" },
      { t: "arena", state: demoArenaApi.state() },
    ];
  },
  /** Buy the cart (the offline stall trusts its own prices) and fight. */
  outfit(cart: ArenaOutfitLine[], _equip: ArenaEquipLine[]): ServerEvent[] {
    demoCart = cart.flatMap((line) => {
      const it = demoStock.find((s) => s.slug === line.slug);
      if (!it) return [];
      return [{
        slug: it.slug, name: it.name, quantity: line.quantity,
        cost_gp: it.cost_gp, line_gp: it.cost_gp * line.quantity,
        equipped: !!line.equipped, attuned: !!line.attuned,
        kind: it.kind, rarity: it.rarity ?? null,
        attunement: !!it.attunement, equippable: it.equippable,
      }];
    });
    const carried = demoCart.map((l) => l.name).join(", ");
    return [
      { t: "narration",
        text: carried
          ? `*You leave the stall carrying ${carried}.*`
          : "*You take nothing from the stall and step through as you are.*" },
      ...this.fight(),
    ];
  },
  fight(environment?: string): ServerEvent[] {
    const run = demoArenaState.run;
    if (!run) return [];
    if (environment) run.environment = environment;
    run.phase = "fighting";
    run.result = null;
    run.roster = "Three Goblin Warriors";
    run.roster_reads = run.difficulty;
    run.fights = (run.fights ?? 0) + 1;
    const env = demoEnvs.find((e) => e.slug === run.environment);
    demoCombatStage = 1;
    return [
      { t: "narration",
        text: `**${env?.name ?? "The Grounds"}** — ${env?.blurb ?? ""}\n\n`
              + "The wards close. Three Goblin Warriors take shape across the "
              + "ground from you." },
      { t: "combat", encounter: demoEncounter(1) },
      { t: "vtt", scene: demoVtt(1) },
      { t: "arena", state: demoArenaApi.state() },
    ];
  },
  /** The offline feed resolves a bout the moment the player swings. */
  resolve(): ServerEvent[] {
    const run = demoArenaState.run;
    if (!run || run.phase !== "fighting") return [];
    run.phase = "resolved";
    run.result = "victory";
    run.wins = (run.wins ?? 0) + 1;
    return [
      { t: "narration",
        text: "*The last of them goes down. The wards dim, and the Grounds go "
              + "quiet — you are whole again, and ready for the next.*" },
      { t: "combat", encounter: null },
      { t: "arena", state: demoArenaApi.state() },
    ];
  },
  leave(): ServerEvent[] {
    if (demoArenaState.run) {
      demoArenaState.run.phase = "idle";
      demoArenaState.run.result = null;
    }
    return [
      { t: "combat", encounter: null },
      { t: "vtt", scene: null },
      { t: "arena", state: demoArenaApi.state() },
    ];
  },
};

export const demoScript = {
  chronicle: demoChronicle,
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
    locale,
    party,
    {
      t: "narration",
      text:
        "The road out of Greenfields narrows where the alder trees crowd close, " +
        "and the Wispering Mill rises ahead — sails torn, turning anyway in a wind " +
        "you cannot feel. A goblin warrior's tracks cross the mud at your feet, " +
        "fresh enough that water still seeps into them. Sylvaine reads them " +
        "twice; Thistlewick will not come nearer, and swears by Aurelion that " +
        "Vashra the Unlit has been here before you.",
    } as ServerEvent,
    {
      t: "speech",
      who: "Old Marla",
      script: "dwarven",
      text: "\"Millers grind no grain at midnight,\" she told you over her cups, " +
        "\"and the ones that do aren't millers.\"",
    } as ServerEvent,
    {
      // A rendered establishing shot. In play this is guaranteed on arriving
      // somewhere new (_maybe_render_arrival) and can also come from an
      // [[IMAGE: ...]] hook; offline we stand in a shipped background so the
      // pane is actually exercised.
      t: "scene",
      url: "/assets/backgrounds/play-cavern.jpg",
    } as ServerEvent,
    {
      t: "suggest",
      actions: ["follow the tracks", "sneak up to the mill door",
                "call out to whoever is inside"],
    } as ServerEvent,
  ],
  respond(action: string): ServerEvent[] {
    if (/level ?up/i.test(action)) {
      return [demoLevelUp];
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
        {
          t: "suggest",
          actions: ["shoot the nearer goblin", "listen to what they are saying",
                    "back away quietly"],
        },
      ];
    }
    if (/travel|journey|set out|road to|ride to|go to|head to|leave for/i.test(action)) {
      return [
        {
          t: "narration",
          text:
            "You could be on the road within the hour. The carter at the gate " +
            "knows three ways to Millbrook, and has opinions about all of them.",
        },
        {
          t: "routes",
          routes: [
            { id: "the-high-road", label: "the high road", destination: "Millbrook",
              miles: 25.1, days: 1.4, terrain: "grassland", danger: "low", nav_dc: 10,
              blurb: "Patrolled and well-kept. Longer, and you will meet other travellers." },
            { id: "the-old-track", label: "the old track", destination: "Millbrook",
              miles: 18.6, days: 1, terrain: "forest", danger: "moderate", nav_dc: 15,
              blurb: "The way most take. Rutted, and quiet in the wrong places." },
            { id: "the-shortcut", label: "the shortcut", destination: "Millbrook",
              miles: 13.4, days: 0.7, terrain: "forest", danger: "high", nav_dc: 15,
              blurb: "Half-forgotten, and it cuts hard through country that has no reason to be kind." },
          ],
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
