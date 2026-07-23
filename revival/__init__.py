"""Revival mechanics: death is reversible by magic.

Pure spell table + resolver (``revival/spells.py``) the backend's [[REVIVE]] hook
uses to un-death a fallen PC. Restores spell-appropriate HP, may leave a fading
penalty, and honors a DNR wish — a willing-soul spell fails against a DNR (the soul
refuses), while a no-consent spell forces the PC back, furious at the reviver. The
DB/world state changes (control handoff, retreat to bastion/town) live in the backend.
"""
from .spells import (
    REVIVAL_SPELLS,
    RevivalPlan,
    SpellSpec,
    get_spell,
    resolve,
)

__all__ = ["REVIVAL_SPELLS", "RevivalPlan", "SpellSpec", "get_spell", "resolve"]
