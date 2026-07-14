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
    { text: "Fireball", kind: "magic" },
    { text: "Umbral Sight", kind: "magic" },
    { text: "longbow", kind: "item" },
    { text: "Greenfields", kind: "place" },
    { text: "Wispering Mill", kind: "place" },
  ],
};

const sheet: ServerEvent = {
  t: "sheet",
  sheet: {
    name: "Kara Emberfall",
    subtitle: "Level 3 Ranger (Gloom Stalker) · Custom Lineage",
    hp: 21,
    hp_max: 28,
    ac: 15,
    stats: { STR: 12, DEX: 17, CON: 14, INT: 10, WIS: 15, CHA: 8 },
    skills: ["Stealth +7", "Perception +4", "Survival +4", "Athletics +3"],
    inventory: ["Longbow", "Shortsword ×2", "Leather armor", "Rope (50 ft)", "Rations ×5"],
    gold: 37,
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
