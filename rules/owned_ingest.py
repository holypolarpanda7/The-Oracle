"""
LOCAL-ONLY ingestion of the user's owned book PDFs (see CLAUDE.md policy).

This module carries TOOLING ONLY — no book content lives in the repo. It reads
PDFs from the user's private library, extracts text into a gitignored workspace
(``owned_books/``), and parses mechanics into the gitignored ``oracle.db``.
The campaign is free; the data never leaves the user's machine.

    uv run python -m rules.owned_ingest            # extract all + parse feats
    uv run python -m rules.owned_ingest --extract  # extraction only

Currently parsed:
  - Player's Handbook 2024 feats -> ``rules_feat`` (drives the CC wizard's
    Custom Lineage feat choice, so feat *prerequisites* are enforced in code).

More parsers (subclasses, spells, monsters) hang off the same workspace.
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
                if f.readline() == marker:
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


def main(argv: list[str]) -> None:
    only = None
    extract_only = "--extract" in argv
    for a in argv:
        if a.startswith("--only="):
            only = a.split("=", 1)[1]
    extract_pdfs(only=only)
    if not extract_only:
        print("[owned] feats:", ingest_feats())


if __name__ == "__main__":
    main(sys.argv[1:])
