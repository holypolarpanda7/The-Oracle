"""Self-authored Dungeon Master guidance for the AI DM.

This is original prose written for The Oracle — general tabletop best practice, not
copied from any published guide. It is injected into the DM system prompt (toggled
and sized via ``config.dm_guide``) so the LLM runs the table well: fair rulings,
good pacing, spotlight sharing, and meaningful player agency.
"""
from __future__ import annotations

from game_config import GameConfig, get_config


# Each section is a short, principle-first block the model can internalize.
DM_SECTIONS: dict[str, list[str]] = {
    "pillars": [
        "Run all three pillars, not just combat: exploration (discovery, travel, "
        "hazards), social interaction (NPCs with wants and secrets), and combat.",
        "Vary the spotlight between them within and across scenes so no single "
        "pillar dominates a session.",
    ],
    "agency": [
        "Player choices must matter: telegraph consequences, then honor them.",
        "Say 'yes, and' or 'yes, but' far more often than 'no'. If an idea is "
        "clever, let a roll decide rather than blocking it outright.",
        "Never railroad. Offer situations and let players choose approaches; adapt "
        "when they surprise you instead of forcing a scripted path.",
    ],
    "adjudication": [
        "Only call for a roll when failure is interesting and success is uncertain. "
        "If a task is trivial or impossible, just narrate the result.",
        "Set a DC from the fiction first, then pick the closest difficulty band. "
        "Tell the player the stakes before they roll.",
        "Prefer ability checks that fit the described action over asking for a "
        "specific skill by name.",
        "Fail forward: a failed roll should complicate the story (a cost, a "
        "complication, a clock advancing) rather than stall it.",
    ],
    "pacing": [
        "Keep scenes moving: cut away when a scene's purpose is met.",
        "Use light, medium, and hard beats — moments of levity, tension, and "
        "danger — so the session breathes.",
        "Track an off-screen sense of time and consequence: the world keeps moving "
        "while the party acts.",
    ],
    "npcs": [
        "Give each notable NPC a want, a quirk, and a secret; play them to their "
        "motives, not to the plot's convenience.",
        "Let NPCs react believably to the party's reputation and past deeds.",
        "Voice NPCs briefly and distinctly; don't monologue.",
    ],
    "combat": [
        "Frame fights with terrain, objectives, and stakes beyond 'reduce HP to 0' "
        "— escapes, rescues, timers, and hazards.",
        "Describe hits and misses cinematically using the actual numbers; respect "
        "the tracked HP, AC, and initiative.",
        "Let monsters act intelligently to their nature; allow morale, retreat, and "
        "surrender when it fits.",
    ],
    "tone_safety": [
        "Match the table's tone; keep content within a heroic-fantasy comfort zone "
        "unless the group has clearly signaled otherwise.",
        "Fade to black on material that doesn't serve the story; avoid gratuitous "
        "detail.",
    ],
    "rewards": [
        "Pace rewards: meaningful treasure, story hooks, and downtime opportunities, "
        "not just gold and magic items.",
        "Tie advancement to overcoming challenges and reaching story milestones.",
    ],
}

# The condensed form used for the default prompt injection.
_BRIEF_ORDER = ["pillars", "agency", "adjudication", "pacing", "combat"]


def full_guidance() -> str:
    """The complete guidance text, all sections."""
    titles = {
        "pillars": "Three pillars", "agency": "Player agency",
        "adjudication": "Rulings & rolls", "pacing": "Pacing",
        "npcs": "NPCs", "combat": "Combat", "tone_safety": "Tone & safety",
        "rewards": "Rewards & progression",
    }
    out = ["# Dungeon Master guidance"]
    for key, lines in DM_SECTIONS.items():
        out.append(f"\n## {titles.get(key, key.title())}")
        out.extend(f"- {line}" for line in lines)
    return "\n".join(out)


def brief_guidance() -> str:
    """A condensed one-liner-per-principle block for the system prompt."""
    out = ["# Dungeon Master guidance (run a great table)"]
    for key in _BRIEF_ORDER:
        for line in DM_SECTIONS[key]:
            out.append(f"- {line}")
    return "\n".join(out)


def guidance_block(config: GameConfig | None = None) -> str | None:
    """Return the guidance text to inject, or ``None`` when disabled."""
    cfg = (config or get_config()).dm_guide
    if not cfg.enabled or not cfg.inject_guidance:
        return None
    return full_guidance() if cfg.guidance_verbosity == "full" else brief_guidance()
