"""The Oracle - one-click launcher.

Starts the systems required to play:
  1. The DM-brain backend (FastAPI / uvicorn)
  2. The Discord bot (which itself auto-starts/stops Lavalink for music)

Each service opens in its own console window. Closing this launcher window
(or pressing Ctrl+C) shuts every service back down.

This file is packaged into "The Oracle.exe" with PyInstaller. It uses only the
Python standard library so the packaged exe is fully self-contained; the actual
game systems run via the project's own virtual environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Where the project lives. Overridable with the ORACLE_HOME environment
# variable, but defaults to this machine's checkout so the desktop exe "just
# works" without any arguments.
PROJECT_ROOT = Path(os.environ.get("ORACLE_HOME", r"D:\Projects\The Oracle"))

VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
BACKEND_DIR = PROJECT_ROOT / "oracle-dm-backend"
BOT_DIR = PROJECT_ROOT / "ai-dm-sicord-bot"

BACKEND_SCRIPT = "fastapi-dm.py"
BOT_SCRIPT = "oracle-dm-discord-bot.py"

# Batch wrappers do the actual launching. Using a single, self-contained .bat
# per service avoids Windows nested-quoting problems (the project path contains
# a space) and keeps each console window open if that service crashes.
BACKEND_BAT = PROJECT_ROOT / "launcher" / "run_backend.bat"
BOT_BAT = PROJECT_ROOT / "launcher" / "run_bot.bat"

HEALTH_URL = "http://127.0.0.1:8000/"
HEALTH_TIMEOUT_SECONDS = 60

# Give each service its own titled console window on Windows.
CREATE_NEW_CONSOLE = 0x00000010


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _banner() -> None:
    print("=" * 60)
    print("            THE ORACLE  -  starting your world")
    print("=" * 60)
    print(f"  Project : {PROJECT_ROOT}")
    print()


def _fail(message: str) -> "int":
    print(f"\n[ERROR] {message}\n")
    input("Press Enter to close...")
    return 1


def _validate() -> "str | None":
    if not VENV_PYTHON.exists():
        return (
            f"Could not find the virtual environment Python at:\n    {VENV_PYTHON}\n"
            "Create it first (e.g. `uv sync`) or set ORACLE_HOME to the project folder."
        )
    if not (BACKEND_DIR / BACKEND_SCRIPT).exists():
        return f"Backend script missing: {BACKEND_DIR / BACKEND_SCRIPT}"
    if not (BOT_DIR / BOT_SCRIPT).exists():
        return f"Bot script missing: {BOT_DIR / BOT_SCRIPT}"
    if not BACKEND_BAT.exists():
        return f"Launcher batch file missing: {BACKEND_BAT}"
    if not BOT_BAT.exists():
        return f"Launcher batch file missing: {BOT_BAT}"
    return None


def _start(title: str, bat: Path) -> subprocess.Popen:
    print(f"[Oracle] launching {title} ...")
    # A single quoted path argument to `cmd /c` is unambiguous, unlike a full
    # inline command string, so paths with spaces work reliably.
    return subprocess.Popen(
        ["cmd", "/c", str(bat)],
        creationflags=CREATE_NEW_CONSOLE,
    )


def _wait_for_backend(timeout: int = HEALTH_TIMEOUT_SECONDS) -> bool:
    print("[Oracle] waiting for the DM brain to wake up ", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(HEALTH_URL, timeout=2) as resp:
                if 200 <= resp.status < 500:
                    print(" ready!")
                    return True
        except (URLError, OSError):
            pass
        print(".", end="", flush=True)
        time.sleep(1.5)
    print(" (timed out - continuing anyway)")
    return False


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    # /T also terminates children (e.g. Lavalink spawned by the bot).
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    _banner()

    problem = _validate()
    if problem:
        return _fail(problem)

    backend = _start("Backend (DM brain)", BACKEND_BAT)
    _wait_for_backend()

    bot = _start("Discord Bot + Music", BOT_BAT)

    print()
    print("-" * 60)
    print("  All systems launched. Two windows should now be open:")
    print("    * Oracle - Backend (DM brain)")
    print("    * Oracle - Discord Bot + Music")
    print()
    print("  Keep THIS window open while you play.")
    print("  Close it (or press Ctrl+C) to shut everything down.")
    print("-" * 60)

    services = [("Discord bot", bot), ("Backend", backend)]
    try:
        while True:
            time.sleep(1)
            if all(p.poll() is not None for _, p in services):
                print("\n[Oracle] all services have stopped.")
                break
    except KeyboardInterrupt:
        print("\n[Oracle] shutdown requested ...")
    finally:
        for name, proc in services:
            if proc.poll() is None:
                print(f"[Oracle] stopping {name} ...")
                _kill_tree(proc)

    print("[Oracle] goodbye.")
    time.sleep(1.5)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # never let the window vanish without a trace
        import traceback

        log = PROJECT_ROOT / "launcher" / "launcher_error.log"
        try:
            log.write_text(traceback.format_exc(), encoding="utf-8")
        except Exception:
            pass
        print("\n[FATAL] The launcher hit an unexpected error:\n")
        traceback.print_exc()
        print(f"\n(Saved to {log})")
        input("\nPress Enter to close...")
        sys.exit(1)
