# The Oracle — Project Instructions

## Environment
- **Package manager**: `uv` (use `uv run` to execute scripts, `uv add` to add dependencies)
- **Python**: 3.12+
- **Config**: `pyproject.toml` (no `setup.py` or `requirements.txt` for deps)
- **OS**: Windows

## Project Structure
- `eight_card_system/` — Persistent world **knowledge graph** (temporal entities/
  relations, event log, relevance-scoped BFS context). NOT hex maps (removed).
- `rules/` — SRD structured rules (monsters/spells) seeded from CC-BY-4.0 5e SRD.
- `dice/` — Internal dice roller + mechanics (checks, attacks, damage).
- `ai-dm-sicord-bot/` — Discord bot for the AI dungeon master
- `oracle-dm-backend/` — FastAPI backend ("DM brain"); shares `oracle.db`
- `reference/` — Reference implementations (town generator, map refs)

## Running
- Run backend: `uv run python oracle-dm-backend/fastapi-dm.py`
- Run bot: `uv run python ai-dm-sicord-bot/oracle-dm-discord-bot.py`
- World-graph demo: `uv run python -m eight_card_system.demo`
- Rules ingest/demo: `uv run python -m rules.demo` (network required)
- Dice demo: `uv run python -m dice.demo`

## Key Conventions
- World persistence = the append-only knowledge graph, not maps. The DM is only fed
  the relevant subgraph via `get_world_context`, never the whole world.
- Dice are rolled internally: the LLM emits `[[ROLL: expr | label | DC n]]` hooks
  that the backend resolves inline (`resolve_roll_hooks`); no Avrae copy-paste.
- D&D Beyond has NO public write API — the backend character DB is source of truth.
- Do NOT reintroduce hex/terrain-render code under `eight_card_system`.
