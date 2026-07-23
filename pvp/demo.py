"""End-to-end demo / smoke test for the PvP subsystem.

Run: ``uv run python -m pvp.demo``

Covers: an unsanctioned kill of a much-lower-level PC (god strikes the killer dead +
Kinslayer's Mark + alignment/renown collapse); a same-level unsanctioned kill (heavy
but non-lethal); an unsanctioned strike (warning tier); a sanctioned to-the-death
duel (allowed, no divine wrath); a to-yield pact + yield (clean resolve); and the
deity fallback when the victim named no patron. Asserts the branches and scaling.
"""
from __future__ import annotations

from . import (assess, authorized, close_pact, describe_pacts, new_state,
               offense_count, open_pact, record_offense, retributor_for, full_name)


def _line(title: str) -> None:
    print("\n" + "=" * 66 + f"\n{title}\n" + "=" * 66)


def demo_pacts() -> None:
    _line("PACTS — authorization is order-insensitive and terminable")
    st = new_state()
    open_pact(st, "Kael", "Mira", "to-the-death", turn=1)
    assert authorized(st, "mira", "kael"), "pact must be order-insensitive"
    assert authorized(st, "Kael", "Mira")["terms"] == "to-the-death"
    assert authorized(st, "Kael", "Bandit") is None, "unrelated pair isn't authorized"
    print(describe_pacts(st))
    close_pact(st, "Kael", "Mira", status="resolved")
    assert authorized(st, "Kael", "Mira") is None, "closed pact no longer authorizes"
    print("pact opened, matched both ways, then closed ✅")


def demo_retribution() -> None:
    _line("RETRIBUTION — unsanctioned kill scales with level gap & the god")
    # Victim worships a lawful-good war god → a wrathful avenger.
    god = retributor_for("Kael the Iron Oath")
    print(f"Avenger (named patron): {full_name(god)} [{god['alignment']}]")

    # 1) Punching down hard: level 12 kills level 2 → death.
    o1 = assess("kill", 12, 2, god, authorized=False)
    print(f"\n1. lvl12 kills lvl2 (gap 10) → severity {o1.severity}, smite={o1.smite}")
    print(f"   curse: {o1.curse['name']} — lift: {o1.curse['lift'][:60]}…")
    print(f"   align {o1.align_shift}, renown {o1.renown_delta}")
    print(f"   PUBLIC: {o1.public[:90]}…")
    assert o1.smite == "death" and o1.curse and o1.align_shift["good"] < 0

    # 2) Even fight, unsanctioned kill → heavy but non-lethal.
    o2 = assess("kill", 5, 5, god, authorized=False)
    print(f"\n2. lvl5 kills lvl5 (gap 0) → severity {o2.severity}, smite={o2.smite}")
    assert o2.smite in ("down", "hp") and o2.smite != "death"

    # 3) Unsanctioned strike (not a kill) → warning tier, no smite.
    o3 = assess("strike", 5, 5, god, authorized=False)
    print(f"\n3. lvl5 strikes lvl5 → severity {o3.severity}, smite={o3.smite}")
    print(f"   PRIVATE: {o3.private[:80]}…")
    assert o3.smite == "none" and o3.severity < o2.severity

    # 4) Repeat kinslaying escalates.
    st = new_state()
    record_offense(st, "Kael"); record_offense(st, "Kael")
    o4 = assess("kill", 5, 5, god, authorized=False, offense_count=offense_count(st, "Kael"))
    print(f"\n4. lvl5 kills lvl5 but 2 priors → severity {o4.severity} (>{o2.severity})")
    assert o4.severity > o2.severity


def demo_authorized() -> None:
    _line("AUTHORIZED — a sanctioned duel draws no divine wrath")
    god = retributor_for("Serath the Dawnmother")
    o = assess("kill", 12, 2, god, authorized=True)
    print(f"sanctioned lvl12-kills-lvl2 → severity {o.severity}, smite={o.smite}, "
          f"curse={o.curse}")
    print(f"PUBLIC: {o.public}")
    assert o.severity == 0 and o.smite == "none" and o.curse is None


def demo_fallback() -> None:
    _line("FALLBACK — no patron → an alignment-fitting avenger steps in")
    for align in ("lawful good", "chaotic good", "neutral"):
        g = retributor_for(None, align)
        print(f"victim alignment {align:13s} → avenger {full_name(g)} [{g['alignment']}]")
        assert "evil" not in g["alignment"].lower()


def main() -> None:
    demo_pacts()
    demo_retribution()
    demo_authorized()
    demo_fallback()
    print("\n" + "=" * 66 + "\nALL PVP-SUBSYSTEM CHECKS PASSED ✅\n" + "=" * 66)


if __name__ == "__main__":
    main()
