"""The Oracle - one-click launcher.

Starts the systems required to play, together, and shuts them down together:
  1. Ollama local LLM (only when the backend is configured for local Ollama)
  2. ComfyUI image generation (optional - only if installed; the game runs
     fine without it, images are simply skipped)
  3. The DM-brain backend (FastAPI / uvicorn)
  4. The Discord bot (which itself auto-starts/stops Lavalink for music)

Each service opens in its own console window. Closing this launcher window
(or pressing Ctrl+C) shuts the game's services back down.

Ollama and ComfyUI are "adopt-or-manage": if one is ALREADY running when the
launcher starts (because you launched it independently for other work), the
launcher uses that instance and leaves it running on exit. Only instances the
launcher itself started are stopped when the game shuts down. The backend and
bot are always the game's own and always stopped.

This file is packaged into "The Oracle.exe" with PyInstaller. It uses only the
Python standard library so the packaged exe is fully self-contained; the actual
game systems run via the project's own virtual environment.
"""

from __future__ import annotations

import ctypes
import os
import re
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
COMFYUI_BAT = PROJECT_ROOT / "launcher" / "run_comfyui.bat"
OLLAMA_BAT = PROJECT_ROOT / "launcher" / "run_ollama.bat"

BACKEND_CRED_ENV = BACKEND_DIR / "backend-cred.env"

# ComfyUI (self-hosted image generation) lives outside the project. Its startup
# is optional: if the install or its venv is missing, the launcher simply skips
# it and the imagery layer runs offline (no images, game unaffected).
COMFYUI_HOME = Path(os.environ.get("COMFYUI_HOME", r"D:\ComfyUI"))
COMFYUI_PYTHON = COMFYUI_HOME / ".venv" / "Scripts" / "python.exe"
# Set ORACLE_START_COMFYUI=0 to always skip launching ComfyUI.
START_COMFYUI = os.environ.get("ORACLE_START_COMFYUI", "1").strip() not in {"0", "false", "no", ""}
# Set ORACLE_START_OLLAMA=0 to never launch Ollama (e.g. you always run it yourself).
START_OLLAMA = os.environ.get("ORACLE_START_OLLAMA", "1").strip() not in {"0", "false", "no", ""}

# Cloudflare tunnel — the public HTTPS front door so Discord can load the
# Activity iframe (it can't reach localhost). Quick tunnels get a fresh random
# URL each run, which the launcher prints for the Dev Portal URL mapping.
CLOUDFLARE_BAT = PROJECT_ROOT / "launcher" / "run_cloudflared.bat"
CLOUDFLARE_URL_LOG = PROJECT_ROOT / "launcher" / "cloudflared_url.log"
# Set ORACLE_START_CLOUDFLARE=0 to skip it (e.g. you run your own / a named tunnel).
START_CLOUDFLARE = os.environ.get("ORACLE_START_CLOUDFLARE", "1").strip() not in {"0", "false", "no", ""}
CLOUDFLARE_URL_TIMEOUT_SECONDS = 30
# For a named tunnel there's no URL to parse (the hostname is fixed), but we do
# wait for cloudflared to register an edge connection so we don't advertise a
# hostname that isn't actually serving yet (e.g. a config/cert failure).
CLOUDFLARE_CONNECT_TIMEOUT_SECONDS = 30
# Named tunnel = a stable hostname (set once in the Dev Portal). When
# ORACLE_TUNNEL_NAME is set, run_cloudflared.bat runs that tunnel and the URL is
# the fixed ORACLE_TUNNEL_HOSTNAME (no per-launch parsing). Empty = quick tunnel.
CLOUDFLARE_TUNNEL_NAME = os.environ.get("ORACLE_TUNNEL_NAME", "").strip()
CLOUDFLARE_TUNNEL_HOSTNAME = os.environ.get("ORACLE_TUNNEL_HOSTNAME", "").strip()

HEALTH_URL = "http://127.0.0.1:8000/"
HEALTH_TIMEOUT_SECONDS = 60

COMFYUI_HEALTH_URL = "http://127.0.0.1:8188/system_stats"
COMFYUI_TIMEOUT_SECONDS = 120

# Ollama root is derived from the backend's LLM_BASE_URL (see _ollama_root); this
# is only the fallback when the env file can't be read.
DEFAULT_OLLAMA_ROOT = "http://127.0.0.1:11434"
OLLAMA_TIMEOUT_SECONDS = 60

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


def _is_up(url: str, timeout: float = 2.0) -> bool:
    """True if an HTTP service answers at ``url`` (any non-5xx status)."""
    try:
        with urlopen(url, timeout=timeout) as resp:
            return 200 <= resp.status < 500
    except (URLError, OSError):
        return False


def _wait_until_up(
    label: str, url: str, timeout: int, *, ready_msg: str, timeout_msg: str
) -> bool:
    print(f"[Oracle] waiting for {label} ", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _is_up(url):
            print(f" {ready_msg}")
            return True
        print(".", end="", flush=True)
        time.sleep(1.5)
    print(f" {timeout_msg}")
    return False


def _wait_for_backend(timeout: int = HEALTH_TIMEOUT_SECONDS) -> bool:
    return _wait_until_up(
        "the DM brain to wake up", HEALTH_URL, timeout,
        ready_msg="ready!", timeout_msg="(timed out - continuing anyway)",
    )


def _comfyui_installed() -> bool:
    return START_COMFYUI and COMFYUI_PYTHON.exists() and COMFYUI_BAT.exists()


def _cloudflared_installed() -> bool:
    if not (START_CLOUDFLARE and CLOUDFLARE_BAT.exists()):
        return False
    from shutil import which
    if which("cloudflared"):
        return True
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return (Path(pf86) / "cloudflared" / "cloudflared.exe").exists()


def _read_cloudflare_url(timeout: int) -> "str | None":
    """Poll the tunnel's logfile for the assigned *.trycloudflare.com URL."""
    pat = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.time() + timeout
    print("[Oracle] opening the public tunnel ", end="", flush=True)
    while time.time() < deadline:
        try:
            m = pat.search(CLOUDFLARE_URL_LOG.read_text(encoding="utf-8", errors="ignore"))
            if m:
                print(" ready!")
                return m.group(0)
        except OSError:
            pass
        print(".", end="", flush=True)
        time.sleep(1.0)
    print(" (no URL yet - check the tunnel window)")
    return None


def _wait_for_named_tunnel(timeout: int) -> bool:
    """Poll the tunnel logfile until cloudflared registers an edge connection.

    A named tunnel's hostname is fixed, so there's no URL to discover - but we
    still want to know the tunnel actually came up. If it doesn't register a
    connection (bad config, missing credentials, cert expired) the hostname
    would just 502; better to warn than to hand out a dead URL."""
    pat = re.compile(r"[Rr]egistered tunnel connection|[Cc]onnection .*registered")
    deadline = time.time() + timeout
    print("[Oracle] connecting the named tunnel to Cloudflare ", end="", flush=True)
    while time.time() < deadline:
        try:
            if pat.search(CLOUDFLARE_URL_LOG.read_text(encoding="utf-8", errors="ignore")):
                print(" connected!")
                return True
        except OSError:
            pass
        print(".", end="", flush=True)
        time.sleep(1.0)
    print(" (no connection yet - check the tunnel window)")
    return False


def _ollama_root() -> str:
    """The Ollama base URL (scheme://host:port), read from the backend's
    LLM_BASE_URL when it points at a local Ollama server; else the default."""
    try:
        for raw in BACKEND_CRED_ENV.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            if key.strip() == "LLM_BASE_URL":
                url = val.strip().strip('"').strip("'")
                m = re.match(r"^(https?://[^/]+)", url)
                if m:
                    return m.group(1)
    except OSError:
        pass
    return DEFAULT_OLLAMA_ROOT


def _llm_is_local_ollama() -> bool:
    """True if the backend is configured to talk to a local Ollama server
    (so the launcher should manage Ollama for the game)."""
    root = _ollama_root().lower()
    return ("11434" in root) or ("localhost" in root) or ("127.0.0.1" in root)


def _adopt_or_start(
    label: str, health_url: str, bat: Path, timeout: int, *,
    ready_msg: str, timeout_msg: str,
) -> "tuple[subprocess.Popen | None, bool]":
    """Use an already-running instance if present (and leave it running on
    exit), otherwise start our own. Returns (proc_or_None, started_by_us)."""
    if _is_up(health_url):
        print(f"[Oracle] {label} already running - using it (will leave it running on exit).")
        return None, False
    proc = _start(label, bat)
    _wait_until_up(label, health_url, timeout, ready_msg=ready_msg, timeout_msg=timeout_msg)
    return proc, True


def _kill_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    # /T also terminates children (e.g. Lavalink spawned by the bot, or
    # cloudflared spawned by the tunnel .bat).
    subprocess.run(
        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# Keep the ctypes callback alive for the process lifetime (GC would free it).
_CONSOLE_HANDLER_REFS: list = []


def _install_console_close_handler(get_managed) -> None:
    """Windows only: tear down child services when the console window is CLOSED
    (the X button), or on logoff/shutdown. Without this, closing the window
    hard-kills the launcher and orphans the tunnel/backend/bot. Ctrl+C and
    Ctrl+Break are left to Python's normal KeyboardInterrupt -> finally path."""
    if os.name != "nt":
        return
    # CTRL_C=0, CTRL_BREAK=1, CTRL_CLOSE=2, CTRL_LOGOFF=5, CTRL_SHUTDOWN=6
    handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)

    def _handler(ctrl_type: int) -> bool:
        if ctrl_type in (2, 5, 6):
            print("\n[Oracle] window closing - shutting the game's services down ...")
            for _name, proc in reversed(list(get_managed())):
                try:
                    _kill_tree(proc)
                except Exception:
                    pass
        return False  # fall through to default (which ends the process)

    cb = handler_type(_handler)
    _CONSOLE_HANDLER_REFS.append(cb)
    try:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(cb, True)
    except Exception as exc:  # non-fatal: Ctrl+C/finally still tears down
        print(f"[Oracle] (could not install console-close handler: {exc})")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    _banner()

    problem = _validate()
    if problem:
        return _fail(problem)

    # Track everything we START so we can stop exactly those on exit. Services
    # we merely ADOPTED (already running) are left alone. Each entry is
    # (name, proc). The backend and bot are always the game's own.
    managed: list[tuple[str, subprocess.Popen]] = []
    adopted: list[str] = []

    # Tear services down even if the launcher window is closed with the X button.
    _install_console_close_handler(lambda: managed)

    # --- Ollama (local LLM) -------------------------------------------------
    # Only relevant when the backend is pointed at a local Ollama server.
    if not START_OLLAMA:
        print("[Oracle] Ollama startup skipped (ORACLE_START_OLLAMA=0).")
    elif not _llm_is_local_ollama():
        print("[Oracle] backend LLM is not local Ollama - not starting Ollama.")
    else:
        ollama_root = _ollama_root()
        proc, started = _adopt_or_start(
            "Ollama (local LLM)", f"{ollama_root}/api/tags", OLLAMA_BAT,
            OLLAMA_TIMEOUT_SECONDS, ready_msg="ready!",
            timeout_msg="(timed out - the DM may fail until it comes up)",
        )
        if started and proc is not None:
            managed.append(("Ollama", proc))
        elif not started:
            adopted.append("Ollama (local LLM)")

    # --- ComfyUI (image generation) ----------------------------------------
    if not START_COMFYUI:
        print("[Oracle] ComfyUI startup skipped (ORACLE_START_COMFYUI=0).")
    elif _is_up(COMFYUI_HEALTH_URL):
        print("[Oracle] ComfyUI already running - using it (will leave it running on exit).")
        adopted.append("ComfyUI (image generation)")
    elif _comfyui_installed():
        proc, _started = _adopt_or_start(
            "ComfyUI (image generation)", COMFYUI_HEALTH_URL, COMFYUI_BAT,
            COMFYUI_TIMEOUT_SECONDS, ready_msg="ready!",
            timeout_msg="(timed out - images will start once it finishes loading)",
        )
        if proc is not None:
            managed.append(("ComfyUI", proc))
    else:
        print(f"[Oracle] ComfyUI not found at {COMFYUI_HOME} - images disabled (game runs normally).")

    # --- Backend + Bot (always the game's own) ------------------------------
    backend = _start("Backend (DM brain)", BACKEND_BAT)
    _wait_for_backend()
    managed.append(("Backend", backend))

    # --- Cloudflare tunnel (public HTTPS for the Discord Activity) ----------
    cf_url = None
    if not START_CLOUDFLARE:
        print("[Oracle] Cloudflare tunnel skipped (ORACLE_START_CLOUDFLARE=0).")
    elif not _cloudflared_installed():
        print("[Oracle] cloudflared not found - the Discord Activity won't be reachable "
              "from Discord (install it, or run your own tunnel). Local browser UI still works.")
    else:
        cf = _start("Cloudflare tunnel", CLOUDFLARE_BAT)
        managed.append(("Cloudflare tunnel", cf))
        if CLOUDFLARE_TUNNEL_NAME and CLOUDFLARE_TUNNEL_HOSTNAME:
            cf_url = f"https://{CLOUDFLARE_TUNNEL_HOSTNAME}"
            print(f"[Oracle] named tunnel '{CLOUDFLARE_TUNNEL_NAME}' -> {cf_url}")
            _wait_for_named_tunnel(CLOUDFLARE_CONNECT_TIMEOUT_SECONDS)
        else:
            cf_url = _read_cloudflare_url(CLOUDFLARE_URL_TIMEOUT_SECONDS)

    bot = _start("Discord Bot + Music", BOT_BAT)
    managed.append(("Discord bot", bot))

    print()
    print("-" * 60)
    print("  All systems launched. These windows should now be open:")
    for name, _proc in managed:
        print(f"    * Oracle - {name}")
    for name in adopted:
        print(f"    * {name}  (already running - not managed by the launcher)")

    if cf_url:
        host = cf_url.replace("https://", "")
        print()
        print("=" * 60)
        print("  DISCORD ACTIVITY URL  (set this in the Developer Portal)")
        print("=" * 60)
        print(f"    {cf_url}")
        print()
        print("  Dev Portal -> your app -> Activities -> URL Mappings:")
        print(f"    prefix  /   ->   target  {host}")
        if CLOUDFLARE_TUNNEL_NAME and CLOUDFLARE_TUNNEL_HOSTNAME:
            print("  Stable named tunnel - set once, no need to change it.")
        else:
            print("  Quick-tunnel URL changes each launch - update it there.")
        print("=" * 60)

    print()
    print("  Keep THIS window open while you play.")
    print("  Close it (or press Ctrl+C) to shut the game down.")
    if adopted:
        print("  (Services marked 'not managed' were already running and will")
        print("   be left running when the game shuts down.)")
    print("-" * 60)

    # Watch the game's core (backend + bot): once both have exited, we're done.
    core = [(n, p) for n, p in managed if n in {"Backend", "Discord bot"}]
    try:
        while True:
            time.sleep(1)
            if all(p.poll() is not None for _, p in core):
                print("\n[Oracle] the backend and bot have stopped.")
                break
    except KeyboardInterrupt:
        print("\n[Oracle] shutdown requested ...")
    finally:
        # Stop only what we started, newest first (bot/backend before deps).
        for name, proc in reversed(managed):
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
