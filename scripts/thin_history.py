"""
Thin old gex_snapshots to a coarser cadence — keep the history, drop the
resolution nobody reads.

The store grows ~170 MB/day and the iCloud archive keeps three full gzipped
copies alongside it, so the pair grows about 290 MB/day. That was fine when
there was 89 GB free. There is a lot less now, and a full disk takes down the
writer, the watchdog, and everything else on the machine — not just Polaris.

Deleting old days outright is the wrong answer: Ayush's call was keep
everything, and the long history is the point. But nothing reads a 69-second
cadence from three weeks ago. The tape wants fine granularity for the last
half hour and one sample an hour beyond that; a day-over-day OI model wants
one a day. So: keep every snapshot inside the recent window, thin everything
older to one per interval, and the history survives at a fraction of the size.

DRY RUN BY DEFAULT. Nothing is deleted unless you pass --apply, and --apply
refuses to run without a fresh archive to fall back on.

    python3 scripts/thin_history.py                      # what would it save
    python3 scripts/thin_history.py --keep-full 14 --every 30
    python3 scripts/thin_history.py --apply               # actually do it
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as app_config

ARCHIVE_DIR = (Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Polaris")


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n:,.1f} TB"


def survey(conn, cutoff: int, every: int) -> tuple[int, int]:
    """(rows older than cutoff, rows of those we would keep)."""
    old = conn.execute(
        "SELECT COUNT(*) FROM gex_snapshots WHERE ts < ?", (cutoff,)
    ).fetchone()[0]
    # One surviving snapshot per ticker per interval bucket.
    kept_ts = conn.execute(
        "SELECT COUNT(*) FROM (SELECT ticker, ts / ? AS b FROM gex_snapshots "
        "WHERE ts < ? GROUP BY ticker, b)",
        (every, cutoff),
    ).fetchone()[0]
    rows_per_snapshot = conn.execute(
        "SELECT COUNT(*) FROM gex_snapshots WHERE ts = "
        "(SELECT MAX(ts) FROM gex_snapshots WHERE ticker='SPY') AND ticker='SPY'"
    ).fetchone()[0] or 1
    return old, kept_ts * rows_per_snapshot


def freshest_archive() -> tuple[Path | None, float]:
    if not ARCHIVE_DIR.exists():
        return None, 0.0
    snaps = sorted(ARCHIVE_DIR.glob("gex-*.db.gz"), key=lambda p: p.stat().st_mtime)
    if not snaps:
        return None, 0.0
    newest = snaps[-1]
    return newest, (time.time() - newest.stat().st_mtime) / 3600


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-full", type=int, default=7,
                    help="days of full-cadence history to leave untouched")
    ap.add_argument("--every", type=int, default=15,
                    help="minutes between kept snapshots, beyond that window")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run)")
    ap.add_argument("--db", default=str(app_config.DB_PATH))
    args = ap.parse_args()

    db = Path(args.db)
    size = db.stat().st_size
    every = args.every * 60
    conn = sqlite3.connect(f"file:{db}?mode=ro" if not args.apply else str(db),
                           uri=not args.apply, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")

    total = conn.execute("SELECT COUNT(*) FROM gex_snapshots").fetchone()[0]
    latest = conn.execute("SELECT MAX(ts) FROM gex_snapshots").fetchone()[0]
    cutoff = latest - args.keep_full * 86400
    old, keep = survey(conn, cutoff, every)
    drop = max(old - keep, 0)
    per_row = size / total if total else 0

    print(f"store          {db}")
    print(f"               {human(size)}, {total:,} rows, newest "
          f"{datetime.fromtimestamp(latest):%Y-%m-%d %H:%M}")
    print(f"plan           keep every snapshot for {args.keep_full}d, then "
          f"1 per {args.every} min")
    print(f"               {old:,} rows older than the window")
    print(f"               {keep:,} would survive, {drop:,} would go")
    print(f"reclaim        ~{human(drop * per_row)} of {human(size)} "
          f"({drop / total * 100:.0f}% of all rows)")

    free = os.statvfs(db.parent)
    free_b = free.f_bavail * free.f_frsize
    print(f"free disk      {human(free_b)}  ->  {human(free_b + drop * per_row)} after")
    conn.close()

    if not args.apply:
        arch, age = freshest_archive()
        print(f"\narchive        {arch.name if arch else 'NONE FOUND'}"
              + (f", {age:.0f}h old" if arch else ""))
        print("\nDRY RUN — nothing was deleted. Re-run with --apply to do it.")
        print("Note: DELETE frees pages inside the file but does not shrink it.")
        print("      Reclaiming disk needs a VACUUM INTO afterwards, which wants")
        print("      room for a second copy while it runs.")
        return 0

    arch, age = freshest_archive()
    if arch is None or age > 48:
        print("\nREFUSING: no archive newer than 48h to fall back on.")
        print("Run scripts/icloud_archive.py first, then retry.")
        return 2

    print(f"\napplying — falling back on {arch.name} ({age:.0f}h old) if this goes wrong")
    conn = sqlite3.connect(str(db), timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    t0 = time.time()
    deleted = 0
    while True:
        cur = conn.execute(
            "DELETE FROM gex_snapshots WHERE rowid IN ("
            "  SELECT rowid FROM gex_snapshots WHERE ts < ? AND ts NOT IN ("
            "    SELECT MIN(ts) FROM gex_snapshots WHERE ts < ? GROUP BY ticker, ts / ?"
            "  ) LIMIT 500000)",
            (cutoff, cutoff, every),
        )
        n = cur.rowcount or 0
        conn.commit()
        deleted += n
        print(f"  deleted {deleted:,} / ~{drop:,} ({time.time()-t0:.0f}s)", flush=True)
        if n == 0:
            break
    conn.close()
    print(f"\ndone — {deleted:,} rows in {time.time()-t0:.0f}s.")
    print("The file has NOT shrunk yet; run a VACUUM INTO to reclaim the space.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
