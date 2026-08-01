"""Cultural hands: the right face for the right culture, and no face at all
for anything we cannot place (which must stay the house serif)."""
import importlib.util, os, sys, tempfile
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))
db = os.path.join(tempfile.gettempdir(), "oracle_scripts_check.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = f"sqlite:///{db}"
spec = importlib.util.spec_from_file_location(
    "fastapi_dm", str(ROOT / "oracle-dm-backend" / "fastapi-dm.py"))
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)

fails = []

# Species -> hand, including the longest-match rule that keeps "half-elf" elven
# and stops "giant" swallowing unrelated names.
cases = {
    "Elf": "elven", "High Elf": "elven", "Half-Elf": "elven", "Drow": "elven",
    "Shadar-kai": "elven", "Dwarf": "dwarven", "Duergar": "dwarven",
    "Goliath": "dwarven", "Firbolg": "dwarven",
    "Dragonborn": "draconic", "Kobold": "draconic", "Lizardfolk": "draconic",
    "Tiefling": "infernal", "Hexblood": "infernal",
    "Gnome": "fey", "Halfling": "fey", "Satyr": "fey", "Harengon": "fey",
    "Aasimar": "celestial", "Kalashtar": "celestial",
    # Unplaceable -> the house serif, never a wrong face.
    "Human": None, "Warforged": None, "": None, None: None,
}
for species, want in cases.items():
    got = m._script_for_species(species)
    if got != want:
        fails.append(f"{species!r} -> {got!r}, wanted {want!r}")
print("species mapping checked:", len(cases))

# Power family -> hand. Every family in the canon must resolve, or a god
# silently falls back to the serif and the feature looks broken.
from eight_card_system.pantheon import POWER_FAMILIES
for key in POWER_FAMILIES:
    if not m._script_for_power(key):
        fails.append(f"power family {key!r} has no hand")
print("power families checked:", len(POWER_FAMILIES))
if m._script_for_power("not_a_family") is not None:
    fails.append("an unknown family should have no hand")

# Only faces we actually ship.
SHIPPED = {"celestial", "dwarven", "elven", "draconic", "infernal", "fey"}
used = set(m._FAMILY_SCRIPT.values()) | set(m._SPECIES_SCRIPT.values())
missing = used - SHIPPED
if missing:
    fails.append(f"mapped to fonts that do not exist: {missing}")
fonts = ROOT / "activity-ui" / "public" / "assets" / "fonts"
for name in SHIPPED:
    if not (fonts / f"{name}.woff2").exists():
        fails.append(f"font file missing: {name}.woff2")
print("fonts on disk:", sorted(p.name for p in fonts.glob("*.woff2")))

# A deity entity resolves through its family; a mortal through their species.
class E:
    def __init__(self, t, a): self.type, self.attributes = t, a
from eight_card_system.models import EntityType
if m._script_for_entity(E(EntityType.DEITY, {"family": "archdevils"})) != "infernal":
    fails.append("a deity did not resolve through its family")
if m._script_for_entity(E(EntityType.NPC, {"race": "Dwarf"})) != "dwarven":
    fails.append("an NPC did not resolve through their species")
if m._script_for_entity(E(EntityType.NPC, {})) is not None:
    fails.append("a cultureless NPC should have no hand")
if m._script_for_entity(None) is not None:
    fails.append("None should be handled")

print("\nFAILS:", fails or "none")
sys.exit(1 if fails else 0)
