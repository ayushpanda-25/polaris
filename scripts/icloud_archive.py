#!/usr/bin/env python3
"""
Polaris → iCloud archiver.

Ships the FULL gex.db store to iCloud Drive as a consistent, compressed
snapshot so the entire GEX history is preserved off-machine ("keep
everything, move the store to iCloud") WITHOUT ever putting the live,
actively-written SQLite database directly inside iCloud Drive.

Why not just move gex.db into iCloud Drive and repoint the app?
    A live SQLite DB is the wrong thing to hand to iCloud Drive:
      • The writer commits every ~60s; iCloud syncs whole files, so it would
        re-upload multi-GB on every change (bandwidth + battery thrash).
      • iCloud can upload a torn copy mid-write, and the -wal/-journal
        sidecar files can sync out of order — either corrupts the DB.
      • Apple explicitly discourages live DBs in iCloud Drive.
    So the live DB stays local (fast + safe) and we mirror a consistent
    snapshot here on a schedule. Static compressed files sync cleanly.

Snapshot method: `VACUUM INTO` writes a compact, transactionally consistent
copy even while the writer runs (it takes a read transaction; a concurrent
flush just waits out busy_timeout). We integrity-check the copy BEFORE
shipping, gzip it, and place it in iCloud with an atomic rename so a
half-written file is never visible.

Run manually:  python3 scripts/icloud_archive.py
Scheduled by:  launchd com.ayush.polaris.icloud-archive (daily, post-close)
Log:           /tmp/polaris_icloud_archive.log
"""
from __future__ import annotations

import gzip
import os
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

POLARIS_DIR = Path.home() / "Claude" / "polaris"
SRC_DB = Path(os.environ.get("POLARIS_DB_PATH", str(POLARIS_DIR / "data" / "gex.db")))
ICLOUD_DIR = Path(
    os.environ.get(
        "POLARIS_ICLOUD_DIR",
        str(
            Path.home() / "Library" / "Mobile Documents"
            / "com~apple~CloudDocs" / "Polaris"
        ),
    )
)
# How many dated snapshots to keep in iCloud. Each is the COMPLETE store, so
# even 1 is lossless; the extras are rollback insurance against a bad snapshot.
# Bounds iCloud footprint.
KEEP_N = int(os.environ.get("POLARIS_ICLOUD_KEEP", "3"))
LOCK = Path("/tmp/polaris_icloud_archive.lock")
BUSY_TIMEOUT_MS = 30_000


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"{ts}  {msg}", flush=True)


def _human(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if f < 1024 or unit == "TB":
            return f"{f:.1f}{unit}"
        f /= 1024
    return f"{f:.1f}TB"


def _acquire_lock() -> bool:
    """Single-run guard. Steals a lock older than 2h (a crashed prior run)."""
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if time.time() - LOCK.stat().st_mtime > 7200:
                LOCK.unlink()
                fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                return False
        except FileNotFoundError:
            try:
                fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except Exception:
                return False
        except Exception:
            return False
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return True


def make_snapshot(src: Path, dst_db: Path) -> None:
    """VACUUM INTO a consistent, compact copy of src at dst_db.

    Opened read-write (VACUUM INTO only reads the source and writes a new
    file, so the live DB is not modified) with a generous busy_timeout so we
    wait out the writer's brief 60s flush instead of erroring.
    """
    if dst_db.exists():
        dst_db.unlink()
    conn = sqlite3.connect(str(src), timeout=60)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("VACUUM INTO ?", (str(dst_db),))
    finally:
        conn.close()


def integrity_ok(db: Path) -> bool:
    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return bool(row) and row[0] == "ok"
    finally:
        conn.close()


def gzip_file(src: Path, dst_gz: Path) -> None:
    with open(src, "rb") as fi, gzip.open(dst_gz, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, length=8 * 1024 * 1024)


def prune_icloud(keep: int) -> None:
    if keep <= 0:
        return
    snaps = sorted(ICLOUD_DIR.glob("gex-*.db.gz"))
    for old in snaps[:-keep]:
        try:
            old.unlink()
            log(f"pruned old archive {old.name}")
        except Exception as e:
            log(f"prune failed for {old.name}: {e}")


README = """Polaris GEX store — iCloud archive
==================================
Each `gex-YYYYMMDD-HHMMSS.db.gz` is a complete, gzip-compressed, integrity-
checked snapshot of the Polaris GEX SQLite database (data/gex.db), taken on
the Mac and mirrored here. The live DB stays on the Mac on purpose — a live
SQLite database must NOT be synced directly by iCloud (it would corrupt).
These snapshots preserve the full history off-machine.

Restore:  gunzip -c gex-YYYYMMDD-HHMMSS.db.gz > gex.db
Inspect:  sqlite3 gex.db "SELECT MIN(ts), MAX(ts), COUNT(*) FROM gex_snapshots;"

Written by scripts/icloud_archive.py (launchd: com.ayush.polaris.icloud-archive).
"""


def main() -> int:
    t0 = time.time()
    log("=" * 60)
    if not SRC_DB.exists():
        log(f"ERROR: source DB not found: {SRC_DB}")
        return 1
    if not _acquire_lock():
        log("another archive run holds the lock; skipping")
        return 0

    tmp_db: Path | None = None
    tmp_gz: Path | None = None
    try:
        ICLOUD_DIR.mkdir(parents=True, exist_ok=True)
        try:
            (ICLOUD_DIR / "README.txt").write_text(README)
        except Exception:
            pass

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        tmp_db = SRC_DB.parent / f".archive-{stamp}.db"
        final_gz = ICLOUD_DIR / f"gex-{stamp}.db.gz"
        tmp_gz = ICLOUD_DIR / f".gex-{stamp}.db.gz.part"

        src_size = SRC_DB.stat().st_size
        log(f"source {SRC_DB} ({_human(src_size)}) → consistent snapshot…")
        make_snapshot(SRC_DB, tmp_db)
        snap_size = tmp_db.stat().st_size
        log(f"snapshot {_human(snap_size)}; verifying integrity…")
        if not integrity_ok(tmp_db):
            log("ERROR: snapshot failed integrity_check; aborting (shipped nothing)")
            return 2

        log("integrity OK; compressing…")
        gzip_file(tmp_db, tmp_gz)
        os.replace(tmp_gz, final_gz)   # atomic within the iCloud dir
        tmp_gz = None
        gz_size = final_gz.stat().st_size
        pct = (gz_size / snap_size * 100) if snap_size else 0
        log(f"wrote {final_gz.name} ({_human(gz_size)}, {pct:.0f}% of snapshot)")

        prune_icloud(KEEP_N)
        kept = sorted(p.name for p in ICLOUD_DIR.glob("gex-*.db.gz"))
        log(f"iCloud archives now ({len(kept)}): {kept}")
        log(f"done in {time.time() - t0:.0f}s")
        return 0
    except Exception as e:
        log(f"ERROR: {type(e).__name__}: {e}")
        return 3
    finally:
        for p in (tmp_db, tmp_gz):
            try:
                if p is not None and Path(p).exists():
                    Path(p).unlink()
            except Exception:
                pass
        try:
            LOCK.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
