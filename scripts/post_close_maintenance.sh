#!/bin/bash
# Post-close store maintenance: archive, thin, vacuum, restart.
#
# Run once, after the cash close, with the market shut and nothing to lose by
# taking the terminal down for a few minutes. Reclaiming disk needs a VACUUM,
# a VACUUM needs to rewrite the file, and rewriting the file under a live
# writer would silently strand every write it makes into the old inode. So the
# writer stops first and comes back at the end.
#
# Order matters and every step gates the next:
#   1. fresh archive          (the fallback; nothing destructive runs without it)
#   2. stop watchdog, then dashboard  (watchdog first or it just respawns it)
#   3. thin                   (DELETE only; the file does not shrink yet)
#   4. VACUUM INTO a temp     (needs room for a second copy)
#   5. integrity_check        (swap ONLY on a clean bill)
#   6. atomic swap + restart
#
# Anything that fails leaves the original gex.db untouched and brings the
# terminal back up. Logs to data/maintenance.log.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$ROOT/data/gex.db"
TMP="$ROOT/data/gex.vacuum.tmp"
LOG="$ROOT/data/maintenance.log"
PY=/usr/local/bin/python3
WATCHDOG="com.ayush.polaris.watchdog"
KEEP_FULL="${KEEP_FULL:-7}"
EVERY="${EVERY:-15}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$LOG"; }
size_gb() { echo "scale=1; $(stat -f%z "$1" 2>/dev/null || echo 0)/1000000000" | bc; }
free_gb() { df -g "$ROOT" | tail -1 | awk '{print $4}'; }

log "=== post-close maintenance starting ==="
log "gex.db $(size_gb "$DB") GB, $(free_gb) GB free"

# Already done? Safe to leave this scheduled; a second run just no-ops.
if [ -f "$ROOT/data/.thinned" ]; then
  log "data/.thinned exists — already run on $(cat "$ROOT/data/.thinned"). Nothing to do."
  exit 0
fi

# Peak usage is the archive step: a full VACUUM INTO copy plus its gzip, so
# roughly 1.5x the database on top of what is already there. Refusing beats
# filling the disk in the middle of rewriting the store.
NEED=$(echo "$(size_gb "$DB") * 1.5 + 5" | bc | cut -d. -f1)
if [ "$(free_gb)" -lt "$NEED" ]; then
  log "ABORT: need ~${NEED} GB free for the archive + vacuum, have $(free_gb) GB."
  log "Free some space and re-run: bash scripts/post_close_maintenance.sh"
  exit 1
fi

# Market hours guard. The whole point of this job is that the writer can stop.
HHMM=$(date '+%H%M')
DOW=$(date '+%u')
if [ "$DOW" -le 5 ] && [ "$HHMM" -ge 0830 ] && [ "$HHMM" -lt 1500 ]; then
  log "ABORT: cash session is open. This takes the terminal down; run after 15:00 CT."
  exit 1
fi

# --- 1. fresh archive -------------------------------------------------
log "[1/6] archiving to iCloud (VACUUM INTO + gzip, several minutes)"
"$PY" "$ROOT/scripts/icloud_archive.py" >>"$LOG" 2>&1
# Exit status is NOT the gate. icloud_archive.py returns 0 when it declines to
# run — notably "another archive run holds the lock", which is exactly what
# happens if the 17:00 daily job is still going when this fires. That skip once
# carried this script straight through to the destructive step on the strength
# of a zero. Gate on the artefact instead: is there actually a recent archive
# on disk to fall back on?
ARCH_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Polaris"
FRESH=$(find "$ARCH_DIR" -maxdepth 1 -name 'gex-*.db.gz' -mmin -720 2>/dev/null | head -1)
if [ -z "$FRESH" ]; then
  log "ABORT: no archive under 12h old in $ARCH_DIR."
  log "       (the archive step logged its reason above; often the daily 17:00"
  log "        job still holds the lock — wait for it, then re-run)"
  exit 1
fi
log "      fallback in place: $(basename "$FRESH")"

# --- 2. stop the writer ----------------------------------------------
log "[2/6] stopping watchdog + dashboard"
launchctl bootout "gui/$(id -u)/$WATCHDOG" 2>/dev/null
sleep 2
# Matched from a file rather than a shell one-liner: any command line holding
# 'src.dashboard' and '--cboe' in that order is itself a match for the
# watchdog's own pgrep, which is how a grep gets mistaken for the terminal.
"$PY" - <<'PYKILL' >>"$LOG" 2>&1
import os, re, subprocess
NEEDLE = re.compile(r"src" + re.escape(".") + r"dashboard" + r".*" + r"--" + r"cboe")
out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True).stdout
for line in out.splitlines():
    pid, _, cmd = line.strip().partition(" ")
    if pid.isdigit() and int(pid) != os.getpid() and NEEDLE.search(cmd):
        os.kill(int(pid), 9)
        print(f"killed dashboard {pid}")
PYKILL
sleep 3

restore() {
  log "restarting watchdog (it respawns the dashboard)"
  launchctl bootstrap "gui/$(id -u)" \
    "$HOME/Library/LaunchAgents/$WATCHDOG.plist" 2>/dev/null \
    || launchctl kickstart -k "gui/$(id -u)/$WATCHDOG" 2>/dev/null
}

# --- 3. thin ----------------------------------------------------------
log "[3/6] thinning: keep ${KEEP_FULL}d full, then 1 per ${EVERY} min"
if ! "$PY" "$ROOT/scripts/thin_history.py" --apply --keep-full "$KEEP_FULL" --every "$EVERY" >>"$LOG" 2>&1; then
  log "THIN FAILED — gex.db is intact (DELETE is transactional). Restarting."
  restore; exit 2
fi

# --- 4. vacuum --------------------------------------------------------
log "[4/6] VACUUM INTO $TMP (this is what actually reclaims the space)"
rm -f "$TMP"
if ! "$PY" -c "
import sqlite3, sys
c = sqlite3.connect('$DB', timeout=120)
c.execute('PRAGMA busy_timeout=120000')
c.execute(\"VACUUM INTO '$TMP'\")
c.close()
" >>"$LOG" 2>&1; then
  log "VACUUM FAILED — original untouched. Restarting."
  rm -f "$TMP"; restore; exit 3
fi
log "      vacuumed copy is $(size_gb "$TMP") GB (was $(size_gb "$DB") GB)"

# --- 5. integrity -----------------------------------------------------
log "[5/6] integrity_check on the new file"
CHECK=$("$PY" -c "
import sqlite3
c = sqlite3.connect('$TMP')
print(c.execute('PRAGMA integrity_check').fetchone()[0])
n = c.execute('SELECT COUNT(*) FROM gex_snapshots').fetchone()[0]
print('rows', n)
c.close()
" 2>&1)
log "      $CHECK"
if ! echo "$CHECK" | grep -q "^ok"; then
  log "INTEGRITY CHECK FAILED — keeping the original, discarding the copy."
  rm -f "$TMP"; restore; exit 4
fi

# --- 6. swap ----------------------------------------------------------
log "[6/6] swapping in the vacuumed file"
mv "$DB" "$DB.old" && mv "$TMP" "$DB" && rm -f "$DB.old"
log "      gex.db is now $(size_gb "$DB") GB, $(free_gb) GB free"
date '+%Y-%m-%d %H:%M' > "$ROOT/data/.thinned"
restore

# One-shot: unload the timer that fired this. Delete data/.thinned and
# bootstrap the plist again to re-arm it, or drop the sentinel check and
# leave it on a weekly calendar to keep the store bounded for good.
launchctl bootout "gui/$(id -u)/com.ayush.polaris.maintenance" 2>/dev/null
log "=== done ==="
