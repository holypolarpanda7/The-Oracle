"""Prove speaker attribution splits dialogue correctly and stays conservative."""
import importlib.util, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_speech_check.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

NAMES = ["Old Marla", "Marla", "Sable", "Garrick Vane"]
fails = []

def seg(text, rolls=None):
    return m._activity_segments(text, list(rolls or []), NAMES)

# 1. Attributed dialogue becomes its own speech block.
out = seg('The mill groans in the wind.\n\n'
          '"Millers grind no grain at midnight," Old Marla says, "and the ones '
          'that do aren\'t millers."\n\n'
          'You look back at the tracks.')
kinds = [(e["t"], e.get("who")) for e in out]
print("1:", kinds)
if kinds != [("narration", None), ("speech", "Old Marla"), ("narration", None)]:
    fails.append(f"basic attribution wrong: {kinds}")

# 2. Longest name wins — not the substring.
out = seg('"Aye," Old Marla mutters.')
if out[0].get("who") != "Old Marla":
    fails.append(f"substring name beat the longer one: {out[0].get('who')}")

# 3. A bare quoted line continues the current speaker.
out = seg('"Aye," Old Marla mutters.\n\n"Then we go in."')
whos = [e.get("who") for e in out]
print("3:", whos)
if whos != ["Old Marla", "Old Marla"]:
    fails.append(f"continuation failed: {whos}")

# 4. Prose that merely QUOTES something stays narration.
out = seg('The sign above the door read "Wispering Mill" in flaking paint, and '
          'below it someone had scratched a warning no one had bothered to '
          'scrub away in all the years since.')
print("4:", [e["t"] for e in out])
if out[0]["t"] != "narration":
    fails.append("a quoting prose paragraph was mistaken for dialogue")

# 5. A name spoken INSIDE the quote does not attribute the line.
out = seg('"Garrick Vane is a coward," someone says from the dark.')
print("5:", out[0]["t"], repr(out[0].get("who")))
if out[0]["t"] != "speech" or out[0].get("who"):
    fails.append(f"attributed from inside the quote: {out[0].get('who')}")

# 6. Roll markers still split at the right place, around dialogue.
rolls = [{"marker": "@@R@@", "expr": "1d20+7", "total": 19}]
out = seg('You creep closer.@@R@@\n\n"Who\'s there?" Marla calls.', rolls)
print("6:", [(e["t"], e.get("who")) for e in out])
if [e["t"] for e in out] != ["narration", "roll", "speech"]:
    fails.append(f"roll interleaving broke: {[e['t'] for e in out]}")

# 7. With no known names, dialogue is still recognised, just unattributed.
out = m._activity_segments('"Who goes there?" the voice calls.', [], [])
print("7:", out[0]["t"], repr(out[0].get("who")))
if out[0]["t"] != "speech":
    fails.append("dialogue not recognised without a name pool")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
