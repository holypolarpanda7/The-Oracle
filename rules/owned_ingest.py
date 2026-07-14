"""
LOCAL-ONLY ingestion of the user's owned book PDFs (see CLAUDE.md policy).

This module carries TOOLING ONLY — no book content lives in the repo. It reads
PDFs from the user's private library, extracts text into a gitignored workspace
(``owned_books/``), and parses mechanics into the gitignored ``oracle.db``.
The campaign is free; the data never leaves the user's machine.

    uv run python -m rules.owned_ingest              # extract all + parse all
    uv run python -m rules.owned_ingest --extract    # extraction only
    uv run python -m rules.owned_ingest --ocr=bigby  # OCR a scanned book
                                                     # (needs: uv sync --group ocr)

Currently parsed:
  - PHB 2024 feats      -> ``rules_feat`` (drives the CC wizard's Custom
    Lineage feat choice, so feat *prerequisites* are enforced in code).
  - PHB 2024 spells     -> ``rules_spell`` (all ~395; same-slug SRD 5.1 rows
    are OVERWRITTEN by the 2024 versions, renames keep the SRD slug).
  - PHB 2024 subclasses -> ``rules_subclass`` (all 48) and Xanathar's 31.
  - MM 2024 stat blocks -> ``rules_monster`` (2024 math overwrites SRD 5.1).

EDITION PRECEDENCE (see ``rules.ingest._upsert``): a row sourced from a
2024/2025 book is never overwritten by an older-edition ingest — new rules
replace old, never the reverse. Xanathar's subclasses that PHB 2024 reprints
(Zealot, Glamour, Gloom Stalker, Celestial) therefore keep their 2024 rows.

Scanned/damaged PDFs go through the OCR pipeline (``ocr_extract_pdf``) —
pypdf's output for the MM/XGtE display fonts is glyph-garbage, so those two
books plus Bigby's are OCR-extracted instead.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Optional

from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, select

from .ingest import get_engine, _upsert
from .models import Feat

# The user's private book library (Windows path via WSL mount when applicable).
DEFAULT_LIBRARY = Path(
    os.getenv("ORACLE_BOOK_LIBRARY",
              "/mnt/c/Users/holyp/OneDrive/Documents/D&D"))
# Gitignored extraction workspace at the repo root.
WORKSPACE = Path(__file__).resolve().parent.parent / "owned_books"


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


# ---------------------------------------------------------------------------
# Extraction: PDF -> workspace text (cached by size so re-runs are instant)
# ---------------------------------------------------------------------------

def extract_pdfs(library: Path = DEFAULT_LIBRARY, workspace: Path = WORKSPACE,
                 only: Optional[str] = None) -> list[Path]:
    """Extract every PDF in the library to ``workspace/<slug>.txt``. Returns
    the list of text files. Skips PDFs whose extraction is already cached."""
    from pypdf import PdfReader

    workspace.mkdir(exist_ok=True)
    out: list[Path] = []
    for pdf in sorted(library.glob("*.pdf")):
        if only and only.lower() not in pdf.name.lower():
            continue
        slug = _slugify(pdf.stem)
        txt = workspace / f"{slug}.txt"
        marker = f"# extracted-from-bytes: {pdf.stat().st_size}\n"
        if txt.exists():
            with open(txt, encoding="utf-8") as f:
                first = f.readline()
            # An OCR'd extraction (scanned book) also counts as cached —
            # never overwrite it with pypdf's empty text.
            if first == marker or first.startswith("# ocr-from-bytes:"):
                out.append(txt)
                continue
        print(f"[owned] extracting {pdf.name} ...", flush=True)
        try:
            reader = PdfReader(str(pdf))
            pages = []
            for i, page in enumerate(reader.pages):
                pages.append(page.extract_text() or "")
            body = "\n\f\n".join(pages)  # form-feed page separators
            txt.write_text(marker + body, encoding="utf-8")
            print(f"[owned]   -> {txt.name}: {len(reader.pages)} pages, "
                  f"{len(body) // 1024} KB text", flush=True)
            out.append(txt)
        except Exception as e:
            print(f"[owned]   FAILED {pdf.name}: {e}", flush=True)
    return out


# ---------------------------------------------------------------------------
# OCR extraction: for scanned/image PDFs where pypdf gets no text (Bigby's).
# Pure-Python pipeline (pypdfium2 render + RapidOCR), installed via the
# optional "ocr" dependency group: uv sync --group ocr
# ---------------------------------------------------------------------------

def ocr_extract_pdf(pdf: Path, workspace: Path = WORKSPACE,
                    scale: float = 2.0) -> Path:
    """OCR every page of ``pdf`` into ``workspace/<slug>.txt`` (cached).
    Reading order is column-aware: boxes left of the page midline come before
    boxes right of it, each column top-to-bottom (D&D books are two-column)."""
    import numpy as np
    import pypdfium2 as pdfium
    from rapidocr_onnxruntime import RapidOCR

    workspace.mkdir(exist_ok=True)
    txt = workspace / f"{_slugify(pdf.stem)}.txt"
    marker = f"# ocr-from-bytes: {pdf.stat().st_size}\n"
    if txt.exists():
        with open(txt, encoding="utf-8") as f:
            if f.readline() == marker:
                return txt

    ocr = RapidOCR()
    doc = pdfium.PdfDocument(str(pdf))
    pages_text: list[str] = []
    try:
        for i in range(len(doc)):
            page = doc[i]
            bitmap = page.render(scale=scale)
            img = np.asarray(bitmap.to_pil().convert("RGB"))
            result, _ = ocr(img)
            boxes: list[tuple[int, float, float, str]] = []
            if result:
                mid_x = img.shape[1] / 2
                for box, text, _conf in result:
                    xs = [p[0] for p in box]
                    ys = [p[1] for p in box]
                    col = 0 if (sum(xs) / len(xs)) < mid_x else 1
                    boxes.append((col, min(ys), min(xs), text))
            # Row clustering: within a column, boxes whose tops are within
            # ~half a line of the current row belong to it; rows then read
            # left-to-right. (Fixed buckets split real rows at boundaries.)
            ordered: list[str] = []
            for want_col in (0, 1):
                colboxes = sorted(b for b in boxes if b[0] == want_col)
                row: list[tuple[float, str]] = []
                row_y = None
                for _c, y, x, cell_text in colboxes:
                    if row_y is not None and y - row_y > 9:
                        ordered.extend(t for _x, t in sorted(row))
                        row = []
                    row.append((x, cell_text))
                    row_y = y
                ordered.extend(t for _x, t in sorted(row))
            body = "\n".join(ordered)
            pages_text.append(body)
            if (i + 1) % 10 == 0:
                print(f"[ocr] {pdf.name}: page {i + 1}/{len(doc)}", flush=True)
    finally:
        doc.close()
    txt.write_text(marker + "\n\f\n".join(pages_text), encoding="utf-8")
    print(f"[ocr] wrote {txt.name}: {len(pages_text)} pages", flush=True)
    return txt


# ---------------------------------------------------------------------------
# Parser: PHB 2024 feats
# ---------------------------------------------------------------------------

_FEAT_HEADER = re.compile(
    r"^\s*([A-Z][A-Z''\- ]{2,40})\s*\n"                 # NAME IN CAPS
    r"\s*(Origin|General|Fighting Style|Epic Boon) Feat"  # category line
    r"[ \t]*(\([^)]*\))?",                                # optional (Prerequisite: ...)
    re.M)

_CATEGORY_MIN_LEVEL = {"origin": 1, "general": 4, "fighting-style": 1, "epic-boon": 19}

# Canonical 2024 feat names (names are uncopyrightable facts). PDF extraction
# inserts stray spaces inside small-caps headers ("A LE RT"); we repair by
# matching the space/hyphen-stripped form against this list.
_CANONICAL_FEAT_NAMES = [
    # origin
    "Alert", "Crafter", "Healer", "Lucky", "Magic Initiate", "Musician",
    "Savage Attacker", "Skilled", "Tavern Brawler", "Tough",
    # general
    "Ability Score Improvement", "Actor", "Athlete", "Charger", "Chef",
    "Crossbow Expert", "Crusher", "Defensive Duelist", "Dual Wielder",
    "Durable", "Elemental Adept", "Fey-Touched", "Grappler",
    "Great Weapon Master", "Heavily Armored", "Heavy Armor Master",
    "Inspiring Leader", "Keen Mind", "Lightly Armored", "Mage Slayer",
    "Martial Weapon Training", "Medium Armor Master", "Moderately Armored",
    "Mounted Combatant", "Observant", "Piercer", "Poisoner", "Polearm Master",
    "Resilient", "Ritual Caster", "Sentinel", "Shadow-Touched", "Sharpshooter",
    "Shield Master", "Skill Expert", "Skulker", "Slasher", "Speedy",
    "Spell Sniper", "Telekinetic", "Telepathic", "War Caster", "Weapon Master",
    # fighting styles
    "Archery", "Blind Fighting", "Defense", "Dueling", "Great Weapon Fighting",
    "Interception", "Protection", "Thrown Weapon Fighting", "Two-Weapon Fighting",
    "Unarmed Fighting",
    # epic boons
    "Boon of Combat Prowess", "Boon of Dimensional Travel",
    "Boon of Energy Resistance", "Boon of Fate", "Boon of Fortitude",
    "Boon of Irresistible Offense", "Boon of Recovery", "Boon of Skill",
    "Boon of Speed", "Boon of Spell Recall", "Boon of the Night Spirit",
    "Boon of Truesight",
]
_CANONICAL_BY_KEY = {re.sub(r"[^a-z]", "", n.lower()): n for n in _CANONICAL_FEAT_NAMES}


def _repair_name(raw: str) -> str:
    """Map a glyph-spaced caps header onto its canonical feat name."""
    key = re.sub(r"[^a-z]", "", raw.lower())
    if key in _CANONICAL_BY_KEY:
        return _CANONICAL_BY_KEY[key]
    return " ".join(raw.title().split())


def parse_phb_feats(text: str) -> list[dict]:
    """Pull every feat block out of the PHB 2024 extraction."""
    feats: list[dict] = []
    matches = list(_FEAT_HEADER.finditer(text))
    for i, m in enumerate(matches):
        name = _repair_name(m.group(1))
        category = m.group(2).lower().replace(" ", "-")
        prereq_par = (m.group(3) or "").strip("() ")
        end = matches[i + 1].start() if i + 1 < len(matches) else m.end() + 4000
        body = text[m.end():end]
        # Benefit text: strip page breaks / running heads, cap length sanely.
        body = re.sub(r"\f", " ", body)
        body = re.sub(r"\s+", " ", body).strip()[:2000]
        # Prerequisite can also open the body ("Prerequisite: Level 4+, ...").
        prereq = prereq_par
        pm = re.match(r"Prerequisite:? ([^.]+)\.", body)
        if not prereq and pm:
            prereq = pm.group(1).strip()
        lvl = _CATEGORY_MIN_LEVEL.get(category, 1)
        lm = re.search(r"Level (\d+)\+", prereq or "")
        if lm:
            lvl = int(lm.group(1))
        feats.append({
            "slug": _slugify(name), "name": name, "category": category,
            "prerequisite": prereq or None, "min_level": lvl,
            "repeatable": "Repeatable" in body[:400] or "repeat" in (prereq or "").lower(),
            "benefit": body,
        })
    # Dedupe by slug (running heads can echo a header).
    seen: dict[str, dict] = {}
    for f in feats:
        if f["slug"] not in seen or len(f["benefit"]) > len(seen[f["slug"]]["benefit"]):
            seen[f["slug"]] = f
    return list(seen.values())


def ingest_feats(engine: Optional[Engine] = None,
                 database_url: Optional[str] = None,
                 workspace: Path = WORKSPACE) -> dict:
    """Parse feats from the extracted PHB 2024 and upsert into rules_feat."""
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)

    phb = next(iter(workspace.glob("*players-handbook-2024*.txt")), None)
    if phb is None:
        return {"error": "PHB 2024 extraction not found — run extract_pdfs() first"}
    text = phb.read_text(encoding="utf-8")
    feats = parse_phb_feats(text)

    result = {"feats_parsed": len(feats), "feats_new": 0}
    with Session(engine) as s:
        for f in feats:
            mapped = Feat(
                index_slug=f["slug"], name=f["name"], category=f["category"],
                prerequisite=f["prerequisite"], min_level=f["min_level"],
                repeatable=f["repeatable"], benefit=f["benefit"],
                source="Owned (PHB 2024) — local ingest",
            )
            if _upsert(s, Feat, f["slug"], mapped):
                result["feats_new"] += 1
        s.commit()
    return result


# ===========================================================================
# Parser: PHB 2024 spells (2024 versions OVERWRITE same-slug SRD 5.1 spells)
# ===========================================================================
# The PDF extraction is glyph-damaged in places ("Enchan tmen t",
# "casting Time :", "Level J"), so every pattern here is built space-tolerant
# and case-insensitive from canonical vocabulary, and values are repaired
# against known word lists afterwards.

_SCHOOLS = ("Abjuration", "Conjuration", "Divination", "Enchantment",
            "Evocation", "Illusion", "Necromancy", "Transmutation")

_CLASSES_2024 = ("Artificer", "Barbarian", "Bard", "Cleric", "Druid", "Fighter",
                 "Monk", "Paladin", "Ranger", "Rogue", "Sorcerer", "Warlock",
                 "Wizard")


def _sp(word: str) -> str:
    """Space-tolerant literal: matches the word with stray spaces injected
    between any letters (PDF small-caps damage)."""
    return r"\s*".join(re.escape(c) for c in word)


_SPELL_HEADER = re.compile(
    r"^([A-Z][A-Za-z '\-/]{2,44}?)[ .\-]*\n"                  # NAME (caps, glyph-damage tolerated)
    r"\s*(?i:(?:" + _sp("Level") + r"\s*([0-9lJIO])\s+)?"     # Level n (damaged digits OK)
    r"([A-Za-z][A-Za-z ]{6,18}?)"                             # school (validated fuzzily)
    r"(\s*" + _sp("Cantrip") + r")?\s*"                       # cantrip marker
    r"\(\s*([^)]+)\))", re.M)


def _match_school(raw: str) -> Optional[str]:
    """Fuzzy-map a possibly damaged school word ('Necrnmancy', 'Divinat ion')
    onto the canonical school list."""
    import difflib
    key = re.sub(r"[^a-z]", "", raw.lower())
    hits = difflib.get_close_matches(key, [s.lower() for s in _SCHOOLS],
                                     n=1, cutoff=0.75)
    return hits[0].capitalize() if hits else None


# 2014 SRD name -> 2024 PHB name. Parsed 2024 spells keep the SRD slug so the
# new version OVERWRITES the old row instead of duplicating it.
_RENAMES_2024 = {
    "Acid Arrow": "Melf's Acid Arrow",
    "Arcane Hand": "Bigby's Hand",
    "Arcane Sword": "Mordenkainen's Sword",
    "Arcanist's Magic Aura": "Nystul's Magic Aura",
    "Black Tentacles": "Evard's Black Tentacles",
    "Branding Smite": "Shining Smite",
    "Faithful Hound": "Mordenkainen's Faithful Hound",
    "Feeblemind": "Befuddlement",
    "Floating Disk": "Tenser's Floating Disk",
    "Freezing Sphere": "Otiluke's Freezing Sphere",
    "Hideous Laughter": "Tasha's Hideous Laughter",
    "Instant Summons": "Drawmij's Instant Summons",
    "Irresistible Dance": "Otto's Irresistible Dance",
    "Magnificent Mansion": "Mordenkainen's Magnificent Mansion",
    "Private Sanctum": "Mordenkainen's Private Sanctum",
    "Resilient Sphere": "Otiluke's Resilient Sphere",
    "Secret Chest": "Leomund's Secret Chest",
    "Telepathic Bond": "Rary's Telepathic Bond",
    "Tiny Hut": "Leomund's Tiny Hut",
}
_OLD_BY_2024_NAME = {new: old for old, new in _RENAMES_2024.items()}


def _fldrx(label: str) -> re.Pattern:
    return re.compile(_sp(label) + r"s?\s*:\s*([^\n]+)", re.I)


_FIELD = {
    "casting": _fldrx("Casting Time"),
    "range": _fldrx("Range"),
    "components": _fldrx("Component"),
    "duration": _fldrx("Duration"),
}

_DIGIT_REPAIR = {"l": "1", "J": "1", "I": "1", "O": "0"}

# Vocabulary for repairing glyph-spaced words in field values.
_VALUE_VOCAB = ("Bonus Action", "Reaction", "Action", "Ritual", "Instantaneous",
                "Concentration", "Touch", "Self", "Special", "Unlimited",
                "Sight", "minutes", "minute", "hours", "hour", "days", "day",
                "rounds", "round", "feet", "mile", "miles")


def _repair_value(s: str) -> str:
    """Repair a header field value: rejoin glyph-spaced vocabulary words,
    fix 'l'->'1' before time units, tidy spacing around punctuation."""
    for w in _VALUE_VOCAB:
        s = re.sub(r"\b" + r" ?".join(re.escape(c) for c in w) + r"\b", w, s,
                   flags=re.I)
    s = re.sub(r"\bl (minute|hour|round|day|mile)", r"1 \1", s)
    s = re.sub(r"\s+([,;:])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip(" .")


def _repair_classes(raw: str) -> list[str]:
    """Map a damaged class list ('Bard, Cleri c, Range r') onto canon names."""
    out = []
    for tok in raw.split(","):
        key = re.sub(r"[^a-z]", "", tok.lower())
        for c in _CLASSES_2024:
            if key == c.lower():
                out.append(c.lower())
                break
    return out


def _fix_ocr_digits(s: str) -> str:
    """PDF glyph damage: 'ld6' -> '1d6', 'l0' -> '10' before digits/dice."""
    s = re.sub(r"\bl(\d)", r"1\1", s)
    s = re.sub(r"(\d)l\b", r"\g<1>1", s)
    return s


def _collapse_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _harvest_spell_names(text: str, engine) -> dict:
    """Canonical spell names: the PHB's own class spell-list tables carry
    clean title-case names; the SRD DB supplies the rest."""
    from .models import Spell
    canon: dict[str, str] = {}
    for new in _RENAMES_2024.values():
        canon.setdefault(_collapse_key(new), new)
    with Session(engine) as s:
        for name in s.exec(select(Spell.name)):
            canon.setdefault(_collapse_key(name), name)
    canon.pop("", None)
    # Table rows look like: "Fireball Evocation C" / "Wall of Stone Evocation C"
    row = re.compile(r"^([A-Z][a-zA-Z'/]+(?: [a-zA-Z'/]+){0,4}) ("
                     + "|".join(_SCHOOLS) + r")( [CMR])*\s*$", re.M)
    for m in row.finditer(text):
        canon.setdefault(_collapse_key(m.group(1)), m.group(1).strip())
    return canon


def parse_phb_spells(text: str, canon: dict,
                     slug_by_key: Optional[dict] = None) -> list[dict]:
    slug_by_key = slug_by_key or {}
    spells: list[dict] = []
    matches = list(_SPELL_HEADER.finditer(text))
    for i, m in enumerate(matches):
        raw_name, lvl, raw_school, cantrip, classes = (
            m.group(1), m.group(2), m.group(3), m.group(4), m.group(5))
        school = _match_school(raw_school)
        if school is None:
            continue
        tidy = re.sub(r"\s*'\s*", "'", raw_name)
        name = (canon.get(_collapse_key(tidy))
                or re.sub(r"'S\b", "'s", " ".join(tidy.title().split())))
        level = 0 if cantrip else int(_DIGIT_REPAIR.get(lvl, lvl) or 0)
        end = matches[i + 1].start() if i + 1 < len(matches) else m.end() + 6000
        body = _fix_ocr_digits(text[m.start():end])
        # A real spell entry always opens with its Casting Time; table rows
        # and running heads that echo the header shape don't.
        if not _FIELD["casting"].search(body[:500]):
            continue
        fields = {k: (_repair_value(rx.search(body).group(1)) if rx.search(body) else None)
                  for k, rx in _FIELD.items()}
        # Description: after the Duration line.
        dm = _FIELD["duration"].search(body)
        desc = re.sub(r"\s+", " ", body[dm.end():]).strip()[:3000] if dm else ""
        comp = fields.get("components") or ""
        material = None
        mm2 = re.search(r"M\s*\(([^)]*)\)", comp)
        if mm2:
            material = mm2.group(1)
        # Slugs must stay canonical with the SRD rows ('hunters-mark', not
        # 'hunter-s-mark') so the 2024 version overwrites, never duplicates.
        old = _OLD_BY_2024_NAME.get(name)
        spells.append({
            "slug": (slug_by_key.get(_collapse_key(name))
                     or (slug_by_key.get(_collapse_key(old)) if old else None)
                     or _slugify(old or name)),
            "name": name, "level": level,
            "school": school,
            "classes": _repair_classes(classes),
            "casting_time": fields.get("casting"),
            "range": fields.get("range"),
            "duration": fields.get("duration"),
            "material": material,
            "concentration": "concentration" in (fields.get("duration") or "").lower(),
            "ritual": "ritual" in (fields.get("casting") or "").lower(),
            "components": [c for c in ("V", "S", "M") if re.search(rf"\b{c}\b", comp)],
            "desc": desc,
        })
    # Dedupe by slug, keep the longest description.
    seen: dict[str, dict] = {}
    for sp in spells:
        if sp["slug"] not in seen or len(sp["desc"]) > len(seen[sp["slug"]]["desc"]):
            seen[sp["slug"]] = sp
    return list(seen.values())


def _fallback_missing_spells(text: str, spells: list[dict], engine) -> list[dict]:
    """Second pass for entries whose header line is scrambled beyond the
    primary regex (e.g. 'SUNBEAM . . . / 6 Evoca tion ...'). We know which
    spells to expect from the SRD DB; locate the name at a line start with a
    Casting Time nearby, take the 2024 body text, and fill any unreadable
    mechanical fields from the SRD row (level/school are stable across
    editions for retained spells)."""
    import difflib
    from .models import Spell
    got = {sp["slug"] for sp in spells}
    with Session(engine) as s:
        srd_rows = list(s.exec(select(Spell)))
    missing = {}
    for row in srd_rows:
        name_2024 = _RENAMES_2024.get(row.name, row.name)
        if row.index_slug in got or _slugify(name_2024) in got:
            continue
        missing[_collapse_key(name_2024)] = (row, name_2024)
    if not missing:
        return []

    level_tok = re.compile(r"(?i)" + _sp("Level"))

    def _alpha(s: str) -> str:
        return re.sub(r"[^a-z]", "", s.lower())

    def _best_tail_ratio(zone: str, key: str) -> float:
        # Junk glyphs shift the name's start; try several suffix lengths.
        return max(difflib.SequenceMatcher(None, zone[-n:], key).ratio()
                   for n in range(max(3, len(key) - 2), len(key) + 5))

    # (ratio, name_key, anchor_pos) candidates: for every Casting Time anchor,
    # the name is the alpha-collapsed tail of the text before its Level token
    # (or before the school + classes when the Level token itself is damaged).
    candidates: list[tuple[float, str, int]] = []
    for cm in _FIELD["casting"].finditer(text):
        pre = text[max(0, cm.start() - 170):cm.start()]
        lms = list(level_tok.finditer(pre))
        zones = []
        if lms:
            zones.append(_alpha(pre[:lms[-1].start()]))
        elif (p := pre.rfind("(")) > 0:
            z = _alpha(pre[:p])
            zones.append(z)
            for sch in _SCHOOLS:
                sk = sch.lower()
                if (len(z) > len(sk) and difflib.SequenceMatcher(
                        None, z[-len(sk):], sk).ratio() >= 0.7):
                    zones.append(z[:-len(sk)])
                    break
        for zone in zones:
            if not zone:
                continue
            for key in missing:
                r = _best_tail_ratio(zone, key)
                if r >= 0.75:
                    candidates.append((r, key, cm.start()))

    found: list[dict] = []
    used_keys: set[str] = set()
    used_anchors: set[int] = set()
    for r, key, pos in sorted(candidates, reverse=True):
        if key in used_keys or pos in used_anchors:
            continue
        used_keys.add(key)
        used_anchors.add(pos)
        row, name_2024 = missing[key]
        body = _fix_ocr_digits(text[pos:pos + 6000])
        # Only trust fields inside the header region; a scrambled line must
        # fall back to the SRD row, not match the NEXT spell's fields.
        head = body[:350]
        fields = {k: (_repair_value(rx.search(head).group(1)) if rx.search(head) else None)
                  for k, rx in _FIELD.items()}
        ends = [rx.search(head).end() for rx in _FIELD.values() if rx.search(head)]
        desc = (re.sub(r"\s+", " ", body[max(ends):]).strip()[:3000]
                if ends else "")
        comp = fields.get("components") or ""
        mm2 = re.search(r"M\s*\(([^)]*)\)", comp)
        duration = fields.get("duration") or row.duration
        found.append({
            "slug": row.index_slug, "name": name_2024,
            "level": row.level, "school": row.school,
            "classes": row.classes or [],
            "casting_time": fields.get("casting") or row.casting_time,
            "range": fields.get("range") or row.range,
            "duration": duration,
            "material": mm2.group(1) if mm2 else row.material,
            "concentration": ("concentration" in (duration or "").lower()
                              or row.concentration),
            "ritual": row.ritual,
            "components": ([c for c in ("V", "S", "M")
                            if re.search(rf"\b{c}\b", comp, re.I)]
                           or row.components),
            "desc": desc or row.desc,
        })
    return found


def ingest_spells(engine=None, database_url=None, workspace: Path = WORKSPACE) -> dict:
    from .models import Spell
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    phb = next(iter(workspace.glob("*players-handbook-2024*.txt")), None)
    if phb is None:
        return {"error": "PHB 2024 extraction not found"}
    text = phb.read_text(encoding="utf-8")
    canon = _harvest_spell_names(text, engine)
    with Session(engine) as s:
        slug_by_key = {_collapse_key(n): slug for n, slug in
                       s.exec(select(Spell.name, Spell.index_slug))}
    spells = parse_phb_spells(text, canon, slug_by_key)
    spells += _fallback_missing_spells(text, spells, engine)
    result = {"spells_parsed": len(spells), "spells_new": 0, "spells_updated": 0}
    with Session(engine) as s:
        for sp in spells:
            mapped = Spell(
                index_slug=sp["slug"], name=sp["name"], level=sp["level"],
                school=sp["school"], casting_time=sp["casting_time"],
                range=sp["range"], duration=sp["duration"], material=sp["material"],
                concentration=sp["concentration"], ritual=sp["ritual"],
                components=sp["components"], classes=sp["classes"], desc=sp["desc"],
                source="Owned (PHB 2024) — local ingest",
            )
            if _upsert(s, Spell, sp["slug"], mapped):
                result["spells_new"] += 1
            else:
                result["spells_updated"] += 1
        s.commit()
    return result


# ===========================================================================
# Parser: PHB 2024 subclasses (all 48; 2024 versions OVERWRITE same subclass)
# ===========================================================================
# Canonical 2024 subclass names per class (names are uncopyrightable facts).
_SUBCLASSES_2024 = {
    "Barbarian": ["Path of the Berserker", "Path of the Wild Heart",
                  "Path of the World Tree", "Path of the Zealot"],
    "Bard": ["College of Dance", "College of Glamour", "College of Lore",
             "College of Valor"],
    "Cleric": ["Life Domain", "Light Domain", "Trickery Domain", "War Domain"],
    "Druid": ["Circle of the Land", "Circle of the Moon", "Circle of the Sea",
              "Circle of the Stars"],
    "Fighter": ["Battle Master", "Champion", "Eldritch Knight", "Psi Warrior"],
    "Monk": ["Warrior of Mercy", "Warrior of Shadow",
             "Warrior of the Elements", "Warrior of the Open Hand"],
    "Paladin": ["Oath of Devotion", "Oath of Glory", "Oath of the Ancients",
                "Oath of Vengeance"],
    "Ranger": ["Beast Master", "Fey Wanderer", "Gloom Stalker", "Hunter"],
    "Rogue": ["Arcane Trickster", "Assassin", "Soulknife", "Thief"],
    "Sorcerer": ["Aberrant Sorcery", "Clockwork Sorcery", "Draconic Sorcery",
                 "Wild Magic Sorcery"],
    "Warlock": ["Archfey Patron", "Celestial Patron", "Fiend Patron",
                "Great Old One Patron"],
    "Wizard": ["Abjurer", "Diviner", "Evoker", "Illusionist"],
}

# Level-3 feature names per 2024 subclass (uncopyrightable facts). Used to
# anchor a section whose header line was destroyed by the PDF extraction, and
# to validate found sections.
_FIRST_FEATURES_2024 = {
    "Path of the Berserker": ["Frenzy"],
    "Path of the Wild Heart": ["Animal Speaker", "Rage of the Wilds"],
    "Path of the World Tree": ["Vitality of the Tree"],
    "Path of the Zealot": ["Divine Fury", "Warrior of the Gods"],
    "College of Dance": ["Dazzling Footwork"],
    "College of Glamour": ["Beguiling Magic", "Mantle of Inspiration"],
    "College of Lore": ["Bonus Proficiencies", "Cutting Words"],
    "College of Valor": ["Combat Inspiration", "Martial Training"],
    "Life Domain": ["Disciple of Life", "Life Domain Spells"],
    "Light Domain": ["Light Domain Spells", "Radiance of the Dawn",
                     "Warding Flare"],
    "Trickery Domain": ["Blessing of the Trickster", "Trickery Domain Spells"],
    "War Domain": ["Guided Strike", "War Domain Spells", "War Priest"],
    "Circle of the Land": ["Circle of the Land Spells", "Land's Aid"],
    "Circle of the Moon": ["Circle Forms", "Circle of the Moon Spells"],
    "Circle of the Sea": ["Circle of the Sea Spells", "Wrath of the Sea"],
    "Circle of the Stars": ["Star Map", "Starry Form"],
    "Battle Master": ["Combat Superiority", "Student of War"],
    "Champion": ["Improved Critical", "Remarkable Athlete"],
    "Eldritch Knight": ["Spellcasting", "War Bond"],
    "Psi Warrior": ["Psionic Power"],
    "Warrior of Mercy": ["Hand of Harm", "Hand of Healing",
                         "Implements of Mercy"],
    "Warrior of Shadow": ["Shadow Arts"],
    "Warrior of the Elements": ["Elemental Attunement", "Manipulate Elements"],
    "Warrior of the Open Hand": ["Open Hand Technique"],
    "Oath of Devotion": ["Oath of Devotion Spells", "Sacred Weapon"],
    "Oath of Glory": ["Inspiring Smite", "Oath of Glory Spells",
                      "Peerless Athlete"],
    "Oath of the Ancients": ["Nature's Wrath", "Oath of the Ancients Spells"],
    "Oath of Vengeance": ["Oath of Vengeance Spells", "Vow of Enmity"],
    "Beast Master": ["Primal Companion"],
    "Fey Wanderer": ["Dreadful Strikes", "Fey Wanderer Spells",
                     "Otherworldly Glamour"],
    "Gloom Stalker": ["Dread Ambusher", "Gloom Stalker Spells",
                      "Umbral Sight"],
    "Hunter": ["Hunter's Lore", "Hunter's Prey"],
    "Arcane Trickster": ["Mage Hand Legerdemain", "Spellcasting"],
    "Assassin": ["Assassinate", "Assassin's Tools"],
    "Soulknife": ["Psionic Power", "Psychic Blades"],
    "Thief": ["Fast Hands", "Second-Story Work"],
    "Aberrant Sorcery": ["Psionic Spells", "Telepathic Speech"],
    "Clockwork Sorcery": ["Clockwork Spells", "Restore Balance"],
    "Draconic Sorcery": ["Draconic Resilience", "Draconic Spells"],
    "Wild Magic Sorcery": ["Wild Magic Surge", "Tides of Chaos"],
    "Archfey Patron": ["Archfey Spells", "Steps of the Fey"],
    "Celestial Patron": ["Celestial Spells", "Healing Light"],
    "Fiend Patron": ["Dark One's Blessing", "Fiend Spells"],
    "Great Old One Patron": ["Awakened Mind", "Great Old One Spells",
                             "Psychic Spells"],
    "Abjurer": ["Abjuration Savant", "Arcane Ward"],
    "Diviner": ["Divination Savant", "Portent"],
    "Evoker": ["Evocation Savant", "Potent Cantrip"],
    "Illusionist": ["Illusion Savant", "Improved Illusions"],
}

# 2014 SRD subclass name -> 2024 name (kept keyed on the SRD slug so the 2024
# version overwrites the old row).
_SUBCLASS_RENAMES_2024 = {
    "Way of the Open Hand": "Warrior of the Open Hand",
    "School of Evocation": "Evoker",
    "Draconic Bloodline": "Draconic Sorcery",
    "The Fiend": "Fiend Patron",
}
_SUBCLASS_OLD_BY_NEW = {new: old for old, new in _SUBCLASS_RENAMES_2024.items()}

_SUBCLASS_FEATURE = re.compile(
    r"^\s*(?i:" + _sp("Level") + r")\s*([0-9lJIO]{1,2})\s*:\s*([^\n]{2,60}?)\s*$",
    re.M)


def _repair_int(raw: str) -> int:
    return int("".join(_DIGIT_REPAIR.get(c, c) for c in raw))


_TINY_WORDS = {"a", "an", "and", "at", "in", "of", "on", "or", "the", "to"}


# Game terms the general-English wordlist splits wrongly.
_GAME_WORDS = ("proficiencies", "proficiency", "spellcasting", "cantrips",
               "cantrip", "darkvision", "hexblade", "kensei", "sunbolt",
               "slayer", "stormsoul")


def _split_caps_words(chunk: str) -> str:
    import wordninja
    words = wordninja.split(re.sub(r"[^A-Za-z]", "", chunk))
    # Glue fragments the wordlist couldn't place ('er', lone letters).
    merged: list[str] = []
    for w in words:
        stray = len(w) <= 2 and w.lower() not in _TINY_WORDS
        prev_stray = bool(merged) and len(merged[-1]) <= 2 \
            and merged[-1].lower() not in _TINY_WORDS
        if merged and (stray or prev_stray):
            merged[-1] += w
        else:
            merged.append(w)
    # Re-merge fragments of game terms ('prof ici enc ies', 'monsters layer').
    i = 0
    remerged: list[str] = []
    while i < len(merged):
        joined = None
        for j in range(min(len(merged), i + 4), i, -1):
            cat = "".join(merged[i:j]).lower()
            hit = next((g for g in _GAME_WORDS
                        if cat == g or (j > i + 1 and cat == g + "s")), None)
            if hit is None and j == i + 2:
                # 'monsters layer' -> 'monster slayer' style boundary slips
                for g in _GAME_WORDS:
                    if cat.endswith(g) and cat[:-len(g)]:
                        remerged.append(cat[:-len(g)])
                        joined = g
                        break
                if joined:
                    i = j
                    break
            if hit:
                joined = cat
                i = j
                break
        if joined:
            remerged.append(joined)
        else:
            remerged.append(merged[i])
            i += 1
    return " ".join(w.capitalize() for w in remerged)


def _repair_caps_name(raw: str) -> str:
    """Repair a glyph-spaced caps header ('Dr E Ad Ambusher', \"LAND 'S AID\")
    by re-splitting the collapsed letters with an English wordlist. Possessive
    segments are re-split separately so the apostrophe survives."""
    tidy = re.sub(r"\s*'\s*", "'", raw).strip()
    tokens = re.findall(r"[A-Za-z]+", tidy)
    damaged = (any(len(t) <= 2 for t in tokens)
               or any(len(t) > 8 for t in tokens)
               or bool(re.search(r"'[sS][A-Za-z]", tidy)))
    if not damaged:
        return re.sub(r"'S\b", "'s", " ".join(tidy.title().split()))
    segments = re.split(r"'[sS](?=[^a-zA-Z]|$)|'[sS](?=[A-Z]{3,})| '", tidy)
    if len(segments) > 1:
        return "'s ".join(_split_caps_words(seg) for seg in segments if seg.strip())
    return _split_caps_words(tidy)


def parse_phb_subclasses(text: str) -> list[dict]:
    """Pull all 48 subclass sections out of the PHB 2024 extraction.

    Header lines can be ornament-damaged ('Sou ~-~-: :~ IFE') or missing
    outright, so each subclass is located by (a) fuzzy line scan for its name,
    validated by a level-3+ feature nearby, else (b) anchoring at its known
    first level-3 feature."""
    import difflib

    feature_matches = [(m.start(), _repair_int(m.group(1)),
                        _collapse_key(m.group(2)), m)
                       for m in _SUBCLASS_FEATURE.finditer(text)]
    lines = [(m.start(), re.sub(r"[^a-z']", "", m.group(1).lower()))
             for m in re.finditer(r"^([^\n]{3,60})$", text, re.M)]

    def _expects(name: str, fkey: str) -> bool:
        return any(difflib.SequenceMatcher(None, fkey, _collapse_key(f)).ratio()
                   >= 0.75 for f in _FIRST_FEATURES_2024[name])

    # Phase 1: fuzzy line scan, validated by the subclass's OWN first feature.
    # Sibling names are near-identical ('Circle of the Sea'/'... Land'), so a
    # line only counts for the subclass it matches BEST, not any that clears
    # the threshold.
    all_names = [(cls, name, _collapse_key(name))
                 for cls, names in _SUBCLASSES_2024.items() for name in names]
    best_lines: dict[str, list[int]] = {}
    for lpos, lkey in lines:
        best = (0.0, None)
        for _cls, name, key in all_names:
            zone = lkey[:len(key) + 3]
            r = difflib.SequenceMatcher(None, zone, key).ratio()
            if r > best[0]:
                best = (r, name)
        if best[0] >= 0.72 and best[1]:
            best_lines.setdefault(best[1], []).append(lpos)

    headers: list[tuple[int, str, str, bool]] = []  # (pos, class, name, has_hdr)
    missing: list[tuple[str, str]] = []
    for cls, name, _key in all_names:
        pos = None
        for lpos in best_lines.get(name, []):
            nxt = _SUBCLASS_FEATURE.search(text, lpos, lpos + 3500)
            if (nxt and _repair_int(nxt.group(1)) == 3
                    and _expects(name, _collapse_key(nxt.group(2)))):
                pos = lpos
                break
        if pos is not None:
            headers.append((pos, cls, name, True))
        else:
            missing.append((cls, name))

    # Phase 2: header destroyed — anchor at the subclass's first L3 feature.
    # Skip features legitimately owned by an already-found section (Psi
    # Warrior and Soulknife both have 'Psionic Power').
    found_sorted = sorted(headers)
    for cls, name in missing:
        anchored = False
        for fpos, lvl, fkey, _m in feature_matches:
            if lvl != 3 or not _expects(name, fkey):
                continue
            owner = None
            for hpos, _hc, hname, _hh in found_sorted:
                if hpos <= fpos:
                    owner = hname
                else:
                    break
            if owner and _expects(owner, fkey):
                continue
            headers.append((fpos, cls, name, False))
            anchored = True
            break
        if not anchored:
            print(f"[owned]   subclass NOT FOUND: {cls}/{name}")
    headers.sort()
    positions = [h[0] for h in headers]

    out: list[dict] = []
    for idx, (pos, cls, name, has_hdr) in enumerate(headers):
        end = positions[idx + 1] if idx + 1 < len(positions) else pos + 30000
        section = text[pos:min(end, pos + 30000)]
        feats = list(_SUBCLASS_FEATURE.finditer(section))
        features = []
        prev_lvl = 0
        for j, fm in enumerate(feats):
            lvl = _repair_int(fm.group(1))
            if lvl < 3 or lvl < prev_lvl:
                break  # leaked into a neighbouring section's features
            prev_lvl = lvl
            fend = feats[j + 1].start() if j + 1 < len(feats) else len(section)
            summary = re.sub(r"\s+", " ",
                             _fix_ocr_digits(section[fm.end():fend])).strip()[:1200]
            features.append({"level": lvl, "name": _repair_caps_name(fm.group(2)),
                             "summary": summary})
        desc = ""
        if has_hdr and feats:
            first_line_end = section.find("\n")
            desc = re.sub(r"\s+", " ",
                          section[first_line_end:feats[0].start()]).strip()[:600]
        out.append({"class": cls, "name": name, "features": features,
                    "description": desc})
    return out


def ingest_subclasses(engine=None, database_url=None,
                      workspace: Path = WORKSPACE) -> dict:
    from .models import DndClass, Subclass
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    phb = next(iter(workspace.glob("*players-handbook-2024*.txt")), None)
    if phb is None:
        return {"error": "PHB 2024 extraction not found"}
    text = phb.read_text(encoding="utf-8")
    subs = parse_phb_subclasses(text)
    result = {"subclasses_parsed": len(subs), "new": 0, "updated": 0}
    with Session(engine) as s:
        slug_by_key = {_collapse_key(n): slug for n, slug in
                       s.exec(select(Subclass.name, Subclass.index_slug))}
        for sc in subs:
            old = _SUBCLASS_OLD_BY_NEW.get(sc["name"])
            slug = (slug_by_key.get(_collapse_key(sc["name"]))
                    or (slug_by_key.get(_collapse_key(old)) if old else None)
                    or _slugify(sc["name"]))
            mapped = Subclass(
                index_slug=slug, name=sc["name"], class_name=sc["class"],
                class_slug=_slugify(sc["class"]), features=sc["features"],
                description=sc["description"],
                source="Owned (PHB 2024) — local ingest",
            )
            if _upsert(s, Subclass, slug, mapped):
                result["new"] += 1
            else:
                result["updated"] += 1
        # 2024 rule: every core class picks its subclass at level 3.
        for cls in _SUBCLASSES_2024:
            row = s.exec(select(DndClass).where(DndClass.name == cls)).first()
            if row and row.subclass_level != 3:
                row.subclass_level = 3
                s.add(row)
        s.commit()
    return result


# ===========================================================================
# Parser: Xanathar's Guide subclasses (2014-era; NEVER overwrites 2024 rows)
# ===========================================================================
# The XGtE extraction is OCR'd (the PDF's fonts defeat pypdf), which drops
# many inter-word spaces — headers arrive like 'GLOOMSTALKERMAGIC'. All
# matching here is collapse-key based; names are re-split with the wordlist.

_SUBCLASSES_XGTE = {
    "Barbarian": ["Path of the Ancestral Guardian", "Path of the Storm Herald",
                  "Path of the Zealot"],
    "Bard": ["College of Glamour", "College of Swords", "College of Whispers"],
    "Cleric": ["Forge Domain", "Grave Domain"],
    "Druid": ["Circle of Dreams", "Circle of the Shepherd"],
    "Fighter": ["Arcane Archer", "Cavalier", "Samurai"],
    "Monk": ["Way of the Drunken Master", "Way of the Kensei",
             "Way of the Sun Soul"],
    "Paladin": ["Oath of Conquest", "Oath of Redemption"],
    "Ranger": ["Gloom Stalker", "Horizon Walker", "Monster Slayer"],
    "Rogue": ["Inquisitive", "Mastermind", "Scout", "Swashbuckler"],
    "Sorcerer": ["Divine Soul", "Shadow Magic", "Storm Sorcery"],
    "Warlock": ["The Celestial", "The Hexblade"],
    "Wizard": ["War Magic"],
}
# XGtE subclasses that PHB 2024 reprints under another name: force the same
# slug so the edition guard keeps the 2024 row (never a duplicate).
_XGTE_SLUG_OVERRIDES = {"The Celestial": "celestial-patron"}

# 'Starting at 3rd level…' / 'At 7th level…' / 'By 11th level…', spaceless.
# OCR sometimes drops the digit ('Startingatrd level'); the ordinal suffix
# still pins it down for 1st/2nd/3rd.
_XGTE_LEVEL = re.compile(
    r"(?:startingat|beginningat|at|by)(\d{0,2})(st|nd|rd|th)level")
_ORDINAL_LEVEL = {"st": 1, "nd": 2, "rd": 3}


def _xgte_level(m: re.Match) -> Optional[int]:
    if m.group(1):
        return int(m.group(1))
    return _ORDINAL_LEVEL.get(m.group(2))

# Chapter/section headers that sit inside subclass sections in page layout.
_XGTE_JUNK_HEADERS = {
    "otherworldlypatrons", "martialarchetypes", "rangerarchetypes",
    "roguisharchetypes", "primalpaths", "sacredoaths", "divinedomains",
    "druidcircles", "monastictraditions", "bardcolleges", "sorcerousorigins",
    "arcanetradition", "arcanetraditions", "eldritchinvocations",
}


def parse_xgte_subclasses(text: str) -> list[dict]:
    import difflib
    lines = list(re.finditer(r"^([^\n]{3,60})$", text, re.M))

    # Caps-ish lines that are candidate headers (subclass or feature).
    def is_capsish(s: str) -> bool:
        alpha = [c for c in s if c.isalpha()]
        return bool(alpha) and sum(c.isupper() for c in alpha) / len(alpha) > 0.8

    headers: list[tuple[int, str, str]] = []
    for cls, names in _SUBCLASSES_XGTE.items():
        for name in names:
            key = _collapse_key(name)
            # Caps headers are the real thing; a case-flipped exact match
            # ('ScouT') is only trusted when no caps line validates, since
            # prose mentions ('Swashbuckler.') also collapse to the key.
            fallback = None
            found = None
            for lm in lines:
                raw = lm.group(1).strip()
                lk = _collapse_key(raw)
                caps = is_capsish(raw)
                if not (lk == key or (caps and difflib.SequenceMatcher(
                        None, lk, key).ratio() >= 0.9)):
                    continue
                # Real section: a leveled feature follows within ~4000 chars
                # (TOC rows and running heads have none).
                window = re.sub(r"[^a-z0-9]", "",
                                text[lm.end():lm.end() + 4000].lower())
                if not _XGTE_LEVEL.search(window):
                    continue
                if caps:
                    found = lm.start()
                    break
                if fallback is None:
                    fallback = lm.start()
            pos = found if found is not None else fallback
            if pos is not None:
                headers.append((pos, cls, name))
    headers.sort()
    positions = [h[0] for h in headers]

    out: list[dict] = []
    for idx, (pos, cls, name) in enumerate(headers):
        end = positions[idx + 1] if idx + 1 < len(positions) else pos + 25000
        section = text[pos:min(end, pos + 25000)]
        # Feature headers: caps-ish lines followed closely by a level phrase.
        feats: list[tuple[int, int, str, int]] = []  # (start, lvl, name, body_at)
        for lm in re.finditer(r"^([^\n]{4,50})$", section, re.M):
            raw = lm.group(1).strip()
            if not is_capsish(raw) or len(_collapse_key(raw)) < 4:
                continue
            near = re.sub(r"[^a-z0-9]", "",
                          section[lm.end():lm.end() + 260].lower())
            lv = _XGTE_LEVEL.search(near)
            if lv and _xgte_level(lv) is not None:
                feats.append((lm.start(), _xgte_level(lv),
                              _repair_caps_name(raw), lm.end()))
        features = []
        name_key = _collapse_key(name)
        prev_lvl = 0
        for j, (fs, lvl, fname, fbody) in enumerate(feats):
            fkey = _collapse_key(fname)
            # Drop running heads / archetype sidebars caught as features.
            if ("chapter" in fkey or fkey.endswith("features")
                    or fkey in _XGTE_JUNK_HEADERS
                    or fkey in name_key
                    or any(fkey.startswith(_collapse_key(c) + "of")
                           for c in _SUBCLASSES_XGTE)):
                continue
            if lvl < prev_lvl:
                break  # leaked into the next section's features
            prev_lvl = lvl
            fend = feats[j + 1][0] if j + 1 < len(feats) else len(section)
            summary = re.sub(r"\s+", " ",
                             _fix_ocr_digits(section[fbody:fend])).strip()[:1200]
            features.append({"level": lvl, "name": fname, "summary": summary})
        desc_end = feats[0][0] if feats else len(section)
        desc = re.sub(r"\s+", " ",
                      section[section.find("\n"):desc_end]).strip()[:600]
        if features:
            out.append({"class": cls, "name": name, "features": features,
                        "description": desc})
    return out


def ingest_xgte_subclasses(engine=None, database_url=None,
                           workspace: Path = WORKSPACE) -> dict:
    from .models import Subclass
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    x = next(iter(workspace.glob("*xanathar*.txt")), None)
    if x is None:
        return {"error": "Xanathar's extraction not found"}
    text = x.read_text(encoding="utf-8")
    subs = parse_xgte_subclasses(text)
    result = {"subclasses_parsed": len(subs), "new": 0, "kept_2024": 0}
    with Session(engine) as s:
        slug_by_key = {_collapse_key(n): slug for n, slug in
                       s.exec(select(Subclass.name, Subclass.index_slug))}
        for sc in subs:
            slug = (_XGTE_SLUG_OVERRIDES.get(sc["name"])
                    or slug_by_key.get(_collapse_key(sc["name"]))
                    or _slugify(sc["name"]))
            mapped = Subclass(
                index_slug=slug, name=sc["name"], class_name=sc["class"],
                class_slug=_slugify(sc["class"]), features=sc["features"],
                description=sc["description"],
                source="Owned (Xanathar's Guide) — local ingest",
            )
            if _upsert(s, Subclass, slug, mapped):
                result["new"] += 1
            else:
                result["kept_2024"] += 1
        s.commit()
    return result


# ===========================================================================
# Parser: Monster Manual 2024 stat blocks (2024 math OVERWRITES SRD 5.1 rows)
# ===========================================================================
# MM 2024 name lines use a display font that extracts as near-garbage
# ('Vluprne FaullllR' = Vampire Familiar), so blocks are anchored on their
# 'AC n' line and names are fuzzy-canonicalized against the SRD monster list.

def _sp_fuzzy(label: str) -> str:
    """Like _sp but also tolerant of common glyph substitutions (I/l/1, O/0)."""
    out = []
    for c in label:
        lc = c.lower()
        if lc == "i" or c == "l" or c == "1":
            out.append("[Il1l]")
        elif lc == "o" or c == "0":
            out.append("[Oo0]")
        else:
            out.append(re.escape(c))
    return r"\s*".join(out)


_MON_DIGITS = {"l": "1", "J": "1", "I": "1", "O": "0", "]": "1", "}": "1",
               "o": "0", "r": "1", "s": "5", "S": "5"}

# The MM's small-caps font extracts with systematic letter confusion
# (t/i/n/u/r/l interchange, c/g interchange, o/0). For fuzzy comparison we
# mask each confusion group to a single placeholder so 'AcrroNs' == 'Actions'.
_MASK_GROUPS = ({"r", "i", "t", "n", "u", "l", "1"}, {"c", "g"}, {"o", "0"})


def _mask_confusables(s: str) -> str:
    out = []
    for ch in re.sub(r"[^a-z0-9]", "", s.lower()):
        for i, grp in enumerate(_MASK_GROUPS):
            if ch in grp:
                out.append("*#@"[i])
                break
        else:
            out.append(ch)
    return "".join(out)


def _mon_int(raw: str) -> Optional[int]:
    try:
        return int("".join(_MON_DIGITS.get(c, c) for c in raw))
    except ValueError:
        return None


_MON_AC = re.compile(r"^\s*AC\s*([\dlJO\]]{1,2})\b[^\n]*$", re.M)
_MON_HP = re.compile(
    r"(?i)^\s*HP\s*([\dlJOrs\],]{1,4})\s*[（(]([^)）]+)[)）]", re.M)
_MON_SPEED = re.compile(r"(?i)^\s*" + _sp_fuzzy("speed") + r"\s*([^\n]+)", re.M)
# CR line tolerates OCR full-width parens and the 'or N in Lair' XP variant:
# 'CR14（XP11,500,or13,000inLair;PB+5)'.
_MON_CR = re.compile(
    r"(?i)^\s*CR\s*([\dlJOr\]]{1,2}(?:/[\dlJOr\]]{1,2})?)\s*"
    r"[（(]\s*XP\s*([\dlJOrs\],]+)[^)）]*?PB\s*\+?\s*([\dlJOr\]]{1,2})", re.M)
# Labelled score/mod/save triples ('Srn 17 +3 +3', 'STR23+6+6'). Layout
# damage can drop whole columns, so scores are slotted into STR..CHA by
# aligning the damaged labels against the canonical order — never by position
# alone. Modifiers are matched (glyph junk like \"-'l\" = -1) then discarded.
# (?<![A-Za-z]) not \b: merged cells ('…+10DEX10+0+7') put a digit before
# the next label, which \b treats as word-internal.
_MON_TRIPLE = re.compile(
    r"(?<![A-Za-z])([A-Za-z.'’]{2,6})\s*([\dlJOrs\]]{1,2})\s*"
    r"([+-]\s*'?[\dlJOr\]]{1,2})\s*([+-]\s*'?[\dlJOr\]]{1,2})")
_MON_DOUBLE = re.compile(
    r"(?<![A-Za-z])([A-Za-z.'’]{2,6})\s*([\dlJOrs\]]{1,2})\s*"
    r"([+-]\s*'?[\dlJOr\]]{1,2})")
_ABILITY_ORDER = ("str", "dex", "con", "int", "wis", "cha")


def _classify_ability_label(label: str) -> Optional[str]:
    """Damaged labels keep a recognizable first glyph: 'Srn'=STR, 'lrr'=INT,
    'Wts'=WIS, 'Cor.r'=CON, 'Cnr'/'Cta'=CHA (CON keeps its 'o')."""
    chars = re.sub(r"[^a-z0-9]", "", label.lower())
    if not chars:
        return None
    c0 = chars[0]
    if c0 == "d":
        return "dex"
    if c0 == "w":
        return "wis"
    if c0 == "s":
        return "str"
    if c0 in "li1j]":
        return "int"
    if c0 == "c":
        return "con" if len(chars) > 1 and chars[1] in "o0" else "cha"
    return None


def _align_abilities(pairs: list[tuple[str, Optional[int]]]) -> dict:
    """Slot (damaged label, score) pairs into STR..CHA by classified label.
    First value per ability wins; OCR row-bucket flips can reorder rows, so
    document order is NOT enforced."""
    scores: dict[str, Optional[int]] = {a: None for a in _ABILITY_ORDER}
    for label, val in pairs:
        k = _classify_ability_label(label)
        if k is not None and scores[k] is None:
            scores[k] = val
    return scores


def _fix_dice(s: str) -> str:
    """Repair dice notation: 'Zd6'->'2d6', '20d10 + a0'->'…+ 40', 'ld8'->'1d8'."""
    s = _fix_ocr_digits(s)
    s = re.sub(r"\bZ(?=d\d)", "2", s)
    s = re.sub(r"(?<=[\s+(])a(?=\d)", "4", s)
    s = re.sub(r"(?<=\d)[oO]", "0", s)
    s = re.sub(r"(?<=\d)[lrIJ]\b", "1", s)
    return s

_MON_LINE_FIELDS = {
    "skills": "Skills", "resistances": "Resistances",
    "immunities": "Immunities", "vulnerabilities": "Vulnerabilities",
    "senses": "Senses", "languages": "Languages", "gear": "Gear",
}
# \s* not \s+: OCR glues labels to values ('SkillsPerception +11').
_MON_LINE_RX = {k: re.compile(r"(?i)^\s*" + _sp_fuzzy(lbl) + r"\s*([^\n]+)", re.M)
                for k, lbl in _MON_LINE_FIELDS.items()}

_MON_SECTIONS = ("Traits", "Actions", "Bonus Actions", "Reactions",
                 "Legendary Actions")
_MON_SIZES = ("Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan")
_MON_TYPES = ("Aberration", "Beast", "Celestial", "Construct", "Dragon",
              "Elemental", "Fey", "Fiend", "Giant", "Humanoid", "Monstrosity",
              "Ooze", "Plant", "Undead")


def _fuzzy_pick(raw: str, options: tuple, cutoff: float = 0.7) -> Optional[str]:
    import difflib
    key = _mask_confusables(raw)
    best, score = None, 0.0
    for o in options:
        r = difflib.SequenceMatcher(None, key, _mask_confusables(o)).ratio()
        if r > score:
            best, score = o, r
    return best if score >= cutoff else None


def _parse_speed(raw: str) -> dict:
    # Repair digit glyphs only where they touch real digits ('1o'->'10',
    # 'l5'->'15') so mode words like 'Fly' survive.
    raw = re.sub(r"(?<=\d)[oOlrIJ\]]", lambda m: _MON_DIGITS.get(m.group(0), "1"),
                 raw)
    raw = re.sub(r"[lrIJ\]](?=\d)", "1", raw)
    out: dict[str, str] = {}
    for m in re.finditer(r"(?i)\b(fly|swim|climb|burrow)?\s*(\d{1,3})\s*[f1lt]t",
                         raw):
        mode = (m.group(1) or "walk").lower()
        out.setdefault(mode, f"{m.group(2)} ft.")
    return out


def _parse_mon_sections(body: str) -> dict[str, list[dict]]:
    """Split the post-CR text into Traits/Actions/... entry lists."""
    import difflib
    # Section headers are short caps-ish lines ('TRnrrs', 'AcrroNs').
    marks: list[tuple[int, int, str]] = []
    for m in re.finditer(r"^\s*([A-Za-z][A-Za-z .,'’]{3,22})\s*$", body, re.M):
        sec = _fuzzy_pick(m.group(1), _MON_SECTIONS, cutoff=0.75)
        if sec:
            marks.append((m.start(), m.end(), sec))
    out: dict[str, list[dict]] = {}
    for i, (s, e, sec) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else min(len(body), s + 6000)
        chunk = body[e:end]
        entries = []
        # 'Bold Name. Description...' entries (OCR may drop the space after
        # the period, so a following capital also counts as the boundary).
        parts = re.split(r"^([A-Z][A-Za-z'’ ()/\-]{2,60}?[.!])(?=\s|[A-Z])",
                         chunk, flags=re.M)
        for j in range(1, len(parts) - 1, 2):
            nm = parts[j].rstrip(".!").strip()
            desc = re.sub(r"\s+", " ", _fix_ocr_digits(parts[j + 1])).strip()[:1500]
            entries.append({"name": nm, "desc": desc})
        if entries:
            out[sec] = entries
    return out


_MM_TOC_ROW = re.compile(
    r"^\s*([A-Z][A-Za-z0-9'’,()\- ]{2,44}?)\s*\.{3,}\s*([0-9lJO]{1,3})\s*$",
    re.M)


def _mm_contents(text: str, canon_names: list[str]) -> list[tuple[str, int]]:
    """(monster name, book page) pairs from the MM's front contents list —
    the one part of the book set in body font, so names survive extraction.
    Names are cleaned (digit-for-letter glyphs) and canonicalized against the
    SRD list where possible."""
    import difflib
    canon_by_key = {re.sub(r"[^a-z]", "", n.lower()): n for n in canon_names}
    keys = list(canon_by_key)

    def clean(raw: str) -> tuple[str, str]:
        name = re.sub(r"(?<=[A-Za-z])1(?=[a-zA-Z])", "l", raw)
        name = re.sub(r"(?<=[A-Za-z])0(?=[a-zA-Z])", "o", name)
        name = re.sub(r"\b4(?=[a-z])", "A", name)
        name = re.sub(r"\b8(?=[a-z])", "B", name)
        name = " ".join(name.split())
        key = re.sub(r"[^a-z]", "", name.lower())
        hit = difflib.get_close_matches(key, keys, n=1, cutoff=0.85)
        return (canon_by_key[hit[0]] if hit else _repair_caps_name(name)), key

    out: list[tuple[str, int]] = []
    # pypdf-style rows: 'Aboleth....... 12' on one line.
    for m in _MM_TOC_ROW.finditer(text):
        page = _mon_int(m.group(2))
        if page is not None:
            out.append((clean(m.group(1))[0], page))
    # OCR-style rows split over two lines in the front matter:
    # 'AarakocraAeromancer...' / '..10' (dots even inside the number).
    front = text[:60000].splitlines()
    for a, b in zip(front, front[1:]):
        nm = re.match(r"^([A-Z][A-Za-z'’ \-]{2,40}?)[. ]*$", a.strip())
        pg = re.match(r"^[. ]*([\d.]{1,6})$", b.strip())
        if not (nm and pg):
            continue
        digits = re.sub(r"\D", "", pg.group(1))
        if not digits or len(digits) > 3:
            continue
        name, key = clean(nm.group(1))
        if len(key) >= 4:
            out.append((name, int(digits)))
    # The contents block appears once; drop echoes of the same (name, page).
    return list(dict.fromkeys(out))


def parse_mm_monsters(text: str, canon_names: list[str]) -> list[dict]:
    import difflib
    out: list[dict] = []
    anchors = list(_MON_AC.finditer(text))

    # --- page-based name assignment ---------------------------------------
    contents = _mm_contents(text, canon_names)
    by_page: dict[int, list[str]] = {}
    for nm, pg in contents:
        by_page.setdefault(pg, []).append(nm)

    ff_positions = [m.start() for m in re.finditer("\f", text)]
    import bisect

    def render_page(pos: int) -> int:
        return bisect.bisect_left(ff_positions, pos) + 1

    def raw_name_of(am) -> str:
        pre = [l for l in text[max(0, am.start() - 300):am.start()]
               .splitlines() if l.strip()]
        return pre[-2].strip() if len(pre) >= 2 else ""

    # Calibrate render-page -> book-page offset from blocks whose damaged
    # name still fuzzy-matches a contents name outright.
    contents_page = {nm: pg for nm, pg in contents}
    offsets: list[int] = []
    for am in anchors:
        key = _mask_confusables(raw_name_of(am))
        if not key:
            continue
        for nm, pg in contents:
            if difflib.SequenceMatcher(
                    None, key, _mask_confusables(nm)).ratio() >= 0.85:
                offsets.append(render_page(am.start()) - pg)
                break
    from collections import Counter
    offset = Counter(offsets).most_common(1)[0][0] if offsets else 0

    # Best-score unique assignment of contents names to anchors on that page.
    cands: list[tuple[float, int, str]] = []
    for idx, am in enumerate(anchors):
        page = render_page(am.start()) - offset
        key = _mask_confusables(raw_name_of(am))
        # Family sections span several pages after their contents entry.
        pool = {nm for d in range(-1, 5) for nm in by_page.get(page - d, [])}
        for nm in pool:
            r = difflib.SequenceMatcher(None, key, _mask_confusables(nm)).ratio()
            if r >= 0.45:
                cands.append((r, idx, nm))
    assigned_name: dict[int, str] = {}
    used_names: set[str] = set()
    for r, idx, nm in sorted(cands, reverse=True):
        if idx in assigned_name or nm in used_names:
            continue
        assigned_name[idx] = nm
        used_names.add(nm)
    # A block whose name line was destroyed outright still gets named when
    # its page window holds exactly one unclaimed contents entry.
    for idx, am in enumerate(anchors):
        if idx in assigned_name:
            continue
        page = render_page(am.start()) - offset
        pool = {nm for d in range(0, 3) for nm in by_page.get(page - d, [])} \
            - used_names
        if len(pool) == 1:
            nm = pool.pop()
            assigned_name[idx] = nm
            used_names.add(nm)
    # -----------------------------------------------------------------------

    for i, am in enumerate(anchors):
        # Name + size/type/alignment are the two non-empty lines above AC.
        pre_lines = [l for l in text[max(0, am.start() - 300):am.start()]
                     .splitlines() if l.strip()]
        if len(pre_lines) < 2:
            continue
        size_line = pre_lines[-1].strip()
        raw_name = pre_lines[-2].strip()
        # OCR drops word spaces ('MediumorSmallHumanoid'): substring match on
        # the collapsed line, fuzzy per-word as fallback for glyph damage.
        lkey = re.sub(r"[^a-z]", "", size_line.lower())
        size = next((s for s in _MON_SIZES if s.lower() in lkey), None) \
            or _fuzzy_pick(size_line.split()[0] if size_line.split() else "",
                           _MON_SIZES)
        mtype = next((t for t in _MON_TYPES if t.lower() in lkey), None)
        if mtype is None:
            words = re.findall(r"[A-Za-z']+", size_line)
            mtype = next((t for w in words
                          for t in [_fuzzy_pick(w, _MON_TYPES, 0.8)] if t), None)
        if size is None and mtype is None:
            continue  # AC line that isn't a stat block
        alignment = None
        al = re.search(r",\s*([^,\n]+)$", size_line)
        if al:
            alignment = _repair_caps_name(al.group(1).strip())

        block_end = anchors[i + 1].start() - 300 if i + 1 < len(anchors) \
            else len(text)
        body = text[am.start():max(block_end, am.start() + 800)]

        # Canonical name: page-based contents assignment; else fuzzy vs SRD;
        # else wordlist-repaired extraction.
        key = _mask_confusables(raw_name)
        name = assigned_name.get(i)
        matched = name is not None
        if name is None:
            match, score = None, 0.0
            for cn in canon_names:
                r = difflib.SequenceMatcher(
                    None, key, _mask_confusables(cn)).ratio()
                if r > score:
                    match, score = cn, r
            matched = score >= 0.8
            name = match if matched else _repair_caps_name(raw_name)
        if len(re.sub(r"[^A-Za-z]", "", name)) < 3:
            continue

        hp = _MON_HP.search(body[:400])
        speed = _MON_SPEED.search(body[:500])
        cr = _MON_CR.search(body)
        table = body[:cr.start()] if cr else body[:900]
        pairs = [(t[0], _mon_int(t[1])) for t in _MON_TRIPLE.findall(table)][:8]
        abil_scores = _align_abilities(pairs)
        if not all(abil_scores.values()):
            # OCR can sever a cell's save bonus ('CHA5-3' alone): fall back
            # to label+score+one modifier for the slots still empty.
            doubles = [(t[0], _mon_int(t[1])) for t in _MON_DOUBLE.findall(table)]
            for k, v in _align_abilities(doubles).items():
                abil_scores[k] = abil_scores[k] or v
        cr_val = None
        if cr:
            frac = cr.group(1)
            if "/" in frac:
                a, b = frac.split("/")
                cr_val = ((_mon_int(a) or 0) / (_mon_int(b) or 1))
            else:
                cr_val = float(_mon_int(frac) or 0)
        fields = {k: (re.sub(r"\s+", " ", rx.search(body).group(1)).strip()
                      if rx.search(body) else None)
                  for k, rx in _MON_LINE_RX.items()}
        sections = _parse_mon_sections(body[cr.end():] if cr else body)

        abil = {"strength": abil_scores["str"], "dexterity": abil_scores["dex"],
                "constitution": abil_scores["con"],
                "intelligence": abil_scores["int"],
                "wisdom": abil_scores["wis"], "charisma": abil_scores["cha"]}
        out.append({
            "slug": _slugify(name), "name": name,
            "size": size, "type": (mtype or "").lower() or None,
            "alignment": alignment,
            "armor_class": _mon_int(am.group(1)),
            "hit_points": _mon_int(hp.group(1).replace(",", "")) if hp else None,
            "hit_points_roll": (_fix_dice(hp.group(2)).replace(" ", "")
                                if hp else None),
            "speed": _parse_speed(speed.group(1)) if speed else None,
            **abil,
            "challenge_rating": cr_val,
            "xp": _mon_int(cr.group(2).replace(",", "")) if cr else None,
            "proficiency_bonus": _mon_int(cr.group(3)) if cr else None,
            "languages": fields["languages"],
            "senses": fields["senses"],
            "damage_resistances": fields["resistances"],
            "damage_immunities": fields["immunities"],
            "damage_vulnerabilities": fields["vulnerabilities"],
            "skills": fields["skills"],
            "special_abilities": sections.get("Traits"),
            "actions": (sections.get("Actions") or [])
                + [dict(e, name=f"Bonus Action: {e['name']}")
                   for e in sections.get("Bonus Actions", [])]
                + [dict(e, name=f"Reaction: {e['name']}")
                   for e in sections.get("Reactions", [])],
            "legendary_actions": sections.get("Legendary Actions"),
            "matched_srd": matched,
        })
    # Dedupe by slug: keep the block with the most actions/traits.
    seen: dict[str, dict] = {}
    for mo in out:
        richness = len(mo.get("actions") or []) + len(mo.get("special_abilities") or [])
        prev = seen.get(mo["slug"])
        if prev is None or richness > (len(prev.get("actions") or [])
                                       + len(prev.get("special_abilities") or [])):
            seen[mo["slug"]] = mo
    return list(seen.values())


def ingest_monsters(engine=None, database_url=None,
                    workspace: Path = WORKSPACE) -> dict:
    from .models import Monster
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    mm = next(iter(workspace.glob("*monster-manual-2024*.txt")), None)
    if mm is None:
        return {"error": "MM 2024 extraction not found"}
    text = mm.read_text(encoding="utf-8")
    with Session(engine) as s:
        rows = s.exec(select(Monster.name, Monster.index_slug)).all()
    canon_names = [n for n, _ in rows]
    slug_by_key = {_collapse_key(n): slug for n, slug in rows}
    monsters = parse_mm_monsters(text, canon_names)
    result = {"monsters_parsed": len(monsters),
              "srd_matched": sum(1 for m in monsters if m["matched_srd"]),
              "new": 0, "updated": 0}
    with Session(engine) as s:
        for mo in monsters:
            slug = slug_by_key.get(_collapse_key(mo["name"])) or mo["slug"]
            proficiencies = None
            if mo.get("skills"):
                proficiencies = [{"skill": mo["skills"]}]
            mapped = Monster(
                index_slug=slug, name=mo["name"], size=mo["size"],
                type=mo["type"], alignment=mo["alignment"],
                armor_class=mo["armor_class"], hit_points=mo["hit_points"],
                hit_points_roll=mo["hit_points_roll"],
                strength=mo["strength"], dexterity=mo["dexterity"],
                constitution=mo["constitution"], intelligence=mo["intelligence"],
                wisdom=mo["wisdom"], charisma=mo["charisma"],
                challenge_rating=mo["challenge_rating"], xp=mo["xp"],
                proficiency_bonus=mo["proficiency_bonus"],
                languages=mo["languages"], speed=mo["speed"],
                senses={"raw": mo["senses"]} if mo["senses"] else None,
                proficiencies=proficiencies,
                damage_resistances=([mo["damage_resistances"]]
                                    if mo["damage_resistances"] else None),
                damage_immunities=([mo["damage_immunities"]]
                                   if mo["damage_immunities"] else None),
                damage_vulnerabilities=([mo["damage_vulnerabilities"]]
                                        if mo["damage_vulnerabilities"] else None),
                special_abilities=mo["special_abilities"],
                actions=mo["actions"] or None,
                legendary_actions=mo["legendary_actions"],
                source="Owned (MM 2024) — local ingest",
            )
            if _upsert(s, Monster, slug, mapped):
                result["new"] += 1
            else:
                result["updated"] += 1
        s.commit()
    return result


# ===========================================================================
# Parser: 2014-format stat blocks (Bigby's; reusable for Volo's/VRGtR later)
# ===========================================================================
# Format: 'Armor Class 16 (natural armor)' / 'Hit Points 84 (8d10+40)' /
# ability table as a labels row then a values row / 'Challenge 7 (2,900 XP)'.

_B14_AC = re.compile(r"(?i)^\s*Armor ?Class\s*(\d{1,2})\s*(\(([^)]*)\))?", re.M)
_B14_HP = re.compile(r"(?i)^\s*Hit ?Points?\s*(\d{1,4})\s*\(([^)]+)\)", re.M)
_B14_SPEED = re.compile(r"(?i)^\s*Speed\s*([^\n]+)", re.M)
_B14_CR = re.compile(
    r"(?i)^\s*Challenge\s*(\d{1,2}(?:/\d)?)\s*\(([\d,]+)\s*XP\)", re.M)
_B14_PB = re.compile(r"(?i)^\s*Proficiency ?Bonus\s*\+?\s*(\d)", re.M)
# 'N (+M)' ability cells; both halves tolerate letter-digit glyphs ('+O').
_B14_SCORE = re.compile(
    r"\b(\d[\dOlJIrs]?|[OlJIrs]\d)\s*\(\s*([+-]\s*[\dOlJIrs]{1,2})\s*\)")
_B14_LINE_FIELDS = {
    "skills": "Skills", "senses": "Senses", "languages": "Languages",
    "resistances": "Damage Resistances", "immunities": "Damage Immunities",
    "vulnerabilities": "Damage Vulnerabilities",
    "condition_immunities": "Condition Immunities",
    "saving_throws": "Saving Throws",
}
_B14_LINE_RX = {k: re.compile(r"(?i)^\s*" + _sp_fuzzy(lbl) + r"\s+([^\n]+)", re.M)
                for k, lbl in _B14_LINE_FIELDS.items()}
_B14_SECTIONS = ("Actions", "Bonus Actions", "Reactions", "Legendary Actions",
                 "Lair Actions")


def parse_2014_statblocks(text: str, source_book: str) -> list[dict]:
    out: list[dict] = []
    anchors = list(_B14_AC.finditer(text))
    for i, am in enumerate(anchors):
        pre_lines = [l for l in text[max(0, am.start() - 300):am.start()]
                     .splitlines() if l.strip()]
        if len(pre_lines) < 2:
            continue
        size_line = pre_lines[-1].strip()
        raw_name = pre_lines[-2].strip()
        # OCR drops word spaces ('MediumOoze,Unaligned'): substring match on
        # the collapsed line, fuzzy per-word as fallback for glyph damage.
        lkey = re.sub(r"[^a-z]", "", size_line.lower())
        size = next((s for s in _MON_SIZES if s.lower() in lkey), None) \
            or _fuzzy_pick(size_line.split()[0] if size_line.split() else "",
                           _MON_SIZES)
        mtype = next((t for t in _MON_TYPES if t.lower() in lkey), None)
        if mtype is None:
            words = re.findall(r"[A-Za-z']+", size_line)
            mtype = next((t for w in words
                          for t in [_fuzzy_pick(w, _MON_TYPES, 0.8)] if t), None)
        if size is None and mtype is None:
            continue
        alignment = None
        al = re.search(r",\s*(?:typically\s*)?([^,\n]+)$", size_line, re.I)
        if al:
            alignment = _repair_caps_name(
                re.sub(r"(?i)^typically", "", al.group(1)).strip())
        name = _repair_caps_name(raw_name)
        if len(re.sub(r"[^A-Za-z]", "", name)) < 3:
            continue

        block_end = anchors[i + 1].start() - 300 if i + 1 < len(anchors) \
            else len(text)
        body = text[am.start():max(block_end, am.start() + 800)]

        hp = _B14_HP.search(body[:400])
        speed = _B14_SPEED.search(body[:500])
        cr = _B14_CR.search(body)
        pb = _B14_PB.search(body)
        # Ability scores: six 'N (+M)' cells, paired positionally. Column
        # interleave can push trailing cells past the Challenge line, so the
        # window extends a little beyond it (dice rolls can't false-match:
        # their parens hold dice, not a signed modifier).
        head = body[:cr.end() + 600 if cr else 900]
        cells = [_mon_int(m.group(1)) for m in _B14_SCORE.finditer(head)][:6]
        abil = dict(zip(("strength", "dexterity", "constitution",
                         "intelligence", "wisdom", "charisma"),
                        cells + [None] * (6 - len(cells))))
        cr_val = None
        if cr:
            frac = cr.group(1)
            cr_val = (int(frac.split("/")[0]) / int(frac.split("/")[1])
                      if "/" in frac else float(frac))
        fields = {k: (re.sub(r"\s+", " ", rx.search(body).group(1)).strip()
                      if rx.search(body) else None)
                  for k, rx in _B14_LINE_RX.items()}

        # Sections: caps-ish header lines; traits live between CR and ACTIONS.
        sec_marks: list[tuple[int, int, str]] = []
        sec_zone = body[cr.end():] if cr else body
        for m in re.finditer(r"^\s*([A-Za-z][A-Za-z ]{3,22})\s*$", sec_zone, re.M):
            sec = _fuzzy_pick(m.group(1), _B14_SECTIONS, cutoff=0.85)
            if sec:
                sec_marks.append((m.start(), m.end(), sec))
        sections: dict[str, str] = {}
        traits_txt = sec_zone[:sec_marks[0][0]] if sec_marks else sec_zone[:4000]
        for j, (s, e, sec) in enumerate(sec_marks):
            end = sec_marks[j + 1][0] if j + 1 < len(sec_marks) \
                else min(len(sec_zone), s + 6000)
            sections[sec] = sec_zone[e:end]

        def entries(chunk: Optional[str]) -> Optional[list]:
            if not chunk:
                return None
            parts = re.split(r"^([A-Z][A-Za-z'’ ()/\-]{2,60}?[.!])(?=\s|[A-Z])",
                             chunk, flags=re.M)
            es = [{"name": parts[j].rstrip(".!").strip(),
                   "desc": re.sub(r"\s+", " ",
                                  _fix_dice(parts[j + 1])).strip()[:1500]}
                  for j in range(1, len(parts) - 1, 2)]
            return es or None

        out.append({
            "slug": _slugify(name), "name": name, "size": size,
            "type": (mtype or "").lower() or None, "alignment": alignment,
            "armor_class": _mon_int(am.group(1)), "ac_desc": am.group(3),
            "hit_points": _mon_int(hp.group(1)) if hp else None,
            "hit_points_roll": (_fix_dice(hp.group(2)).replace(" ", "")
                                if hp else None),
            "speed": _parse_speed(speed.group(1)) if speed else None,
            **abil,
            "challenge_rating": cr_val,
            "xp": _mon_int(cr.group(2).replace(",", "")) if cr else None,
            "proficiency_bonus": _mon_int(pb.group(1)) if pb else None,
            "languages": fields["languages"], "senses": fields["senses"],
            "damage_resistances": fields["resistances"],
            "damage_immunities": fields["immunities"],
            "damage_vulnerabilities": fields["vulnerabilities"],
            "condition_immunities": fields["condition_immunities"],
            "skills": fields["skills"],
            "special_abilities": entries(traits_txt),
            "actions": (entries(sections.get("Actions")) or [])
                + [dict(e, name=f"Bonus Action: {e['name']}")
                   for e in entries(sections.get("Bonus Actions")) or []]
                + [dict(e, name=f"Reaction: {e['name']}")
                   for e in entries(sections.get("Reactions")) or []],
            "legendary_actions": entries(sections.get("Legendary Actions")),
            "source": source_book,
        })
    # Dedupe by slug, keep the richest block.
    seen: dict[str, dict] = {}
    for mo in out:
        rich = len(mo.get("actions") or []) + len(mo.get("special_abilities") or [])
        prev = seen.get(mo["slug"])
        if prev is None or rich > (len(prev.get("actions") or [])
                                   + len(prev.get("special_abilities") or [])):
            seen[mo["slug"]] = mo
    return list(seen.values())


def ingest_2014_monsters(glob_pat: str, source_book: str, engine=None,
                         database_url=None, workspace: Path = WORKSPACE) -> dict:
    """Ingest any 2014-format book's stat blocks (Bigby's, Volo's, …)."""
    from .models import Monster
    engine = engine or get_engine(database_url)
    SQLModel.metadata.create_all(engine)
    bb = next(iter(workspace.glob(glob_pat)), None)
    if bb is None:
        return {"error": f"extraction not found: {glob_pat}"}
    monsters = parse_2014_statblocks(bb.read_text(encoding="utf-8"), source_book)
    result = {"monsters_parsed": len(monsters), "new": 0, "updated": 0}
    with Session(engine) as s:
        slug_by_key = {_collapse_key(n): slug for n, slug in
                       s.exec(select(Monster.name, Monster.index_slug))}
        for mo in monsters:
            slug = slug_by_key.get(_collapse_key(mo["name"])) or mo["slug"]
            mapped = Monster(
                index_slug=slug, name=mo["name"], size=mo["size"],
                type=mo["type"], alignment=mo["alignment"],
                armor_class=mo["armor_class"], ac_desc=mo.get("ac_desc"),
                hit_points=mo["hit_points"],
                hit_points_roll=mo["hit_points_roll"],
                strength=mo["strength"], dexterity=mo["dexterity"],
                constitution=mo["constitution"],
                intelligence=mo["intelligence"], wisdom=mo["wisdom"],
                charisma=mo["charisma"], challenge_rating=mo["challenge_rating"],
                xp=mo["xp"], proficiency_bonus=mo["proficiency_bonus"],
                languages=mo["languages"], speed=mo["speed"],
                senses={"raw": mo["senses"]} if mo["senses"] else None,
                proficiencies=([{"skill": mo["skills"]}] if mo["skills"] else None),
                damage_resistances=([mo["damage_resistances"]]
                                    if mo["damage_resistances"] else None),
                damage_immunities=([mo["damage_immunities"]]
                                   if mo["damage_immunities"] else None),
                damage_vulnerabilities=([mo["damage_vulnerabilities"]]
                                        if mo["damage_vulnerabilities"] else None),
                condition_immunities=([mo["condition_immunities"]]
                                      if mo["condition_immunities"] else None),
                special_abilities=mo["special_abilities"],
                actions=mo["actions"] or None,
                legendary_actions=mo["legendary_actions"],
                source=mo["source"],
            )
            if _upsert(s, Monster, slug, mapped):
                result["new"] += 1
            else:
                result["updated"] += 1
        s.commit()
    return result


def main(argv: list[str]) -> None:
    only = None
    ocr_match = None
    extract_only = "--extract" in argv
    for a in argv:
        if a.startswith("--only="):
            only = a.split("=", 1)[1]
        if a.startswith("--ocr="):
            ocr_match = a.split("=", 1)[1]
    if ocr_match:
        for pdf in sorted(DEFAULT_LIBRARY.glob("*.pdf")):
            if ocr_match.lower() in pdf.name.lower():
                ocr_extract_pdf(pdf)
        return
    extract_pdfs(only=only)
    if not extract_only:
        print("[owned] feats:", ingest_feats())
        print("[owned] spells:", ingest_spells())
        print("[owned] subclasses:", ingest_subclasses())
        print("[owned] xgte subclasses:", ingest_xgte_subclasses())
        print("[owned] monsters:", ingest_monsters())
        print("[owned] bigby monsters:", ingest_2014_monsters(
            "*bigby*.txt", "Owned (Bigby's Glory of the Giants) — local ingest"))
        print("[owned] volo monsters:", ingest_2014_monsters(
            "*volo*.txt", "Owned (Volo's Guide to Monsters) — local ingest"))
        print("[owned] vrgtr monsters:", ingest_2014_monsters(
            "*van-richten*.txt",
            "Owned (Van Richten's Guide to Ravenloft) — local ingest"))


if __name__ == "__main__":
    main(sys.argv[1:])
