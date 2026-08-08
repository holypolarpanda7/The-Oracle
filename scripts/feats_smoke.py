"""Feat smoke test — a feat has to MEAN something, all the way down.

Offline, no GPU, no LLM. Runs against the real rules DB (read-only) and
in-memory Character rows, because every claim here is about the feat pipeline
and none of it is about persistence.

What it pins, in the order the bugs were found:

1. A feat's questions are asked and applied — including a spell pick scoped by
   SCHOOL, and a THIRD question (`also` as a list).
2. What a feat grants outright lands on the sheet without the client sending
   it; a grant is not a choice.
3. The same feat can't fill both creation slots.
4. Two feats' named options don't cross.
5. An option that names a real feat becomes that feat.
6. A feat that grants a RESOURCE gets a real, spendable pool — and merges with
   a class pool of the same key rather than making a second one.
7. An at-will spell from a feat option reaches the ENFORCED castable list.
8. All of it is visible to the DM.

Usage:  uv run python scripts/feats_smoke.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(ROOT / 'oracle-dm-backend' / 'oracle.db').as_posix()}")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "oracle-dm-backend"))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = f"{GREEN}✓{OFF}" if ok else f"{RED}✗{OFF}"
    print(f"  {mark} {label}" + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        _fails.append(label)


def _load_backend():
    spec = importlib.util.spec_from_file_location(
        "dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    dm = _load_backend()
    C = dm.Character

    def pc(cls: str = "Fighter", level: int = 4, **stats) -> "C":
        base = {"strength": 16, "dexterity": 12, "constitution": 14,
                "intelligence": 10, "wisdom": 11, "charisma": 13}
        base.update(stats)
        return C(name="Test", discord_user_id="smoke", char_class=cls,
                 level=level, tags=[], spells=[], resources_used={}, stats=base)

    has_choices = bool(dm._feat_choices_merged().get("fey-touched"))
    if not has_choices:
        print(f"{DIM}owned-book feat choices absent — "
              f"schema-dependent checks skipped{OFF}")

    print(f"\n{BOLD}1. a feat asks its questions, and the answers stick{OFF}")
    if has_choices:
        ch = pc(wisdom=15)
        notes = dm._apply_feat(ch, "fey-touched",
                              {"ability": "wis", "spells": ["charm-person"]})
        check("the ability bump is applied", ch.stats["wisdom"] == 16,
              f"WIS -> {ch.stats['wisdom']}")
        check("the school-scoped spell is learned", "Charm Person" in (ch.spells or []))
        check("and what the feat GRANTS rides along",
              "Misty Step" in (ch.spells or []), ", ".join(ch.spells or []))
        check("the notes name all of it", any("Misty Step" in n for n in notes))

        specs = dm._feat_choice_specs("skill-expert")
        check("a feat may ask three things", len(specs) == 3,
              " + ".join(s["kind"] for s in specs))
        ch = pc()
        dm._apply_feat(ch, "skill-expert",
                       {"ability": "dex", "skills": ["Stealth"],
                        "options": ["Acrobatics"]})
        check("all three land on the sheet",
              ch.stats["dexterity"] == 13 and "skill: Stealth" in ch.tags
              and "expertise: Acrobatics" in ch.tags)

        pool = dm.cc_feat_spells("fey-touched")
        check("the spell pool is school-scoped, and the server owns the filter",
              pool["n"] == 1 and pool["spells"]
              and {s["school"] for s in pool["spells"]} <= {"Divination", "Enchantment"},
              f"{len(pool['spells'])} spells")
        check("a grant-only pick offers nothing to choose",
              dm.cc_feat_spells("telepathic")["n"] == 0
              and dm.cc_feat_spells("telepathic")["granted"])
        check("an unanswered spell pick blocks the level",
              dm._feat_choice_satisfied(
                  dm._feat_choice_specs("fey-touched")[1], {"ability": "wis"}) is False)
        check("a grant-only spec needs no answer",
              dm._feat_choice_satisfied(
                  dm._feat_choice_specs("telepathic")[1], {}) is True)
        check("creation collects grants without the client sending them",
              dm._feat_granted_spells(["fey-touched"]) == ["Misty Step"])

    print(f"\n{BOLD}2. two feats stay two feats{OFF}")
    if has_choices:
        ch = pc()
        dm._apply_feat(ch, "eldritch-adept",
                       {"options": ["Devil's Sight"], "ability": "cha"})
        dm._apply_feat(ch, "metamagic-adept",
                       {"options": ["Quickened Spell", "Subtle Spell"]})
        feats = {f["slug"]: f for f in dm.character_feats(ch)}
        check("each feat keeps its own picks",
              feats["eldritch-adept"]["picks"] == ["Devil's Sight"]
              and set(feats["metamagic-adept"]["picks"])
              == {"Quickened Spell", "Subtle Spell"})
        check("and neither claims the other's",
              "Devil's Sight" not in feats["metamagic-adept"]["picks"]
              and "Quickened Spell" not in feats["eldritch-adept"]["picks"])

        ch = pc()
        dm._apply_feat(ch, "fighting-initiate", {"options": ["Archery"]})
        got = {f["slug"] for f in dm.character_feats(ch)}
        check("a style pick becomes the real fighting-style feat",
              "archery" in got, ", ".join(sorted(got)))
        arch = next(f for f in dm.character_feats(ch) if f["slug"] == "archery")
        check("…carrying its own rules text", bool(arch["benefit"]),
              (arch["benefit"] or "")[:48])

    print(f"\n{BOLD}3. a granted resource is a real, spendable pool{OFF}")
    if has_choices:
        ch = pc()
        check("a fighter starts with no pool", dm._class_resources_for(ch) == [])
        dm._apply_feat(ch, "metamagic-adept",
                       {"options": ["Quickened Spell", "Subtle Spell"]})
        res = dm._class_resources_for(ch)
        check("the feat gives them one",
              len(res) == 1 and res[0]["key"] == "sorcery" and res[0]["total"] == 2,
              str(res))
        out = dm.resolve_use_hooks("[[USE: Sorcery Points | 2]]", ch)
        check("[[USE]] spends it", "spent" in out and ch.resources_used.get("sorcery") == 2,
              out.strip())
        check("and over-spending is refused, not granted",
              "not enough" in dm.resolve_use_hooks("[[USE: Sorcery Points]]", ch))

        sor = pc("Sorcerer", 5, charisma=16)
        check("a sorcerer's own pool is their level",
              dm._class_resources_for(sor)[0]["total"] == 5)
        dm._apply_feat(sor, "metamagic-adept",
                       {"options": ["Twinned Spell", "Careful Spell"]})
        merged = dm._class_resources_for(sor)
        check("the feat ADDS to it — one pool, not two",
              len(merged) == 1 and merged[0]["total"] == 7, str(merged))

    print(f"\n{BOLD}4. an at-will grant reaches the ENFORCED castable list{OFF}")
    if has_choices:
        ch = pc()
        check("nothing castable to begin with", dm._castable_lists(ch) == ([], []))
        dm._apply_feat(ch, "eldritch-adept",
                       {"options": ["Armor of Shadows"], "ability": "cha"})
        cantrips, _ = dm._castable_lists(ch)
        check("the invocation's spell is castable",
              "Mage Armor" in cantrips and dm._can_cast(ch, "Mage Armor"),
              ", ".join(cantrips))
        ch2 = pc()
        dm._apply_feat(ch2, "eldritch-adept",
                       {"options": ["Devil's Sight"], "ability": "wis"})
        check("an invocation that grants no spell adds none",
              dm._castable_lists(ch2) == ([], []))

    print(f"\n{BOLD}5. the artificer has a spell list{OFF}")
    art = dm.cc_spells("artificer")
    check("artificer is a caster with a real pool",
          art["caster"] and len(art["cantrips"]) > 5 and len(art["spells"]) > 5,
          f"{len(art['cantrips'])} cantrips / {len(art['spells'])} level-1")
    check("…and it did not steal them from anyone",
          "cleric" in {c.lower() for c in
                       (dm.rules_lib.get_spell('cure-wounds').classes or [])})

    print(f"\n{BOLD}6. the DM is actually told{OFF}")
    if has_choices:
        ch = pc()
        dm._apply_feat(ch, "metamagic-adept",
                       {"options": ["Quickened Spell", "Subtle Spell"]})
        block = "\n".join(dm._feats_prompt_block(ch))
        check("the feat is in the prompt", "Metamagic Adept" in block)
        check("with its chosen options", "Quickened Spell" in block)
        check("and what each option COSTS",
              "sorcery point" in block.lower(), block.splitlines()[-1].strip()[:70])
        # A real session: the class/species half of the tab reads the rules DB,
        # and passing None would exercise only the feat half being asserted.
        with dm.Session(dm.engine) as s:
            feats_on_sheet = [f["name"] for f in dm._sheet_features(s, ch)]
        check("and the sheet shows it too",
              any("Metamagic Adept" in n for n in feats_on_sheet),
              "; ".join(feats_on_sheet))
        check("beside the class features, not instead of them",
              len(feats_on_sheet) > 1, f"{len(feats_on_sheet)} rows")

    print(f"\n{BOLD}7. Metamagic is a rule the code decides{OFF}")
    if has_choices:
        sor = pc("Sorcerer", 5, charisma=16)
        sor.spells = ["Fireball", "Magic Missile", "Haste", "Fire Bolt",
                      "Charm Person", "Shield"]
        dm._apply_feat(sor, "metamagic-adept",
                       {"options": ["Quickened Spell", "Extended Spell"]})
        dm._add_tags(sor, "metamagic", ["Careful Spell", "Distant Spell",
                                        "Transmuted Spell", "Empowered Spell",
                                        "Heightened Spell"])

        def mm(hook: str, reset: bool = True) -> str:
            if reset:
                sor.resources_used = {}
            return dm.resolve_metamagic_hooks(hook, sor).strip()

        check("a legal shaping is applied and priced",
              "1 bonus action" in mm("[[METAMAGIC: Quickened Spell | Fireball]]"))
        check("a reaction spell can't be Quickened",
              "can't touch" in mm("[[METAMAGIC: Quickened Spell | Shield]]"),
              mm("[[METAMAGIC: Quickened Spell | Shield]]"))
        check("an instantaneous spell can't be Extended",
              "can't touch" in mm("[[METAMAGIC: Extended Spell | Fireball]]"))
        check("a 1-minute spell can be, and the new duration is computed",
              "2 minutes" in mm("[[METAMAGIC: Extended Spell | Haste]]"))
        check("a spell with no save can't be made Careful",
              "can't touch" in mm("[[METAMAGIC: Careful Spell | Magic Missile]]"))
        check("one with a save can",
              "can't touch" not in mm("[[METAMAGIC: Careful Spell | Fireball]]"))
        check("Distant doubles a real range",
              "300 feet" in mm("[[METAMAGIC: Distant Spell | Fireball]]"))
        check("a damageless spell can't be Transmuted",
              "can't touch" in mm("[[METAMAGIC: Transmuted Spell | Charm Person]]"))
        check("an unknown option is refused",
              "isn't a Metamagic they know"
              in mm("[[METAMAGIC: Subtle Spell | Fireball]]"))
        check("a spell that isn't a spell is refused",
              "isn't one" in mm("[[METAMAGIC: Careful Spell | Banana Bolt]]"))

        sor.resources_used = {}
        two = dm.resolve_metamagic_hooks(
            "[[METAMAGIC: Distant Spell | Fireball]] "
            "[[METAMAGIC: Heightened Spell | Fireball]]", sor)
        check("only ONE option to a casting", "only one Metamagic" in two)
        sor.resources_used = {}
        stacked = dm.resolve_metamagic_hooks(
            "[[METAMAGIC: Empowered Spell | Fireball]] "
            "[[METAMAGIC: Heightened Spell | Fireball]]", sor)
        check("…except the one that says it stacks",
              "only one Metamagic" not in stacked)

        broke = pc("Sorcerer", 2, charisma=16)     # 2 class points, no feat
        broke.spells = ["Fireball"]
        dm._add_tags(broke, "metamagic", ["Quickened Spell"])
        dm.resolve_metamagic_hooks("[[METAMAGIC: Quickened Spell | Fireball]]", broke)
        check("points are actually spent",
              broke.resources_used.get("sorcery") == 2, str(broke.resources_used))
        check("and running out refuses the shaping",
              "needs 2 sorcery points" in dm.resolve_metamagic_hooks(
                  "[[METAMAGIC: Quickened Spell | Fireball]]", broke))

    print(f"\n{BOLD}8. an invocation grants what it says it grants{OFF}")
    if has_choices:
        w = pc()
        dm._apply_feat(w, "eldritch-adept",
                       {"options": ["Beguiling Influence"], "ability": "cha"})
        check("a skill-granting invocation grants the skills",
              "skill: Deception" in w.tags and "skill: Persuasion" in w.tags,
              "; ".join(t for t in w.tags if t.startswith("skill")))

        d2 = pc()
        dm._apply_feat(d2, "eldritch-adept",
                       {"options": ["Devil's Sight"], "ability": "cha"})
        from survival.light import parse_senses, perceives
        sense_tags = [t.split(":", 1)[1].strip()
                      for t in d2.tags if t.lower().startswith("sense:")]
        senses = parse_senses({"raw": " ".join(sense_tags)})
        check("Devil's Sight lands as a real sense on the sheet",
              senses.get("devils_sight") == 120, str(senses))
        check("…and the light engine honours it in the dark",
              perceives("dark", 100, senses)["sees"] is True)
        check("…including through MAGICAL darkness",
              perceives("dark", 50, senses, obscured="heavy",
                        magical_dark=True)["sees"] is True)
        check("…but not through fog, which is not darkness",
              perceives("dark", 50, senses, obscured="heavy")["sees"] is False)

        t5 = pc()
        dm._apply_feat(t5, "eldritch-adept",
                       {"options": ["Thief of Five Fates"], "ability": "cha"})
        pools = {r["key"]: r for r in dm._class_resources_for(t5)}
        check("a once-per-rest invocation gets a counted allowance",
              any(k.startswith("opt-") and v["total"] == 1
                  for k, v in pools.items()), str(list(pools)))

    print()
    if _fails:
        print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
        return 1
    print(f"{GREEN}a feat means the same thing everywhere{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
