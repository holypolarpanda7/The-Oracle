"""Re-render every species portrait, with a progress bar you can watch.

The whole set is ~108 files at roughly 20s each, so this runs for the better
part of an hour. It exists so that wait is VISIBLE: the underlying command
prints a line per render and nothing else, which tells you it is alive but not
how far along it is or when to come back.

Run it from its .bat so it gets its own console window:

    scripts\\rebuild_species_art.bat

or directly (WINDOWS interpreter — ComfyUI is a Windows process and WSL cannot
reach it, see CLAUDE.md -> Environment):

    .venv\\Scripts\\python.exe scripts\\rebuild_species_art.py

Progress is measured by counting portrait files whose mtime is newer than the
run's start, NOT by counting files present: this re-renders with --force, so
every target already exists on disk and a plain file count would read 100% from
the first second.

The full child log goes to species-render.log next to the portraits, so the
console stays a status bar and nothing is lost.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "activity-ui" / "public" / "assets" / "species"
LOG = ROOT / "species-render.log"
BAR_W = 42


def human(sec: float) -> str:
    sec = int(max(sec, 0))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"


def main() -> int:
    try:
        from imagery.species_portraits import expected_files
        total = len(expected_files()) or 0
    except Exception as e:
        print(f"could not read the race list: {e}")
        total = 0
    if not total:
        print("The rules DB has no races, so this would render only the 16\n"
              "built-in curated looks and leave the rest stale. Re-ingest\n"
              "first (see CLAUDE.md), then run this again.")
        return 2

    print("=" * 64)
    print(" Oracle - species portrait re-render")
    print("=" * 64)
    print(f" target   : {total} files -> {OUT_DIR}")
    print(f" log      : {LOG}")
    print(f" estimate : ~{human(total * 20)} at ~20s/render")
    print("=" * 64)
    print(" Safe to leave running. This window updates in place.\n")

    start = time.time()
    cmd = [sys.executable, "-m", "imagery.species_portraits",
           "--force", "--lineages", "--kin"]
    with open(LOG, "w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=log,
                                stderr=subprocess.STDOUT)
        done = 0
        while proc.poll() is None:
            time.sleep(2.0)
            try:
                done = sum(1 for p in OUT_DIR.glob("*.webp")
                           if p.stat().st_mtime >= start)
            except Exception:
                pass
            _draw(done, total, start)
        done = sum(1 for p in OUT_DIR.glob("*.webp") if p.stat().st_mtime >= start)
        _draw(done, total, start)

    print("\n")
    rc = proc.returncode
    if rc != 0:
        print(f"FAILED (exit {rc}). Last lines of {LOG.name}:\n")
        try:
            for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]:
                print("   " + line)
        except Exception:
            pass
        return rc

    print(f" DONE - {done}/{total} rendered in {human(time.time() - start)}")
    if done < total:
        print(f" NOTE: {total - done} file(s) were not rewritten; check {LOG.name}.")
    print("=" * 64)
    return 0


def _draw(done: int, total: int, start: float) -> None:
    frac = min(done / total, 1.0) if total else 0.0
    filled = int(frac * BAR_W)
    elapsed = time.time() - start
    eta = (elapsed / done * (total - done)) if done else 0
    bar = "#" * filled + "-" * (BAR_W - filled)
    sys.stdout.write(
        f"\r [{bar}] {done:>3}/{total}  {frac*100:5.1f}%  "
        f"elapsed {human(elapsed):>7}  eta {human(eta):>7} ")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
