"""Species-choice smoke test — a trait that says "of your choice" is a QUESTION.

Offline, no GPU, no LLM. Runs against a scratch DB carrying a copy of the real
rules_race / rules_feat tables, so every claim here is made about the species
and feats actually ingested on this machine rather than about fixtures.

What it pins, in the order the bugs were reported:

1. A species that grants "any feat you qualify for" (Custom Lineage) is NOT
   held to the level a feat's category is filed behind — while the background
   slot still is, and an epic boon stays level 19 for everybody.
2. A species ASKS its questions: languages read off its own languages line
   (with the tongues it already speaks kept out of the pool), a human's
   Skillful skill, and a Custom Lineage's either/or gift.
3. The answers land on the sheet — a skill is a skill, a language is a
   language, and the gift that grants darkvision writes the `sense:` tag the
   tactical board reads.

Usage:  uv run python scripts/species_choices_smoke.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REAL_DB = ROOT / "oracle-dm-backend" / "oracle.db"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "oracle-dm-backend"))

GREEN, RED, DIM, BOLD, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"
_fails: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = f"{GREEN}✓{OFF}" if ok else f"{RED}✗{OFF}"
    print(f"  {mark} {label}" + (f" {DIM}— {detail}{OFF}" if detail else ""))
    if not ok:
        _fails.append(label)


def _scratch_db() -> str:
    """A throwaway DB holding a COPY of the rules rows this test reads.

    Never the real file: registering a character writes, and the world DB is
    not a fixture (see scripts/world_wipe.py).
    """
    path = os.path.join(tempfile.gettempdir(), "oracle_species_choices.db")
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)
    con = sqlite3.connect(path)
    con.execute("ATTACH DATABASE ? AS src", (str(REAL_DB),))
    for table in ("rules_race", "rules_feat"):
        con.execute(f"CREATE TABLE {table} AS SELECT * FROM src.{table}")
    con.commit()
    con.close()
    return path


def main() -> int:
    db = _scratch_db()
    os.environ["DATABASE_URL"] = f"sqlite:///{db}"
    spec = importlib.util.spec_from_file_location(
        "dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
    dm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dm)

    from sqlmodel import Session, SQLModel, select
    SQLModel.metadata.create_all(dm.engine)

    opts = dm.cc_options()
    races = {r["slug"]: r for r in opts["races"]}
    feats = {f["slug"]: f for f in opts["feats"]}

    # ---------------------------------------------------------------- 1
    print(f"\n{BOLD}1. Custom Lineage takes ANY feat it qualifies for{OFF}")
    # A level-gated feat a level-1 FIGHTER could otherwise satisfy: the book
    # prints the level twice (the category AND the prerequisite line), so this
    # only skips prerequisites about training or magic, never about level.
    def _plain(f: dict) -> bool:
        pre = (f.get("prerequisite") or "").lower()
        return not any(w in pre for w in
                       ("spellcast", "pact", "armor", "shield", "training"))
    general = next((f for f in opts["feats"]
                    if (f.get("min_level") or 1) >= 4
                    and f.get("category") not in ("epic-boon", "dragonmark")
                    and f["slug"] != "ability-score-improvement"
                    and _plain(f)), None)
    boon = next((f for f in opts["feats"]
                 if f.get("category") == "epic-boon"), None)
    check("the pool really does hold level-gated feats", general is not None,
          f"{(general or {}).get('name')} (level {(general or {}).get('min_level')}+)")
    check("Custom Lineage's slot is the 'any' kind",
          races.get("custom-lineage", {}).get("feat_choice") == "any")

    def prereq(feat: dict, *, waived: bool) -> tuple:
        return dm._feat_prereq_met(
            feat.get("prerequisite"), feat.get("min_level") or 1,
            {"strength": 15, "dexterity": 15, "constitution": 15,
             "intelligence": 15, "wisdom": 15, "charisma": 15},
            "Fighter", 1, race="Custom Lineage", background="soldier",
            waive_min_level=waived)

    if general:
        check("blocked at level 1 through an ordinary slot",
              prereq(general, waived=False)[0] is False,
              prereq(general, waived=False)[1] or "")
        check("…and allowed through the species' own",
              prereq(general, waived=True)[0] is True)

    with Session(dm.engine) as s:
        free = dm._species_free_feat(s, "Custom Lineage",
                                     ["tough", "skilled"], "soldier")
        bg_feat = (dm._BACKGROUND_KITS.get("soldier", {}) or {}).get("origin_feat")
        check("the species slot is told from the background's granted feat",
              free is not None and free != bg_feat, f"{free} (bg grants {bg_feat})")
        check("a species with no such gift waives nothing",
              dm._species_free_feat(s, "Human", ["tough"], "soldier") is None)
        check("…and a lineage in the name doesn't hide the species",
              dm._race_row(s, "Elf (Wood Elf)") is not None)

    # ---------------------------------------------------------------- 2
    print(f"\n{BOLD}2. a species asks what its traits promise{OFF}")
    human = races.get("human", {})
    parts = dm._flatten_choice(human.get("choices"))
    kinds = [p["kind"] for p in parts]
    check("Skillful is a real skill pick", "skills" in kinds, " + ".join(kinds))
    lang = next((p for p in parts if p["kind"] == "language"), None)
    check("'Common + two extra of your choice' asks for two",
          bool(lang) and lang["n"] == 2, str(lang and lang["n"]))
    check("…and Common isn't offered back",
          bool(lang) and "Common" not in (lang.get("from") or []))

    asked = {slug: dm._flatten_choice(r.get("choices"))
             for slug, r in races.items()}
    for slug, row in races.items():
        line = (row.get("languages") or "").lower()
        if "of your choice" in line:
            got = [p for p in asked[slug] if p["kind"] == "language"]
            if not got:
                check(f"{slug}: its language line is asked", False, line)
    check("every 'of your choice' language line became a question",
          all(any(p["kind"] == "language" for p in asked[slug])
              for slug, r in races.items()
              if "of your choice" in (r.get("languages") or "").lower()))
    known_kept_out = [
        slug for slug, r in races.items()
        if any(p["kind"] == "language" for p in asked[slug])
        and any(tongue in (r.get("languages") or "")
                and tongue in (next(p for p in asked[slug]
                                    if p["kind"] == "language").get("from") or [])
                for tongue in ("Elvish", "Draconic", "Dwarvish"))]
    check("a tongue a species already speaks is never offered again",
          not known_kept_out, ", ".join(known_kept_out))

    cl = dm._flatten_choice(races.get("custom-lineage", {}).get("choices"))
    gift = next((p for p in cl if p["kind"] == "options"), None)
    follow = next((p for p in cl if p.get("when")), None)
    check("the lineage gift is an either/or", bool(gift) and len(gift["from"]) == 2,
          ", ".join((gift or {}).get("from") or []))
    check("…and the extra skill hangs off the half that grants it",
          bool(follow) and follow["when"] in (gift or {}).get("from", []),
          str(follow and follow["when"]))

    # ---------------------------------------------------------------- 3
    print(f"\n{BOLD}3. the answers reach the sheet{OFF}")
    Req = dm.RegisterCharacterRequest

    async def register(**kw):
        return await dm.register_character(Req(
            discord_user_id=kw.pop("user"), name=kw.pop("name"),
            level=1, approve=True, source="guided", gear_mode="buy",
            bought_items=[], **kw))

    stats = {"strength": 12, "dexterity": 14, "constitution": 14,
             "intelligence": 10, "wisdom": 12, "charisma": 15}
    bg = "soldier" if "soldier" in {b["slug"] for b in opts["backgrounds"]} \
        else opts["backgrounds"][0]["slug"]
    bg_feat = (dm._BACKGROUND_KITS.get(bg, {}) or {}).get("origin_feat")

    ok = True
    try:
        asyncio.run(register(
            user="species-smoke", name="Vashti", race="Custom Lineage",
            char_class="Fighter", background=bg, stats=stats,
            feats=[f for f in (bg_feat, (general or {}).get("slug")) if f],
            skills=["Athletics"], languages=["Draconic"],
            feat_options=["Darkvision 60 ft"]))
    except Exception as e:
        ok = False
        check("a Custom Lineage with a level-4 feat registers", False, str(e))
    if ok:
        with Session(dm.engine) as s:
            ch = s.exec(select(dm.Character).where(
                dm.Character.name == "Vashti")).first()
        tags = list((ch.tags if ch else None) or [])
        check("a Custom Lineage with a level-4 feat registers", ch is not None)
        check("the general feat is on the sheet",
              f"feat: {(general or {}).get('slug')}" in tags,
              (general or {}).get("name") or "")
        check("the chosen language is a real proficiency",
              "language: Draconic" in tags)
        check("the gift is filed under the species, not as a feat option",
              "lineage-gift: Darkvision 60 ft" in tags,
              "; ".join(t for t in tags if "arkvision" in t))
        check("…and it grants the sense the BOARD reads",
              any(t.startswith("sense:") and "darkvision" in t.lower()
                  for t in tags))

        from survival.light import parse_senses
        seen = {}
        for t in tags:
            if t.lower().startswith("sense:"):
                seen.update(parse_senses({"raw": t.split(":", 1)[1]}))
        check("…parsed by the same parser the board uses",
              int(seen.get("darkvision") or 0) >= 60, str(seen))

    # Two feats the gift does NOT reach: an epic boon (level 19 is what an epic
    # boon IS) and the straight ASI (the slot steps outside the ASI schedule —
    # it does not buy a turn of it).
    def refuse(name: str, feat_slug: str) -> str | None:
        try:
            asyncio.run(register(
                user=f"species-smoke-{name}", name=name, race="Custom Lineage",
                char_class="Fighter", background=bg, stats=stats,
                feats=[bg_feat, feat_slug]))
        except Exception as e:
            return getattr(e, "detail", str(e))
        return None

    if boon and bg_feat:
        why = refuse("Ilyra", boon["slug"])
        check("an epic boon stays level 19 even in that slot",
              why is not None, str(why)[:70])
    if bg_feat and "ability-score-improvement" in feats:
        why = refuse("Corvin", "ability-score-improvement")
        check("…and so does the straight Ability Score Improvement",
              why is not None, str(why)[:70])

    # the background slot is still level-gated: same feat, wrong slot.
    if general and bg_feat:
        refused = None
        try:
            asyncio.run(register(
                user="species-smoke-2", name="Kestrel", race="Human",
                char_class="Fighter", background=bg, stats=stats,
                feats=[bg_feat, general["slug"]]))
        except Exception as e:
            refused = getattr(e, "detail", str(e))
        check("a species WITHOUT the gift can't reach a level-4 feat",
              refused is not None, str(refused)[:70])

    print()
    if _fails:
        print(f"{RED}{len(_fails)} check(s) failed:{OFF} " + "; ".join(_fails))
        return 1
    print(f"{GREEN}a species asks what it grants, and the answers stick{OFF}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
