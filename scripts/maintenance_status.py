"""
Did the weekly maintenance actually work? One verdict, no log reading.

The job runs unattended on a Sunday evening. Nobody is going to read
data/maintenance.log every week, and a run that half-failed looks exactly like
one that succeeded if all you check is that the file grew. So check the things
that would actually be wrong:

  - did the last run reach the end, or stop partway
  - is the store readable and internally consistent
  - is every day still represented, or did thinning eat a range
  - is the terminal back up, or was it left down after a compaction
  - is there still a full-resolution archive to fall back on
  - is the disk actually better off

Exit code is the verdict: 0 all clear, 1 something needs a look. That makes it
usable from launchd, which is how the failure notification gets sent.

    python3 scripts/maintenance_status.py          # human readable
    python3 scripts/maintenance_status.py --quiet   # only speak up on failure
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as app_config

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "maintenance.log"
STAMP = ROOT / "data" / ".thinned"
ARCHIVE = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Polaris"
NEEDLE = re.compile(r"src" + re.escape(".") + r"dashboard" + r".*" + r"--" + r"cboe")

checks: list[tuple[bool, str]] = []


def check(ok: bool, msg: str) -> None:
    checks.append((bool(ok), msg))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quiet", action="store_true",
                    help="print nothing unless something failed")
    ap.add_argument("--notify", action="store_true",
                    help="raise a macOS notification when something failed")
    args = ap.parse_args()
    db = Path(app_config.DB_PATH)

    # 1. did it run, and did it finish
    if STAMP.exists():
        last = datetime.fromtimestamp(STAMP.stat().st_mtime)
        age_d = (datetime.now() - last).days
        check(age_d <= 9,
              f"last run {last:%Y-%m-%d %H:%M} ({age_d}d ago)"
              + ("" if age_d <= 9 else " — the weekly timer may not be firing"))
    else:
        check(False, "no data/.thinned — maintenance has never completed")

    if LOG.exists():
        tail = LOG.read_text(errors="replace").strip().splitlines()[-40:]
        done = [l for l in tail if "=== done ===" in l]
        bad = [l for l in tail if "ABORT" in l or "FAILED" in l or "ERROR" in l]
        check(bool(done) and not bad,
              f"last log lines: {'clean finish' if done and not bad else 'PROBLEM'}"
              + (f" — {bad[-1].strip()}" if bad else ""))
    else:
        check(False, "no data/maintenance.log")

    # 2. is the store sound
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM gex_snapshots").fetchone()[0]
        lo, hi = conn.execute("SELECT MIN(ts), MAX(ts) FROM gex_snapshots").fetchone()
        # A gap wider than two days anywhere means thinning removed a range
        # rather than thinning it. Cheap to check: distinct days present.
        days = {r[0] for r in conn.execute(
            "SELECT DISTINCT CAST(ts/86400 AS INT) FROM gex_snapshots")}
        conn.close()
        check(integrity == "ok", f"integrity: {integrity}")
        check(rows > 0, f"{rows:,} snapshot rows")
        span_days = int((hi - lo) / 86400) + 1
        missing = span_days - len(days)
        check(missing <= 3,
              f"span {datetime.fromtimestamp(lo):%Y-%m-%d} to "
              f"{datetime.fromtimestamp(hi):%Y-%m-%d}, {missing} day(s) absent")
        check(hi > time.time() - 3 * 3600,
              f"newest row {datetime.fromtimestamp(hi):%Y-%m-%d %H:%M}"
              + ("" if hi > time.time() - 3 * 3600 else " — the writer may be stopped"))
    except Exception as e:
        check(False, f"could not read the store: {type(e).__name__}: {e}")

    # 3. is the terminal back up (a failed compaction could leave it down)
    out = subprocess.run(["ps", "-axo", "pid=,command="],
                         capture_output=True, text=True).stdout
    alive = [l.split()[0] for l in out.splitlines() if NEEDLE.search(l)]
    check(bool(alive), f"dashboard {'running, pid ' + alive[0] if alive else 'NOT RUNNING'}")

    # 4. is there still something to fall back on
    full = list((ARCHIVE / "full-resolution").glob("*.db.gz")) if ARCHIVE.exists() else []
    recent = [p for p in ARCHIVE.glob("gex-*.db.gz")
              if time.time() - p.stat().st_mtime < 9 * 86400] if ARCHIVE.exists() else []
    check(bool(full), f"full-resolution archive: {full[0].name if full else 'MISSING'}")
    check(bool(recent), f"recent rotating archive: {len(recent)} within 9 days")

    # 5. disk
    st = os.statvfs(db.parent)
    free = st.f_bavail * st.f_frsize / 1e9
    check(free > 15, f"{db.stat().st_size/1e9:.1f} GB store, {free:.0f} GB free")

    failed = [m for ok, m in checks if not ok]
    if not args.quiet or failed:
        print(f"POLARIS MAINTENANCE — {'OK' if not failed else 'NEEDS A LOOK'}")
        for ok, msg in checks:
            print(f"  {'ok  ' if ok else 'FAIL'}  {msg}")

    # Silence means success. A weekly job nobody hears from is only reassuring
    # if it would have spoken up, so the failure path has to leave the log and
    # reach the screen.
    if failed and args.notify:
        first = failed[0].split(" — ")[0][:110]
        subprocess.run([
            "osascript", "-e",
            'display notification "{}" with title "Polaris maintenance" '
            'subtitle "{} check(s) failed"'.format(
                first.replace('"', "'"), len(failed)),
        ], capture_output=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
