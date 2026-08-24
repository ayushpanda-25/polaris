#!/bin/bash
# Weekly store maintenance: archive, thin, compact.
#
# The store grows about 170 MB/day at full cadence and the iCloud archive keeps
# three gzipped copies that track it, so left alone the pair fills the disk --
# which takes down the writer and the watchdog, not just Polaris. Nothing reads
# a 69-second cadence from three weeks ago, so beyond a recent window it gets
# thinned to one snapshot per interval. Every day stays represented.
#
# Run by launchctl weekly (com.ayush.polaris.maintenance), Sunday evening, well
# clear of both the cash session and the 17:00 daily archive job. Safe to run by
# hand any time the market is shut.
#
# The expensive part runs with the terminal UP: SQLite handles a concurrent
# DELETE fine as long as the transactions stay short, and thin_history commits
# per ticker-day for exactly that reason. Only the VACUUM swap needs the writer
# stopped, and on a thinned store that window is under a minute.
#
# Logs to data/maintenance.log.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="$ROOT/data/gex.db"
LOG="$ROOT/data/maintenance.log"
STAMP="$ROOT/data/.thinned"
PY=/usr/local/bin/python3
ARCH_DIR="$HOME/Library/Mobile Documents/com~apple~CloudDocs/Polaris"
KEEP_FULL="${KEEP_FULL:-7}"     # days of full cadence to leave alone
EVERY="${EVERY:-15}"            # minutes between kept snapshots before that
MIN_DAYS="${MIN_DAYS:-6}"       # don't repeat inside this many days
FORCE="${1:-}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S')  $*" | tee -a "$LOG"; }
size_gb() { echo "scale=1; $(stat -f%z "$1" 2>/dev/null || echo 0)/1000000000" | bc; }
free_gb() { df -g "$ROOT" | tail -1 | awk '{print $4}'; }

log "=== maintenance starting ==="
log "gex.db $(size_gb "$DB") GB, $(free_gb) GB free"

# --- guards -----------------------------------------------------------
# Ran recently? A weekly timer plus a manual run shouldn't thin twice.
if [ "$FORCE" != "--force" ] && [ -f "$STAMP" ]; then
  LAST=$(stat -f%m "$STAMP")
  AGE_D=$(( ( $(date +%s) - LAST ) / 86400 ))
  if [ "$AGE_D" -lt "$MIN_DAYS" ]; then
    log "last run was ${AGE_D}d ago (< ${MIN_DAYS}d) — nothing to do. --force to override."
    exit 0
  fi
fi

# This stops the writer for the compaction, so never during the cash session.
HHMM=$(date '+%H%M'); HHMM=$((10#$HHMM))   # 10# or 0830 parses as bad octal
DOW=$(date '+%u')
if [ "$DOW" -le 5 ] && [ "$HHMM" -ge 830 ] && [ "$HHMM" -lt 1500 ]; then
  log "ABORT: cash session is open. Run after 15:00 CT."
  exit 1
fi

NEED=$(echo "$(size_gb "$DB") * 1.5 + 5" | bc | cut -d. -f1)
if [ "$(free_gb)" -lt "$NEED" ]; then
  log "ABORT: want ~${NEED} GB free for the archive and vacuum, have $(free_gb) GB."
  exit 1
fi

# --- 1. archive -------------------------------------------------------
log "[1/3] archiving to iCloud"
"$PY" "$ROOT/scripts/icloud_archive.py" >>"$LOG" 2>&1
# Exit status is NOT the gate. icloud_archive.py returns 0 when it declines to
# run -- notably "another archive run holds the lock", which is what happens if
# the 17:00 daily job is still going. That skip once carried this script into
# the destructive step on the strength of a zero. Gate on the artefact: is
# there actually a recent archive on disk to fall back on?
FRESH=$(find "$ARCH_DIR" -maxdepth 1 -name 'gex-*.db.gz' -mmin -720 2>/dev/null | head -1)
if [ -z "$FRESH" ]; then
  log "ABORT: no archive under 12h old in $ARCH_DIR (reason logged above)."
  exit 1
fi
log "      fallback in place: $(basename "$FRESH")"

# --- 2. thin (terminal stays up) --------------------------------------
log "[2/3] thinning: keep ${KEEP_FULL}d at full cadence, then 1 per ${EVERY} min"
if ! "$PY" "$ROOT/scripts/thin_history.py" --apply \
        --keep-full "$KEEP_FULL" --every "$EVERY" >>"$LOG" 2>&1; then
  log "THIN FAILED — gex.db is intact, DELETE is transactional. Stopping here."
  exit 2
fi

# --- 3. compact (brief writer stop, handled inside) -------------------
log "[3/3] compacting"
if ! "$PY" "$ROOT/scripts/vacuum_swap.py" >>"$LOG" 2>&1; then
  log "VACUUM FAILED — gex.db is intact and the watchdog was restarted."
  exit 3
fi

date '+%Y-%m-%d %H:%M' > "$STAMP"
log "gex.db $(size_gb "$DB") GB, $(free_gb) GB free"
log "=== done ==="

# Verify rather than assume. A run that half-failed leaves a log that looks
# much like a good one, so check the things that would actually be wrong --
# store readable, no day range eaten, terminal back up, fallback still there --
# and put it on screen if any of that is off.
"$PY" "$ROOT/scripts/maintenance_status.py" --notify >>"$LOG" 2>&1 \
  || log "SELF-CHECK FAILED — see the verdict above"
