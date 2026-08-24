"""
VACUUM the store and swap the compacted file in.

Thinning deletes rows but SQLite keeps the freed pages inside the file, so the
disk never comes back until something rewrites it. This is that something.

The writer has to stop for the swap, and only for the swap. VACUUM INTO builds
a second file; moving it over the original while the dashboard still holds the
old one open would leave that process writing into an unlinked inode, and every
row it flushed afterwards would disappear when it exited. On a thinned store
the whole window is under a minute.

Safe by construction: the original is replaced only after the new file passes
integrity_check and reports a plausible row count, and the watchdog is
restarted on every exit path including failures.

    python3 scripts/vacuum_swap.py                 # skip if <1 GB to reclaim
    python3 scripts/vacuum_swap.py --min-reclaim 0 # always
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config as app_config

LABEL = "com.ayush.polaris.watchdog"
PLIST = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
UID = os.getuid()

# Assembled piecewise so this file's own command line can never match the
# watchdog's `pgrep -f 'src\.dashboard.*--cboe'`, which would make it mistake
# this process for the terminal it is supposed to be supervising.
NEEDLE = re.compile(r"src" + re.escape(".") + r"dashboard" + r".*" + r"--" + r"cboe")


def log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


def gb(p) -> float:
    p = Path(p)
    return p.stat().st_size / 1e9 if p.exists() else 0.0


def free_gb(p) -> float:
    s = os.statvfs(Path(p).parent)
    return s.f_bavail * s.f_frsize / 1e9


def reclaimable_gb(db: Path) -> float:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    try:
        pages = conn.execute("PRAGMA freelist_count").fetchone()[0]
        size = conn.execute("PRAGMA page_size").fetchone()[0]
    finally:
        conn.close()
    return pages * size / 1e9


def stop_writer() -> None:
    subprocess.run(["launchctl", "bootout", f"gui/{UID}/{LABEL}"], capture_output=True)
    time.sleep(2)
    out = subprocess.run(["ps", "-axo", "pid=,command="],
                         capture_output=True, text=True).stdout
    killed = []
    for line in out.splitlines():
        pid, _, cmd = line.strip().partition(" ")
        if pid.isdigit() and int(pid) != os.getpid() and NEEDLE.search(cmd):
            os.kill(int(pid), 9)
            killed.append(pid)
    time.sleep(3)
    log(f"writer stopped (watchdog booted out, killed {killed or 'nothing'})")


def start_writer() -> None:
    r = subprocess.run(["launchctl", "bootstrap", f"gui/{UID}", str(PLIST)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{UID}/{LABEL}"],
                       capture_output=True)
    log("watchdog restarted; it respawns the dashboard within ~30-90s")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-reclaim", type=float, default=1.0,
                    help="GB that must be reclaimable to bother (default 1)")
    ap.add_argument("--db", default=str(app_config.DB_PATH))
    args = ap.parse_args()

    db = Path(args.db)
    tmp = db.with_name("gex.vacuum.tmp")
    before = gb(db)
    slack = reclaimable_gb(db)
    log(f"gex.db {before:.1f} GB, {slack:.1f} GB reclaimable, {free_gb(db):.0f} GB free")

    if slack < args.min_reclaim:
        log(f"only {slack:.1f} GB to reclaim (< {args.min_reclaim} GB) — skipping.")
        return 0
    if free_gb(db) < before + 2:
        log(f"ABORT: need room for a second copy ({before:.0f} GB) plus headroom.")
        return 1

    stop_writer()
    try:
        tmp.unlink(missing_ok=True)
        log("VACUUM INTO ...")
        t0 = time.time()
        conn = sqlite3.connect(str(db), timeout=300)
        conn.execute("PRAGMA busy_timeout=300000")
        conn.execute(f"VACUUM INTO '{tmp}'")
        conn.close()
        log(f"vacuumed in {time.time()-t0:.0f}s -> {gb(tmp):.1f} GB")

        conn = sqlite3.connect(str(tmp))
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            rows = conn.execute("SELECT COUNT(*) FROM gex_snapshots").fetchone()[0]
            oi = conn.execute("SELECT COUNT(*) FROM oi_daily").fetchone()[0]
        finally:
            conn.close()
        log(f"integrity={ok}  gex_snapshots={rows:,}  oi_daily={oi:,}")
        if ok != "ok" or rows == 0:
            log("ABORT: the new file failed its check. Original left untouched.")
            tmp.unlink(missing_ok=True)
            return 2

        old = db.with_suffix(".db.old")
        db.rename(old)
        tmp.rename(db)
        old.unlink()
        log(f"swapped. gex.db is now {gb(db):.1f} GB (reclaimed {before - gb(db):.1f} GB)")
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e} — original untouched.")
        tmp.unlink(missing_ok=True)
        return 3
    finally:
        start_writer()

    log(f"free disk now {free_gb(db):.0f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
