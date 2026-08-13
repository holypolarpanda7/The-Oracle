"""Verify the SUBCLASS override slot works end to end — for whatever is in it.

Deliberately DATA-DRIVEN: every expectation is derived from
``owned_books/subclasses_overrides.json`` (and the companion stat blocks in
``summons_overrides.json``) rather than written here, because those files are
gitignored book-derived data and this script is committed tooling. The same
split the loaders keep: the tool is in the repo, the numbers are not. With no
slot present it reports "nothing to check" and exits clean.

What it proves for EVERY entry, not just a sampled few:

  * it loads, and is wired to a class the game actually has;
  * it is OFFERED at that class's subclass level and the pick sticks;
  * its features arrive at their own levels and at no other;
  * the DM prompt can render it;
  * an always-prepared spell list is CASTABLE at the right level and not before;
  * a damage defence stated in the feature text is enforced in combat;
  * a companion stat block materializes at the formula it declares.

Run: uv run python scripts/subclass_overrides_smoke.py
"""
from __future__ import annotations
import importlib.util, json, os, re, shutil, sys, tempfile
from pathlib import Path

ROOT = Path("/mnt/d/Projects/The Oracle")
sys.path.insert(0, str(ROOT))

SLOT = ROOT / "owned_books" / "subclasses_overrides.json"
if not SLOT.is_file():
    print("no subclasses_overrides.json — nothing to check")
    sys.exit(0)

# Work on a COPY of the live DB: the class and spell tables are needed to level
# a character honestly, and a smoke test must never write to the real world.
live = ROOT / "oracle-dm-backend" / "oracle.db"
db = Path(tempfile.gettempdir()) / "oracle_subclass_check.db"
if db.exists():
    db.unlink()
if live.is_file():
    shutil.copy(live, db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"

spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

from sqlmodel import Session, SQLModel, select
SQLModel.metadata.create_all(m.engine)

from rules import damage as damage_rules, summons as _sum
from rules.models import DndClass, Subclass
from rules.owned_ingest import ingest_subclasses_overrides
from rules.query import RulesLibrary, format_subclass_brief

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


entries = json.loads(SLOT.read_text("utf-8"))
check("the slot parses as a list of entries",
      isinstance(entries, list) and bool(entries), f"{len(entries)} entries")
check("every entry has a slug, a name, a class and features",
      all(e.get("slug") and e.get("name") and e.get("class") and e.get("features")
          for e in entries))
slugs = [e["slug"] for e in entries]
check("no slug is used twice", len(set(slugs)) == len(slugs),
      str([s for s in slugs if slugs.count(s) > 1][:4]))

res = ingest_subclasses_overrides(engine=m.engine)
check("the loader applies the whole slot",
      res.get("subclasses_new", 0) + res.get("subclasses_applied", 0) >= len(entries),
      str(res))

lib = RulesLibrary(engine=m.engine)
with Session(m.engine) as s:
    classes = {c.name: c for c in s.exec(select(DndClass)).all()}
    rows = {r.index_slug: r for r in s.exec(select(Subclass)).all()}

unknown = [e["slug"] for e in entries if e["class"] not in classes]
check("every entry names a class the game has", not unknown, str(unknown[:4]))
check("every entry reached the rules table",
      all(e["slug"] in rows for e in entries),
      str([e["slug"] for e in entries if e["slug"] not in rows][:4]))
check("each is wired to its class's own slug",
      all(rows[e["slug"]].class_slug == classes[e["class"]].index_slug
          for e in entries if e["slug"] in rows and e["class"] in classes))

# --- features land at their own level, and only there -----------------------
wrong, leaked = [], []
for e in entries:
    lv = {int(f["level"]) for f in e["features"]}
    for f in e["features"]:
        at = lib.subclass_features_at(e["slug"], int(f["level"]))
        if not any(x.get("name") == f["name"] for x in at):
            wrong.append(f"{e['slug']}:{f['name']}@{f['level']}")
    for other in set(range(1, 21)) - lv:
        if lib.subclass_features_at(e["slug"], other):
            leaked.append(f"{e['slug']}@{other}")
check("every feature is returned at its own level", not wrong, str(wrong[:4]))
check("and never at a level it doesn't grant one", not leaked, str(leaked[:4]))
check("the DM prompt can render every one of them",
      all(format_subclass_brief(lib.get_subclass(e["slug"])) for e in entries))

# --- offered at the class's subclass level, and the pick sticks -------------
STATS = {"strength": 14, "dexterity": 14, "constitution": 14,
         "intelligence": 14, "wisdom": 16, "charisma": 16}


def sheet(cls: str, sub: str | None, lvl: int) -> int:
    with Session(m.engine) as s:
        c = m.Character(discord_user_id=f"sub-{cls}-{sub}-{lvl}", name="T",
                        race="Human", char_class=cls, subclass=sub, level=lvl,
                        approved=True, max_hp=30, current_hp=30, stats=dict(STATS))
        s.add(c); s.commit(); s.refresh(c)
        return c.id


not_offered, not_stuck = [], []
for cls_name in sorted({e["class"] for e in entries} & set(classes)):
    cls_row = classes[cls_name]
    pick_lvl = int(cls_row.subclass_level or 3)
    want = [e for e in entries if e["class"] == cls_name]
    cid = sheet(cls_name, None, pick_lvl - 1)
    with Session(m.engine) as s:
        prog = m._progression(s, s.get(m.Character, cid), None, apply=False)
    have = {o["slug"] for o in (prog.get("subclass_options") or [])}
    not_offered += [e["slug"] for e in want if e["slug"] not in have]
    with Session(m.engine) as s:
        c = s.get(m.Character, cid)
        m._progression(s, c, want[0]["slug"], apply=True)
        s.commit()
        c = s.get(m.Character, cid)
        if c.subclass != want[0]["name"] or c.level != pick_lvl:
            not_stuck.append(want[0]["slug"])
check("every entry is offered at its class's subclass level",
      not not_offered, str(not_offered[:4]))
check("choosing one sticks on the sheet", not not_stuck, str(not_stuck[:4]))

# --- always-prepared spells become CASTABLE, at the right level -------------
# Expectations come from the entry's OWN text, so this checks any future pack.
TIERED = re.compile(r"[Aa]lways[- ]prepared[^:]*:\s*([^\n]+)")
TIER = re.compile(r"\bL(\d{1,2})\s+([^;]+)")
spell_miss, spell_early = [], []
for e in entries:
    if e["class"] not in classes:
        continue
    tiers: dict[int, list[str]] = {}
    for f in e["features"]:
        for blob in TIERED.findall(str(f.get("summary") or "")):
            for lvl, names in TIER.findall(blob):
                tiers.setdefault(int(lvl), []).extend(
                    m._resolve_spell_names(names))
    if not tiers:
        continue
    low = min(tiers)
    cid = sheet(e["class"], e["name"], low)
    with Session(m.engine) as s:
        cant, lev = m._castable_lists(s.get(m.Character, cid))
    got = {x.lower() for x in (*cant, *lev)}
    spell_miss += [f"{e['slug']}:{n}" for n in tiers[low] if n.lower() not in got]
    for hi in (t for t in tiers if t > low):
        spell_early += [f"{e['slug']}@{hi}:{n}" for n in tiers[hi]
                        if n.lower() in got and n not in tiers[low]]
check("an always-prepared subclass spell is castable at its tier",
      not spell_miss, str(spell_miss[:5]))
check("and a higher tier never arrives early", not spell_early,
      str(spell_early[:5]))

# --- a stated damage defence is actually enforced ---------------------------
# The engine reads the feature TEXT, and the sheet keeps only the first 90
# characters of a summary — so a defence written past that point silently does
# nothing. This catches exactly that, using the engine's own matcher.
RESIST = re.compile(
    r"\b(resistance|immunity|vulnerability|immune|resistant|vulnerable)\b"
    r"[^.]{0,60}?\b(" + "|".join(damage_rules.DAMAGE_TYPES) + r")\b", re.I)
# Words that mean the defence is CONDITIONAL (or is somebody else's), so the
# sheet correctly does not carry it.
COND = ("while", "until", "when ", "once", "ignores", "instead", "its ",
        "takes ", "damage if", "choose")
def_bad = []
for e in entries:
    if e["class"] not in classes:
        continue
    for f in e["features"]:
        s_txt = str(f.get("summary") or "")
        if any(w in s_txt.lower() for w in COND):
            continue            # conditional — must NOT be permanent
        want = {t.lower() for _, t in RESIST.findall(s_txt[:90])}
        if not want:
            continue
        cid = sheet(e["class"], e["name"], int(f["level"]))
        with Session(m.engine) as s:
            d = m._pc_defenses(s.get(m.Character, cid))
        have = set(d.resist) | set(d.immune) | set(d.vulnerable)
        if not want.issubset(have):
            def_bad.append(f"{e['slug']}:{f['name']} wants {sorted(want)} "
                           f"got {sorted(have)}")
check("a damage defence stated in a feature is enforced in combat",
      not def_bad, "; ".join(def_bad[:3]))

# --- companion stat blocks materialize at their declared formula ------------
comp_slot = ROOT / "owned_books" / "summons_overrides.json"
comp_bad = []
if comp_slot.is_file():
    for entry in json.loads(comp_slot.read_text("utf-8")):
        lvl = max(int(entry.get("min_level") or 1), 5)
        dc, pb = 15, 3                      # -> caster modifier of +4
        mon = _sum.materialize(entry["slug"], level=lvl, engine=m.engine,
                               attack_bonus=7, save_dc=dc, proficiency_bonus=pb)
        if mon is None:
            comp_bad.append(f"{entry['slug']}: nothing materialized")
            continue
        # The entry declares the arithmetic; scaled() is the one implementation.
        # A spirit with VARIANTS is built from the variant's patch, so read back
        # which one materialize() chose rather than assuming the base block.
        vkey = (mon.raw or {}).get("variant")
        patch = ((entry.get("variants") or {}).get(vkey) or {}) if vkey else {}

        def term(name):
            return patch.get(name, entry.get(name))

        want_ac = _sum.scaled(term("armor_class"), lvl, default=10,
                              caster_mod=dc - 8 - pb)
        want_hp = max(1, _sum.scaled(term("hit_points"), lvl, default=1,
                                     caster_mod=dc - 8 - pb))
        if (mon.armor_class, mon.hit_points) != (want_ac, want_hp):
            comp_bad.append(f"{entry['slug']}: AC {mon.armor_class}/HP "
                            f"{mon.hit_points} != {want_ac}/{want_hp}")
check("every conjurable stat block matches its own declared formula",
      not comp_bad, "; ".join(comp_bad[:3]))

print()
print(f"{len(fails)} failure(s)" if fails else "ALL PASS")
if fails:
    print("\n".join(f"  - {f}" for f in fails))
sys.exit(1 if fails else 0)
