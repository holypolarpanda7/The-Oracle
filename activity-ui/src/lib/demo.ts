import type { ServerEvent } from "./types";

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
    name: "Kara Emberfall",
    subtitle: "Level 5 · Dragonborn Bard",
    hp: 32,
    hp_max: 40,
    ac: 14,
    stats: { STR: 10, DEX: 16, CON: 14, INT: 12, WIS: 11, CHA: 18 },
    skills: ["Persuasion +7", "Performance +7", "Perception +3", "Deception +7"],
    inventory: [
      { name: "Rapier", qty: 1, type: "Martial", brief: "A finesse blade — quick, precise, a duelist's friend." },
      { name: "Spellbook", qty: 1, type: "Wondrous", interactive: "spellbook" as const,
        brief: "A leather tome of inscribed magic." },
      { name: "Cloak of Billowing", qty: 1, type: "Wondrous", rarity: "Common",
        brief: "Billows dramatically as a bonus action." },
      { name: "Lute", qty: 1, type: "Instrument", brief: "A bard's most trusted companion." },
      { name: "Leather Armor", qty: 1, type: "Light Armor" },
      { name: "Rations", qty: 5, type: "Gear" },
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
    if (/shoot|attack|fire|loose/i.test(action)) {
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
            "burying itself in the alder behind her.",
        },
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
