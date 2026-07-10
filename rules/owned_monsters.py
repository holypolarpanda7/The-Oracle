"""Hand-authored, non-SRD monster & NPC catalog (OWNED_SOURCE).

The open 5e SRD dataset already covers most classic creatures, so this module
*expands* the roster with original homebrew threats, public-domain folklore
creatures, and extra NPC stat blocks — all written from scratch as concise
mechanical facts, never copied from any copyrighted book. In particular this
deliberately contains **no** Wizards-of-the-Coast Product Identity monsters
(beholders, mind flayers, githyanki, displacer beasts, etc.).

Entries map straight onto the shared ``rules_monster`` table, so they flow through
the same search / mention-detection / stat-brief paths the DM brain already uses.
``seed_owned_monsters`` is offline and idempotent (upsert by ``index_slug``).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from .models import Monster, OWNED_SOURCE

# Functional lookup tables (plain game math, not creative expression).
XP_BY_CR: dict[float, int] = {
    0: 10, 0.125: 25, 0.25: 50, 0.5: 100, 1: 200, 2: 450, 3: 700, 4: 1100,
    5: 1800, 6: 2300, 7: 2900, 8: 3900, 9: 5000, 10: 5900, 11: 7200, 12: 8400,
    13: 10000, 14: 11500, 15: 13000, 16: 15000, 17: 18000, 18: 20000,
    19: 22000, 20: 25000, 21: 33000, 22: 41000, 23: 50000, 24: 62000,
}


def _pb_for_cr(cr: float) -> int:
    if cr <= 4:
        return 2
    if cr <= 8:
        return 3
    if cr <= 12:
        return 4
    if cr <= 16:
        return 5
    if cr <= 20:
        return 6
    return 7


def _atk(name: str, bonus: int, dice: str, dtype: str, desc: str = "") -> dict:
    # A utility (non-attack) action: no to-hit / damage line.
    if dice in ("0", "", None) and not dtype:
        return {"name": name, "desc": desc}
    return {
        "name": name,
        "attack_bonus": bonus,
        "damage": [{"damage_dice": dice, "damage_type": {"name": dtype}}],
        "desc": desc,
    }


def _trait(name: str, desc: str) -> dict:
    return {"name": name, "desc": desc}


def _mon(slug, name, size, type_, align, ac, hp, hd, speed, stats, cr, *,
         ac_desc=None, senses=None, languages=None, traits=None, actions=None) -> dict:
    s, d, c, i, w, ch = stats
    return {
        "index_slug": slug,
        "name": name,
        "size": size,
        "type": type_,
        "alignment": align,
        "armor_class": ac,
        "ac_desc": ac_desc,
        "hit_points": hp,
        "hit_dice": hd,
        "strength": s, "dexterity": d, "constitution": c,
        "intelligence": i, "wisdom": w, "charisma": ch,
        "challenge_rating": cr,
        "proficiency_bonus": _pb_for_cr(cr),
        "xp": XP_BY_CR.get(cr, 0),
        "speed": speed,
        "senses": senses or {"passive_perception": 10},
        "languages": languages,
        "special_abilities": traits or [],
        "actions": actions or [],
        "source": OWNED_SOURCE,
    }


# ---------------------------------------------------------------------------
# The catalog. Grouped for readability; all original / folklore, non-PI.
# ---------------------------------------------------------------------------
OWNED_MONSTERS: list[dict] = [

    # ----- NPCs: martial & common threats -----
    _mon("town-guard-captain", "Town Guard Captain", "Medium", "humanoid",
         "lawful neutral", 18, 65, "10d8+20", {"walk": "30 ft."},
         (16, 12, 15, 11, 13, 14), 3, ac_desc="chain mail, shield",
         senses={"passive_perception": 13}, languages="Common",
         traits=[_trait("Rally the Watch",
                        "As a bonus action, an ally within 30 ft. that can hear the "
                        "captain gains 5 temporary hit points.")],
         actions=[_atk("Longsword", 5, "1d8+3", "Slashing"),
                  _atk("Heavy Crossbow", 3, "1d10+1", "Piercing")]),

    _mon("mercenary-veteran", "Mercenary Veteran", "Medium", "humanoid",
         "any", 16, 45, "6d8+18", {"walk": "30 ft."},
         (15, 13, 16, 10, 11, 10), 2, ac_desc="scale mail",
         languages="Common", senses={"passive_perception": 10},
         actions=[_atk("Battleaxe", 4, "1d8+2", "Slashing",
                       "The veteran makes two attacks with its battleaxe.")]),

    _mon("highwayman", "Highwayman", "Medium", "humanoid", "chaotic neutral",
         14, 27, "5d8+5", {"walk": "30 ft."}, (12, 16, 12, 10, 11, 13), 1,
         ac_desc="studded leather", languages="Common",
         senses={"passive_perception": 11},
         traits=[_trait("Ambusher",
                        "In the first round of combat, the highwayman has advantage "
                        "on attacks against any creature that has not yet acted.")],
         actions=[_atk("Rapier", 5, "1d8+3", "Piercing"),
                  _atk("Light Crossbow", 5, "1d8+3", "Piercing")]),

    _mon("street-thug", "Street Thug", "Medium", "humanoid", "neutral evil",
         12, 16, "3d8+3", {"walk": "30 ft."}, (14, 12, 13, 9, 10, 9), 0.25,
         ac_desc="leather armor", languages="Common",
         actions=[_atk("Club", 4, "1d4+2", "Bludgeoning"),
                  _atk("Thrown Brick", 3, "1d6+1", "Bludgeoning")]),

    _mon("arena-champion", "Arena Champion", "Medium", "humanoid", "any",
         17, 90, "12d8+36", {"walk": "30 ft."}, (18, 14, 16, 10, 12, 13), 5,
         ac_desc="breastplate", languages="Common",
         senses={"passive_perception": 11},
         traits=[_trait("Crowd's Favor",
                        "Once per turn the champion can reroll a missed melee attack; "
                        "it must use the new roll.")],
         actions=[_atk("Greatsword", 7, "2d6+4", "Slashing",
                       "The champion makes two greatsword attacks."),
                  _atk("Chained Javelin", 7, "1d6+4", "Piercing",
                       "Ranged 20/60 ft.; on a hit the target is pulled 10 ft. toward "
                       "the champion.")]),

    _mon("royal-knight-commander", "Royal Knight-Commander", "Medium",
         "humanoid", "lawful good", 20, 120, "16d8+48", {"walk": "30 ft."},
         (19, 11, 17, 12, 14, 16), 8, ac_desc="plate, shield",
         languages="Common", senses={"passive_perception": 12},
         traits=[_trait("Bulwark",
                        "Allies within 10 ft. of the commander have half cover.")],
         actions=[_atk("Warhammer", 8, "1d8+4", "Bludgeoning",
                       "Two attacks; each hit lets the commander push a Large or "
                       "smaller target 5 ft."),
                  _atk("Commanding Shout", 0, "0", "",
                       "One ally within 30 ft. can immediately move up to its speed "
                       "and make one weapon attack as a reaction.")]),

    _mon("bounty-hunter", "Bounty Hunter", "Medium", "humanoid", "any",
         15, 52, "8d8+16", {"walk": "30 ft."}, (13, 17, 14, 12, 15, 11), 4,
         ac_desc="studded leather", languages="Common, Thieves' cant",
         senses={"passive_perception": 15, "darkvision": "30 ft."},
         traits=[_trait("Manacle Shot",
                        "On a critical hit with the net-launcher, the target is "
                        "restrained (escape DC 14).")],
         actions=[_atk("Twin Shortswords", 6, "1d6+3", "Piercing",
                       "Two attacks; sneak-attack style +2d6 to the first hit against "
                       "a surprised or flanked target."),
                  _atk("Net Launcher", 6, "0", "",
                       "Ranged 20/60 ft.; a Large or smaller target is restrained.")]),

    _mon("pit-fighter", "Pit Fighter", "Medium", "humanoid", "any",
         14, 39, "6d8+12", {"walk": "30 ft."}, (17, 13, 15, 8, 10, 9), 2,
         ac_desc="unarmored defense", languages="Common",
         traits=[_trait("Brawler",
                        "The pit fighter's unarmed strikes count as magical and it "
                        "has advantage on checks to grapple.")],
         actions=[_atk("Spiked Gauntlet", 5, "1d6+3", "Piercing",
                       "Two attacks."),
                  _atk("Slam & Grapple", 5, "1d4+3", "Bludgeoning")]),

    # ----- NPCs: casters & specialists -----
    _mon("hedge-witch", "Hedge Witch", "Medium", "humanoid", "neutral",
         12, 33, "6d8+6", {"walk": "30 ft."}, (9, 14, 13, 15, 16, 12), 3,
         languages="Common, Sylvan", senses={"passive_perception": 13},
         traits=[_trait("Hex Ward",
                        "The first time each turn the witch takes damage, it reduces "
                        "that damage by 5.")],
         actions=[_atk("Thornlash", 5, "2d6+2", "Piercing",
                       "Ranged 60 ft. spell attack."),
                  _atk("Withering Curse", 0, "0", "",
                       "One creature within 30 ft. makes a DC 13 Con save or takes "
                       "3d6 necrotic damage and has disadvantage on its next attack.")]),

    _mon("court-mage", "Court Mage", "Medium", "humanoid", "any", 15, 71,
         "13d8+13", {"walk": "30 ft."}, (9, 14, 13, 18, 12, 13), 6,
         ac_desc="mage armor", languages="Common and two others",
         senses={"passive_perception": 11},
         traits=[_trait("Arcane Recovery",
                        "Three times per day the mage can convert a reaction into a "
                        "counter that halves incoming spell damage to itself.")],
         actions=[_atk("Arcane Bolt", 8, "3d8", "Force",
                       "Ranged 120 ft.; the mage makes two bolt attacks."),
                  _atk("Cone of Frost", 0, "0", "",
                       "15-ft. cone, DC 15 Dex save, 6d8 cold (half on success); the "
                       "area becomes difficult terrain until the end of its next turn.")]),

    _mon("war-priest", "War Priest", "Medium", "humanoid", "any", 18, 68,
         "9d8+27", {"walk": "30 ft."}, (16, 10, 16, 11, 17, 13), 5,
         ac_desc="chain mail, shield", languages="Common",
         senses={"passive_perception": 13},
         traits=[_trait("Battle Blessing",
                        "As a bonus action, an ally within 30 ft. adds 1d6 to its next "
                        "attack roll or saving throw.")],
         actions=[_atk("Maul", 6, "2d6+3", "Bludgeoning", "Two attacks."),
                  _atk("Searing Rebuke", 0, "0", "",
                       "One creature within 60 ft. makes a DC 14 Wis save or takes "
                       "4d6 radiant damage and is blinded until the end of its turn.")]),

    _mon("plague-doctor", "Plague Doctor", "Medium", "humanoid", "neutral",
         13, 44, "8d8+8", {"walk": "30 ft."}, (10, 14, 13, 16, 14, 10), 3,
         languages="Common", senses={"passive_perception": 12},
         traits=[_trait("Miasma Ward",
                        "The plague doctor is immune to disease and poison damage.")],
         actions=[_atk("Bone Saw", 4, "1d8+2", "Slashing"),
                  _atk("Vial of Contagion", 4, "2d6", "Poison",
                       "Ranged 20 ft.; DC 13 Con save or poisoned for 1 minute and "
                       "contract a random disease on a second failure.")]),

    _mon("forest-warden", "Forest Warden", "Medium", "humanoid",
         "neutral good", 15, 49, "9d8+9", {"walk": "35 ft."},
         (13, 16, 13, 12, 16, 11), 3, ac_desc="hide armor",
         languages="Common, Elvish, Druidic",
         senses={"passive_perception": 16, "darkvision": "30 ft."},
         traits=[_trait("Natural Ambusher",
                        "The warden has advantage on Stealth in natural terrain and "
                        "deals +1d8 damage to a creature it surprises.")],
         actions=[_atk("Longbow", 5, "1d8+3", "Piercing",
                       "Two attacks; range 150/600 ft."),
                  _atk("Handaxe", 5, "1d6+3", "Slashing")]),

    _mon("shadow-agent", "Shadow Agent", "Medium", "humanoid", "any",
         15, 55, "10d8+10", {"walk": "35 ft."}, (11, 18, 12, 14, 13, 12), 4,
         ac_desc="studded leather", languages="Common, Thieves' cant",
         senses={"passive_perception": 15},
         traits=[_trait("Evasive",
                        "When the agent succeeds on a Dex save for half damage it "
                        "takes none instead, and none on a failure."),
                 _trait("Shadow Step",
                        "As a bonus action while in dim light or darkness, teleport "
                        "up to 30 ft. to an unoccupied space it can see.")],
         actions=[_atk("Poisoned Dagger", 7, "1d4+4", "Piercing",
                       "Two attacks; +3d6 to a surprised or flanked target, and the "
                       "target makes a DC 13 Con save or is poisoned.")]),

    _mon("desert-raider", "Desert Raider", "Medium", "humanoid",
         "chaotic neutral", 14, 22, "4d8+4", {"walk": "30 ft."},
         (13, 15, 12, 10, 11, 11), 0.5, ac_desc="leather, shield",
         languages="Common", senses={"passive_perception": 11},
         actions=[_atk("Scimitar", 4, "1d6+2", "Slashing"),
                  _atk("Shortbow", 4, "1d6+2", "Piercing")]),

    _mon("dockside-smuggler", "Dockside Smuggler", "Medium", "humanoid",
         "neutral", 13, 18, "4d8", {"walk": "30 ft."},
         (11, 15, 11, 12, 10, 13), 0.25, ac_desc="leather armor",
         languages="Common, Thieves' cant",
         actions=[_atk("Gutting Knife", 4, "1d4+2", "Piercing"),
                  _atk("Smoke Pellet", 0, "0", "",
                       "Creates a 10-ft. cloud of obscuring smoke for 1 round.")]),

    # ----- Beasts & animals (original names) -----
    _mon("ridgeback-wolf", "Ridgeback Wolf", "Medium", "beast",
         "unaligned", 13, 26, "4d8+8", {"walk": "45 ft."},
         (14, 15, 15, 3, 12, 7), 0.5, ac_desc="natural armor",
         senses={"passive_perception": 13, "darkvision": "30 ft."},
         traits=[_trait("Pack Tactics",
                        "Advantage on an attack if an ally is within 5 ft. of the "
                        "target.")],
         actions=[_atk("Bite", 4, "2d6+2", "Piercing",
                       "If the target is Large or smaller it makes a DC 12 Str save "
                       "or is knocked prone.")]),

    _mon("glacier-bear", "Glacier Bear", "Large", "beast", "unaligned",
         13, 51, "6d10+18", {"walk": "40 ft.", "swim": "30 ft."},
         (19, 10, 16, 2, 13, 7), 3, ac_desc="natural armor",
         senses={"passive_perception": 13},
         traits=[_trait("Cold Hide",
                        "The glacier bear is resistant to cold damage.")],
         actions=[_atk("Maul", 6, "1d8+4", "Slashing",
                       "Two claw attacks and one bite."),
                  _atk("Bite", 6, "1d10+4", "Piercing")]),

    _mon("thornback-boar", "Thornback Boar", "Medium", "beast", "unaligned",
         12, 22, "3d8+9", {"walk": "40 ft."}, (16, 11, 16, 2, 9, 5), 1,
         ac_desc="natural armor", senses={"passive_perception": 9},
         traits=[_trait("Charge",
                        "If it moves 20 ft. straight toward a target and hits with a "
                        "gore, +1d6 damage and the target makes a DC 13 Str save or "
                        "is knocked prone."),
                 _trait("Barbed Hide",
                        "A creature that hits the boar with a melee attack while "
                        "within 5 ft. takes 2 piercing damage.")],
         actions=[_atk("Gore", 5, "1d8+3", "Piercing")]),

    _mon("marsh-serpent", "Marsh Serpent", "Large", "beast", "unaligned",
         13, 45, "6d10+12", {"walk": "20 ft.", "swim": "40 ft."},
         (17, 15, 14, 2, 12, 5), 2, ac_desc="natural armor",
         senses={"passive_perception": 11, "blindsight": "10 ft."},
         actions=[_atk("Constrict", 5, "1d8+3", "Bludgeoning",
                       "The target is grappled (escape DC 13) and restrained."),
                  _atk("Bite", 5, "1d6+3", "Piercing",
                       "DC 12 Con save or take 2d6 poison damage.")]),

    _mon("ashfeather-raptor", "Ashfeather Raptor", "Medium", "beast",
         "unaligned", 13, 19, "3d8+6", {"walk": "20 ft.", "fly": "60 ft."},
         (13, 16, 14, 4, 14, 6), 0.5, ac_desc="natural armor",
         senses={"passive_perception": 16},
         traits=[_trait("Keen Sight", "Advantage on sight-based Perception.")],
         actions=[_atk("Talons", 5, "2d4+3", "Slashing",
                       "Flyby: the raptor doesn't provoke opportunity attacks when "
                       "it flies out of reach.")]),

    _mon("cavern-stalker", "Cavern Stalker", "Large", "beast", "unaligned",
         14, 33, "6d10", {"walk": "30 ft.", "climb": "30 ft."},
         (14, 16, 11, 2, 11, 4), 1, ac_desc="natural armor",
         senses={"passive_perception": 10, "blindsight": "10 ft.",
                 "darkvision": "60 ft."},
         traits=[_trait("Web Walker", "Ignores movement restrictions from webbing.")],
         actions=[_atk("Bite", 5, "1d8+3", "Piercing",
                       "DC 12 Con save or take 2d6 poison and be poisoned."),
                  _atk("Web", 5, "0", "",
                       "Ranged 30/60 ft.; a Large or smaller target is restrained "
                       "(escape or break DC 12).")]),

    _mon("frost-elk", "Frost Elk", "Large", "beast", "unaligned", 12, 30,
         "4d10+8", {"walk": "50 ft."}, (17, 12, 15, 3, 13, 8), 1,
         senses={"passive_perception": 13},
         traits=[_trait("Charge",
                        "Moving 20 ft. before a ram adds 2d6 and may knock the target "
                        "prone (DC 13 Str).")],
         actions=[_atk("Ram", 5, "1d8+3", "Bludgeoning"),
                  _atk("Hooves", 5, "2d4+3", "Bludgeoning")]),

    _mon("crag-roc", "Crag Roc", "Huge", "beast", "unaligned", 14, 84,
         "8d12+32", {"walk": "20 ft.", "fly": "80 ft."},
         (22, 10, 18, 3, 12, 9), 6, ac_desc="natural armor",
         senses={"passive_perception": 15},
         actions=[_atk("Talons", 9, "4d6+6", "Slashing",
                       "Two attacks; a Large or smaller target is grappled "
                       "(escape DC 17) and can be carried aloft."),
                  _atk("Beak", 9, "3d8+6", "Piercing")]),

    # ----- Undead (original / folklore) -----
    _mon("draugr", "Draugr", "Medium", "undead", "chaotic evil", 15, 52,
         "7d8+21", {"walk": "30 ft.", "swim": "20 ft."},
         (17, 11, 16, 9, 11, 12), 3, ac_desc="rusted mail",
         languages="the languages it knew in life",
         senses={"passive_perception": 10, "darkvision": "60 ft."},
         traits=[_trait("Grave Cold",
                        "A creature that starts its turn grappling the draugr takes "
                        "1d6 cold damage."),
                 _trait("Undead Fortitude",
                        "When reduced to 0 HP by non-radiant damage, DC 13 Con save "
                        "to drop to 1 HP instead.")],
         actions=[_atk("Barrow Blade", 5, "1d8+3", "Slashing",
                       "Plus 1d6 cold. Two attacks."),
                  _atk("Draining Grasp", 5, "2d6+3", "Necrotic",
                       "The draugr regains hit points equal to the damage dealt.")]),

    _mon("barrow-wight", "Barrow-Wight", "Medium", "undead", "lawful evil",
         14, 45, "6d8+18", {"walk": "30 ft."}, (15, 14, 16, 10, 13, 15), 4,
         ac_desc="studded leather", languages="Common",
         senses={"passive_perception": 13, "darkvision": "60 ft."},
         traits=[_trait("Sunlight Sensitivity",
                        "Disadvantage on attacks and Perception in sunlight.")],
         actions=[_atk("Grave-Iron Sword", 5, "1d8+2", "Slashing",
                       "Two attacks, or one sword and one life drain."),
                  _atk("Life Drain", 4, "1d6+2", "Necrotic",
                       "DC 14 Con save or hit-point maximum reduced by the damage "
                       "until a long rest; a humanoid slain this way rises as a "
                       "gravebound thrall.")]),

    _mon("gravebound-thrall", "Gravebound Thrall", "Medium", "undead",
         "neutral evil", 9, 22, "3d8+9", {"walk": "25 ft."},
         (14, 8, 16, 4, 6, 5), 0.5, languages="understands its master",
         senses={"passive_perception": 8, "darkvision": "60 ft."},
         traits=[_trait("Relentless",
                        "If the thrall is reduced to 0 HP but not by radiant damage "
                        "or a critical hit, DC 10 Con save to drop to 1 HP instead.")],
         actions=[_atk("Rotting Slam", 4, "1d8+2", "Bludgeoning")]),

    _mon("ashen-revenant", "Ashen Revenant", "Medium", "undead",
         "neutral", 14, 82, "11d8+33", {"walk": "40 ft."},
         (18, 14, 16, 12, 14, 16), 6, languages="Common",
         senses={"passive_perception": 14, "darkvision": "60 ft."},
         traits=[_trait("Vengeful Purpose",
                        "The revenant knows the direction to the creature that "
                        "wronged it and has advantage on attacks against it."),
                 _trait("Ashen Reform",
                        "When it drops to 0 HP its body crumbles to ash and reforms "
                        "with full HP after 24 hours unless its purpose is fulfilled "
                        "or it is destroyed by holy means.")],
         actions=[_atk("Cinder Fists", 8, "2d6+4", "Bludgeoning",
                       "Two attacks; plus 1d6 fire."),
                  _atk("Grasp of Retribution", 0, "0", "",
                       "A creature within 10 ft. makes a DC 15 Str save or is pulled "
                       "adjacent and grappled; while grappled it takes 2d6 fire at "
                       "the start of each of its turns.")]),

    _mon("wailing-shade", "Wailing Shade", "Medium", "undead",
         "chaotic evil", 12, 36, "8d8", {"walk": "0 ft.", "fly": "40 ft."},
         (6, 15, 10, 10, 12, 15), 3, languages="understands Common",
         senses={"passive_perception": 11, "darkvision": "60 ft."},
         traits=[_trait("Incorporeal",
                        "Can move through creatures and objects as difficult terrain; "
                        "takes 1d10 force if it ends its turn inside an object."),
                 _trait("Sunlight Weakness",
                        "In sunlight the shade has disadvantage on everything.")],
         actions=[_atk("Chilling Touch", 4, "3d6", "Cold"),
                  _atk("Death Wail", 0, "0", "",
                       "Recharge 5-6: each creature within 30 ft. makes a DC 12 Con "
                       "save or takes 3d6 psychic and is frightened for 1 minute.")]),

    _mon("hollow-monarch", "Hollow Monarch", "Medium", "undead",
         "lawful evil", 17, 165, "22d8+66", {"walk": "30 ft."},
         (16, 12, 17, 16, 14, 19), 11, ac_desc="crown-forged plate",
         languages="Common and two others",
         senses={"passive_perception": 16, "darkvision": "120 ft."},
         traits=[_trait("Legendary Resistance (2/Day)",
                        "If the monarch fails a save, it can choose to succeed."),
                 _trait("Crown of Dominion",
                        "Undead within 60 ft. have advantage on saves and add 1d4 to "
                        "their attacks."),
                 _trait("Turn Immunity", "The monarch cannot be turned.")],
         actions=[_atk("Scepter of Ruin", 9, "2d8+3", "Bludgeoning",
                       "Two attacks; plus 2d6 necrotic and the target can't regain "
                       "hit points until the start of its next turn."),
                  _atk("Command the Dead", 0, "0", "",
                       "Up to three undead allies the monarch can see may each move "
                       "up to their speed and make one attack as a reaction.")]),

    # ----- Fey & folklore (public domain) -----
    _mon("redcap", "Redcap", "Small", "fey", "chaotic evil", 13, 45,
         "7d6+21", {"walk": "35 ft."}, (16, 13, 17, 10, 9, 7), 3,
         ac_desc="natural armor", languages="Common, Sylvan",
         senses={"passive_perception": 9, "darkvision": "60 ft."},
         traits=[_trait("Outpace",
                        "The redcap can Dash as a bonus action and ignores difficult "
                        "terrain when it does."),
                 _trait("Iron Boots",
                        "A creature the redcap moves through takes 1d4 bludgeoning.")],
         actions=[_atk("Wicked Sickle", 5, "2d8+3", "Slashing",
                       "Two attacks; a prone target takes an extra 1d8.")]),

    _mon("kelpie", "Kelpie", "Large", "fey", "neutral evil", 13, 59,
         "7d10+21", {"walk": "40 ft.", "swim": "60 ft."},
         (17, 14, 16, 11, 13, 16), 4, ac_desc="natural armor",
         languages="Sylvan, Aquan", senses={"passive_perception": 13},
         traits=[_trait("Beguiling Shape",
                        "The kelpie can appear as a beautiful horse or a fair "
                        "stranger; a creature that touches it in this form makes a "
                        "DC 14 Wis save or is charmed and adheres to the kelpie."),
                 _trait("Amphibious", "Can breathe air and water.")],
         actions=[_atk("Hooves", 6, "2d6+3", "Bludgeoning"),
                  _atk("Drowning Pull", 0, "0", "",
                       "A charmed or grappled creature within 5 ft. is dragged into "
                       "deep water; it must hold its breath or begin drowning.")]),

    _mon("grindylow", "Grindylow", "Small", "fey", "chaotic evil", 12, 21,
         "6d6", {"walk": "10 ft.", "swim": "40 ft."},
         (9, 15, 11, 8, 12, 8), 0.5, languages="Aquan, Sylvan",
         senses={"passive_perception": 11, "darkvision": "30 ft."},
         traits=[_trait("Pack Hunter",
                        "Advantage on attacks against a creature within 5 ft. of an "
                        "ally.")],
         actions=[_atk("Grasping Tendrils", 4, "1d6+2", "Bludgeoning",
                       "A Medium or smaller target is grappled (escape DC 12) and "
                       "pulled 5 ft. toward water.")]),

    _mon("corpse-candle", "Corpse Candle", "Tiny", "fey", "chaotic evil",
         14, 17, "5d4+5", {"walk": "0 ft.", "fly": "50 ft."},
         (1, 18, 12, 12, 14, 11), 2, languages="—",
         senses={"passive_perception": 12, "darkvision": "120 ft."},
         traits=[_trait("Ephemeral", "Can't wear or carry anything."),
                 _trait("False Beacon",
                        "The corpse candle can mimic a lantern or campfire to lure "
                        "travelers; it is invisible when its light is extinguished.")],
         actions=[_atk("Shock", 7, "3d8", "Lightning",
                       "The corpse candle turns invisible until it attacks again.")]),

    _mon("briar-nymph", "Briar Nymph", "Medium", "fey", "neutral", 13, 39,
         "6d8+12", {"walk": "30 ft."}, (12, 16, 14, 13, 15, 16), 3,
         languages="Sylvan, Common", senses={"passive_perception": 14},
         traits=[_trait("Bound to the Grove",
                        "While within 120 ft. of its warded tree the nymph regains "
                        "10 hit points at the start of each of its turns.")],
         actions=[_atk("Thorn Whip", 5, "2d6+3", "Piercing",
                       "Range 30 ft.; pulls a Large or smaller target 10 ft."),
                  _atk("Entangling Roots", 0, "0", "",
                       "20-ft. radius within 60 ft.; DC 13 Str save or restrained "
                       "until the end of the nymph's next turn.")]),

    # ----- Elementals & constructs (original) -----
    _mon("cinder-wraith", "Cinder Wraith", "Medium", "elemental",
         "neutral evil", 13, 45, "6d8+18", {"walk": "40 ft.", "fly": "30 ft."},
         (10, 16, 16, 6, 10, 9), 4, languages="Ignan",
         senses={"passive_perception": 10, "darkvision": "60 ft."},
         traits=[_trait("Heated Form",
                        "A creature that touches it or hits it with a melee attack "
                        "within 5 ft. takes 1d6 fire."),
                 _trait("Water Susceptibility",
                        "The wraith takes 1 cold damage for every 5 ft. it moves "
                        "through water, and 3d6 if doused.")],
         actions=[_atk("Ember Lash", 6, "2d6+3", "Fire", "Two attacks."),
                  _atk("Flare", 0, "0", "",
                       "Recharge 6: 10-ft. burst, DC 14 Dex save, 4d6 fire (half on "
                       "success).")]),

    _mon("stoneward-sentinel", "Stoneward Sentinel", "Large", "construct",
         "unaligned", 17, 95, "10d10+40", {"walk": "25 ft."},
         (20, 8, 18, 3, 11, 1), 6, ac_desc="natural armor",
         languages="understands its creator's orders",
         senses={"passive_perception": 10, "darkvision": "60 ft."},
         traits=[_trait("Immutable Form", "Immune to any spell that alters its form."),
                 _trait("Siege Monster", "Deals double damage to objects/structures.")],
         actions=[_atk("Slam", 8, "2d10+5", "Bludgeoning",
                       "Two attacks; a Large or smaller target is pushed 10 ft."),
                  _atk("Ground Slam", 0, "0", "",
                       "Recharge 5-6: each creature within 10 ft. makes a DC 15 Dex "
                       "save or takes 3d10 bludgeoning and is knocked prone.")]),

    _mon("clockwork-hound", "Clockwork Hound", "Medium", "construct",
         "unaligned", 15, 26, "4d8+8", {"walk": "50 ft."},
         (14, 15, 14, 4, 11, 3), 1, ac_desc="natural armor",
         languages="—",
         senses={"passive_perception": 12, "darkvision": "60 ft."},
         traits=[_trait("Tireless Pursuit",
                        "The hound ignores exhaustion and never needs to rest; it has "
                        "advantage to track a scent it has been given.")],
         actions=[_atk("Snapping Jaws", 4, "1d10+2", "Piercing",
                       "A Medium or smaller target is grappled (escape DC 12).")]),

    _mon("tempest-mote", "Tempest Mote", "Small", "elemental", "neutral",
         15, 22, "5d6+5", {"walk": "0 ft.", "fly": "60 ft. (hover)"},
         (6, 18, 12, 6, 10, 6), 1, languages="Auran",
         senses={"passive_perception": 10},
         traits=[_trait("Air Form",
                        "Can move through a space as narrow as 1 inch without "
                        "squeezing; resistant to nonmagical weapon damage.")],
         actions=[_atk("Gale Slam", 6, "2d6+4", "Bludgeoning",
                       "A Medium or smaller target makes a DC 13 Str save or is "
                       "pushed 10 ft.")]),

    _mon("brineborn-horror", "Brineborn Horror", "Large", "elemental",
         "neutral evil", 14, 76, "8d10+32", {"walk": "20 ft.", "swim": "50 ft."},
         (18, 12, 18, 7, 12, 8), 6, ac_desc="natural armor",
         languages="Aquan", senses={"passive_perception": 11, "darkvision": "60 ft."},
         traits=[_trait("Drenching Form",
                        "A creature grappled by the horror is considered submerged.")],
         actions=[_atk("Slam", 8, "2d8+4", "Bludgeoning",
                       "Two attacks; one target is grappled (escape DC 15)."),
                  _atk("Engulf", 0, "0", "",
                       "A grappled Medium or smaller creature is engulfed: DC 15 Con "
                       "save at the start of each turn or begin drowning; takes 3d6 "
                       "bludgeoning each turn while engulfed.")]),

    # ----- Aberrations & original horrors (non-PI) -----
    _mon("gloomtendril", "Gloomtendril", "Large", "aberration",
         "chaotic evil", 15, 93, "11d10+33", {"walk": "20 ft.", "climb": "20 ft."},
         (17, 14, 16, 12, 13, 10), 7, ac_desc="natural armor",
         languages="telepathy 60 ft.",
         senses={"passive_perception": 13, "darkvision": "120 ft.",
                 "blindsight": "30 ft."},
         traits=[_trait("Umbral Dread",
                        "A creature that starts its turn within 10 ft. and can see "
                        "the gloomtendril makes a DC 14 Wis save or has disadvantage "
                        "on attacks until the start of its next turn.")],
         actions=[_atk("Lashing Tendril", 8, "2d8+3", "Bludgeoning",
                       "Reach 15 ft.; four attacks. A hit target is grappled "
                       "(escape DC 15)."),
                  _atk("Consume Light", 0, "0", "",
                       "Recharge 5-6: magical light within 30 ft. is snuffed and the "
                       "area is heavily obscured for 1 minute.")]),

    _mon("maw-of-the-deep", "Maw of the Deep", "Huge", "aberration",
         "unaligned", 13, 126, "11d12+55", {"walk": "10 ft.", "swim": "40 ft."},
         (22, 8, 20, 3, 12, 5), 9, ac_desc="natural armor",
         languages="—",
         senses={"passive_perception": 11, "darkvision": "120 ft.",
                 "tremorsense": "60 ft."},
         traits=[_trait("Ambush from Below",
                        "In the first round the maw has advantage against any "
                        "creature that hasn't acted, and such a hit is a critical.")],
         actions=[_atk("Bite", 10, "4d10+6", "Piercing",
                       "A Large or smaller target is grappled (escape DC 17) and "
                       "swallowed on the maw's next turn."),
                  _atk("Swallow", 0, "0", "",
                       "A swallowed creature is blinded and restrained, takes 3d10 "
                       "acid each turn, and can cut free with 20 damage.")]),

    _mon("dread-effigy", "Dread Effigy", "Medium", "construct",
         "neutral evil", 13, 60, "8d8+24", {"walk": "30 ft."},
         (16, 12, 16, 6, 11, 14), 4, ac_desc="natural armor",
         languages="—",
         senses={"passive_perception": 10, "darkvision": "60 ft."},
         traits=[_trait("Aura of Dread",
                        "Each creature that starts its turn within 20 ft. makes a "
                        "DC 13 Wis save or is frightened until the end of its turn.")],
         actions=[_atk("Wicker Claws", 6, "2d6+3", "Slashing", "Two attacks."),
                  _atk("Effigy Fire", 0, "0", "",
                       "If burned, the effigy explodes on death: 10-ft. burst, DC 13 "
                       "Dex save, 4d6 fire.")]),

    _mon("bramblewretch", "Bramblewretch", "Medium", "plant", "unaligned",
         13, 32, "5d8+10", {"walk": "20 ft."}, (15, 10, 15, 5, 10, 6), 1,
         ac_desc="natural armor", languages="—",
         senses={"passive_perception": 10, "blindsight": "30 ft."},
         traits=[_trait("False Appearance",
                        "While motionless it is indistinguishable from a thornbush."),
                 _trait("Fire Vulnerability", "Vulnerable to fire damage.")],
         actions=[_atk("Rake of Thorns", 4, "2d4+2", "Slashing",
                       "A hit target makes a DC 12 Str save or is restrained by "
                       "grasping vines.")]),

    # ----- Giant-kin & apex threats (original) -----
    _mon("frostbound-jarl", "Frostbound Jarl", "Huge", "giant",
         "neutral evil", 16, 148, "13d12+65", {"walk": "40 ft."},
         (23, 9, 21, 12, 13, 14), 9, ac_desc="patchwork plate",
         languages="Giant, Common",
         senses={"passive_perception": 12},
         traits=[_trait("Cold Born", "Immune to cold damage."),
                 _trait("Rimeforged",
                        "Ground within 15 ft. of the jarl is icy difficult terrain.")],
         actions=[_atk("Great Axe of Frost", 11, "3d12+6", "Slashing",
                       "Two attacks; plus 2d6 cold."),
                  _atk("Hurl Ice Boulder", 8, "4d10+6", "Bludgeoning",
                       "Range 60/240 ft.; a Large or smaller target is knocked prone "
                       "and its speed is halved until the end of its next turn.")]),

    _mon("emberforge-brute", "Emberforge Brute", "Large", "giant",
         "chaotic evil", 18, 138, "12d10+72", {"walk": "40 ft."},
         (23, 9, 22, 9, 11, 12), 9, ac_desc="molten plate",
         languages="Giant, Ignan",
         senses={"passive_perception": 12},
         traits=[_trait("Fire Born", "Immune to fire damage."),
                 _trait("Searing Weapons",
                        "The brute's weapon attacks deal an extra 2d6 fire (included "
                        "below).")],
         actions=[_atk("Forge Hammer", 11, "3d8+6", "Bludgeoning",
                       "Two attacks; plus 2d6 fire; a Large or smaller target is "
                       "pushed 10 ft."),
                  _atk("Molten Cinders", 0, "0", "",
                       "Recharge 5-6: 30-ft. cone, DC 16 Dex save, 6d6 fire (half on "
                       "success); flammable objects ignite.")]),

    _mon("sludge-behemoth", "Sludge Behemoth", "Large", "monstrosity",
         "chaotic evil", 14, 84, "8d10+40", {"walk": "30 ft."},
         (18, 12, 20, 6, 9, 6), 5, ac_desc="natural armor",
         languages="Giant",
         senses={"passive_perception": 9, "darkvision": "60 ft."},
         traits=[_trait("Regeneration",
                        "Regains 10 HP at the start of its turn unless it took acid "
                        "or fire damage since its last turn; it dies only if it ends "
                        "its turn at 0 HP without such damage."),
                 _trait("Keen Smell", "Advantage on Perception relying on smell.")],
         actions=[_atk("Filthy Claws", 7, "1d8+4", "Slashing",
                       "Two claws and a bite."),
                  _atk("Rending Bite", 7, "2d6+4", "Piercing")]),

    # ----- Draconic (original, not the SRD chromatic/metallic set) -----
    _mon("ashdrake-wyrmling", "Ashdrake Wyrmling", "Medium", "dragon",
         "chaotic evil", 16, 45, "6d8+18", {"walk": "30 ft.", "fly": "60 ft."},
         (15, 14, 17, 10, 11, 12), 3, ac_desc="natural armor",
         languages="Draconic",
         senses={"passive_perception": 12, "darkvision": "60 ft.",
                 "blindsight": "10 ft."},
         traits=[_trait("Fire Born", "Immune to fire damage.")],
         actions=[_atk("Bite", 5, "1d10+3", "Piercing", "Plus 1d4 fire."),
                  _atk("Cinder Breath", 0, "0", "",
                       "Recharge 5-6: 15-ft. cone, DC 13 Dex save, 5d6 fire (half on "
                       "success).")]),

    _mon("fen-drake", "Fen Drake", "Large", "dragon", "neutral evil", 15,
         76, "9d10+27", {"walk": "40 ft.", "swim": "40 ft."},
         (18, 13, 17, 8, 12, 10), 5, ac_desc="natural armor",
         languages="Draconic",
         senses={"passive_perception": 13, "darkvision": "60 ft."},
         traits=[_trait("Amphibious", "Can breathe air and water."),
                 _trait("Marsh Camouflage",
                        "Advantage on Stealth checks made in swamp or fen terrain.")],
         actions=[_atk("Bite", 7, "2d10+4", "Piercing", "Plus 1d6 poison."),
                  _atk("Tail", 7, "2d8+4", "Bludgeoning",
                       "A Large or smaller target is knocked prone (DC 14 Str)."),
                  _atk("Toxic Spray", 0, "0", "",
                       "Recharge 6: 30-ft. line, DC 14 Con save, 6d6 poison (half on "
                       "success); failure also poisons for 1 minute.")]),

    # ----- Extra NPCs, vermin & minor threats -----
    _mon("tavern-brawler", "Tavern Brawler", "Medium", "humanoid", "any",
         12, 16, "3d8+3", {"walk": "30 ft."}, (15, 12, 13, 9, 10, 11), 0.125,
         languages="Common",
         traits=[_trait("Improvised Fury",
                        "The brawler deals normal weapon damage with any improvised "
                        "weapon and can shove as a bonus action.")],
         actions=[_atk("Barstool", 4, "1d6+2", "Bludgeoning"),
                  _atk("Tankard Smash", 4, "1d4+2", "Bludgeoning")]),

    _mon("sellsword-archer", "Sellsword Archer", "Medium", "humanoid", "any",
         14, 24, "4d8+4", {"walk": "30 ft."}, (11, 16, 13, 10, 12, 10), 1,
         ac_desc="studded leather", languages="Common",
         senses={"passive_perception": 13},
         traits=[_trait("Steady Aim",
                        "If the archer doesn't move on its turn, it has advantage on "
                        "its first ranged attack.")],
         actions=[_atk("Longbow", 5, "1d8+3", "Piercing",
                       "Two attacks; range 150/600 ft."),
                  _atk("Shortsword", 5, "1d6+3", "Piercing")]),

    _mon("cult-inquisitor", "Cult Inquisitor", "Medium", "humanoid",
         "lawful evil", 13, 58, "9d8+18", {"walk": "30 ft."},
         (11, 14, 14, 15, 16, 13), 5, languages="Common, one other",
         senses={"passive_perception": 16},
         traits=[_trait("Zealous Insight",
                        "Advantage on Insight checks and on saves against being "
                        "charmed or frightened.")],
         actions=[_atk("Ritual Dagger", 5, "1d4+2", "Piercing",
                       "Plus 2d6 necrotic on a creature below half its hit points."),
                  _atk("Word of Anathema", 0, "0", "",
                       "One creature within 60 ft. makes a DC 14 Wis save or takes "
                       "4d6 psychic and can't take reactions until the end of its "
                       "next turn.")]),

    _mon("dune-scuttler", "Dune Scuttler", "Large", "beast", "unaligned",
         15, 52, "7d10+14", {"walk": "40 ft.", "burrow": "20 ft."},
         (16, 13, 15, 1, 10, 3), 3, ac_desc="natural armor",
         senses={"passive_perception": 10, "blindsight": "30 ft.",
                 "tremorsense": "30 ft."},
         traits=[_trait("Sand Ambusher",
                        "Advantage on attacks against a creature that hasn't detected "
                        "it while burrowed in loose ground.")],
         actions=[_atk("Claw", 5, "1d8+3", "Slashing",
                       "Two claws; a hit target is grappled (escape DC 13)."),
                  _atk("Stinger", 5, "1d6+3", "Piercing",
                       "DC 13 Con save or take 3d6 poison (half on success) and be "
                       "poisoned for 1 minute.")]),

    _mon("bloodfen-leech", "Bloodfen Leech", "Large", "beast", "unaligned",
         11, 34, "4d10+12", {"walk": "10 ft.", "swim": "40 ft."},
         (15, 10, 16, 2, 9, 4), 1, ac_desc="natural armor",
         senses={"passive_perception": 9, "blindsight": "30 ft."},
         traits=[_trait("Blood Scent",
                        "Advantage on attacks against creatures missing any hit "
                        "points.")],
         actions=[_atk("Latch", 4, "1d8+2", "Piercing",
                       "The leech attaches (escape DC 12); while attached it drains "
                       "1d8 hit points at the start of each of its turns and heals "
                       "the same amount.")]),

    _mon("plaguebearer-corpse", "Plaguebearer Corpse", "Medium", "undead",
         "neutral evil", 9, 26, "4d8+8", {"walk": "20 ft."},
         (13, 6, 16, 3, 8, 5), 1, languages="—",
         senses={"passive_perception": 9, "darkvision": "60 ft."},
         traits=[_trait("Death Burst",
                        "When it dies it bursts in a 5-ft. cloud of contagion; each "
                        "creature in the area makes a DC 12 Con save or is poisoned "
                        "for 1 minute.")],
         actions=[_atk("Diseased Slam", 3, "1d8+1", "Bludgeoning",
                       "Plus 1d6 necrotic; DC 12 Con save or the target can't regain "
                       "hit points until the end of its next turn.")]),

    _mon("bog-lantern-sprite", "Bog-Lantern Sprite", "Tiny", "fey",
         "chaotic neutral", 14, 10, "4d4", {"walk": "10 ft.", "fly": "40 ft."},
         (3, 18, 10, 11, 13, 14), 0.25, languages="Sylvan",
         senses={"passive_perception": 13, "darkvision": "60 ft."},
         traits=[_trait("Luring Glow",
                        "A creature that ends its turn within 30 ft. and can see the "
                        "sprite's light makes a DC 12 Wis save or moves 10 ft. toward "
                        "it on its next turn.")],
         actions=[_atk("Spark", 6, "1d6+4", "Radiant",
                       "Ranged 40 ft. spell attack.")]),
]


def get_engine(database_url: Optional[str] = None) -> Engine:
    """Default to the backend's ``oracle.db`` (same as the SRD ingest)."""
    if database_url is None:
        database_url = os.getenv("DATABASE_URL")
    if database_url is None:
        backend_db = Path(__file__).resolve().parent.parent / "oracle-dm-backend" / "oracle.db"
        database_url = f"sqlite:///{backend_db}"
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, echo=False, connect_args=connect_args)


def seed_owned_monsters(
    engine: Optional[Engine] = None,
    database_url: Optional[str] = None,
) -> dict:
    """Upsert the owned monster catalog. Offline and idempotent."""
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)

    new = 0
    with Session(engine) as s:
        for data in OWNED_MONSTERS:
            slug = data["index_slug"]
            existing = s.exec(select(Monster).where(Monster.index_slug == slug)).first()
            if existing:
                for k, v in data.items():
                    setattr(existing, k, v)
                s.add(existing)
            else:
                s.add(Monster(**data))
                new += 1
        s.commit()

    return {"owned_monsters_new": new, "owned_monsters_total": len(OWNED_MONSTERS)}


if __name__ == "__main__":
    print(seed_owned_monsters())
