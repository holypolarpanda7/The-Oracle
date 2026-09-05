"""Can a picture be cached, and can a REUSED id serve a stale one?

Two questions, and the second is the one that made this necessary. An image id
is an `INTEGER PRIMARY KEY`, which SQLite aliases to the rowid and reuses:
delete the highest row and the next insert takes its number. The store deletes
rows all the time (LRU eviction, `invalidate_*`, the prerenderer's `--redraw`
and `--prune`), so a URL built from an id alone can start meaning a different
picture — and `/imagery/surface/` was serving exactly that with a year-long
`Cache-Control`, which is a browser showing art that no longer exists with no
request ever made to find out.

The rule this asserts: **a long lifetime is served if and only if the caller
quoted the version that id currently carries.** An unstamped or stale URL still
works and still returns the right bytes; it just has to ask.

    uv run python scripts/cache_smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    import asyncio
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "oracle_backend", ROOT / "oracle-dm-backend" / "fastapi-dm.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["oracle_backend"] = mod
    spec.loader.exec_module(mod)                       # type: ignore[union-attr]

    store = mod.image_store
    import sqlite3
    c = sqlite3.connect(str(ROOT / "oracle-dm-backend" / "oracle.db"))
    row = c.execute("select id from entity_image where kind='material' "
                    "order by id limit 1").fetchone()
    if not row:
        print("no material image to test against — render the catalogue first")
        return 1
    image_id = row[0]
    token = store.cache_token(image_id)
    print(f"image {image_id}, current version {token!r}")

    bad = 0
    # The route functions directly rather than over HTTP: the decision under
    # test is which headers come back, and a test client would only add a
    # dependency (httpx) and a socket between us and the answer.
    run = asyncio.new_event_loop().run_until_complete

    def check(label: str, coro, want_immutable: bool) -> None:
        nonlocal bad
        r = run(coro)
        cc = r.headers.get("cache-control", "")
        got = "immutable" in cc
        ok = got == want_immutable
        print(f"  {'ok  ' if ok else 'FAIL'} {label:34} {cc}")
        if not ok:
            bad += 1

    print("\na stamped URL may be kept; anything else must ask:")
    img, surf = mod.imagery_image, mod.imagery_surface
    check("stamped", img(image_id, v=token), True)
    check("unstamped", img(image_id), False)
    check("stale stamp", img(image_id, v="deadbeef00"), False)
    check("stamped surface", surf(image_id, "normal", v=token), True)
    check("unstamped surface", surf(image_id, "normal"), False)

    # THE ACTUAL FAILURE, acted out: the id keeps its number and starts meaning
    # something else. A cache keyed on the URL alone would now be wrong; a
    # cache keyed on the stamped URL cannot be, because the stamp moved.
    print("\nwhen an id is reused, its old stamp must stop being honoured:")
    from imagery.models import EntityImage, cache_token
    from sqlmodel import Session
    with Session(store.engine) as s:
        r = s.get(EntityImage, image_id)
        before = cache_token(r)
        import datetime as _dt
        r.created_at = r.created_at + _dt.timedelta(seconds=1)   # a new picture
        after = cache_token(r)
        s.rollback()
    if before == after:
        print("  FAIL the token did not move when the row did")
        bad += 1
    else:
        print(f"  ok   {before} -> {after}, so the old URL revalidates")
        rr = run(mod.imagery_image(image_id, v=before))
        keep = "immutable" in rr.headers.get("cache-control", "")
        print(f"  {'ok  ' if keep else 'FAIL'} the surviving row still honours "
              f"its own stamp")
        if not keep:
            bad += 1

    print("\n" + ("every picture is cached only where it is safe to"
                   if not bad else f"{bad} check(s) failed"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
