# The Oracle — Project Instructions

## Environment
- **Package manager**: `uv` (use `uv run` to execute scripts, `uv add` to add dependencies)
- **Python**: 3.12+
- **Config**: `pyproject.toml` (no `setup.py` or `requirements.txt` for deps)
- **OS**: Windows

## Project Structure
- `eight_card_system/` — Hex-based map rendering engine (terrain gen, renderer, hex math)
- `ai-dm-sicord-bot/` — Discord bot for AI dungeon master
- `oracle-dm-backend/` — FastAPI backend
- `reference/` — Reference implementations (town generator, map refs)

## Running
- Run demo: `uv run python -m eight_card_system.demo`
- Run backend: `uv run python oracle-dm-backend/fastapi-dm.py`
- Run bot: `uv run python ai-dm-sicord-bot/oracle-dm-discord-bot.py`

## Key Conventions
- Demo output goes to `eight_card_system/demo_output/`
- The rendering pipeline uses layered compositing (Layer 1a–1f)
- Hex coordinates use axial (q, r) system via `hex_math.py`
