#!/usr/bin/env python3
"""
Polaris dashboard watchdog.

Why this exists
---------------
Polaris's ComputeLoop opens its own LSEG desktop session via
refinitiv-data. When that session goes stale (Workspace relaunch,
long idle, LSEG backend blip), every call to get_chain_snapshot
raises "Could not resolve spot price ..." forever — the loop
catches and ignores the exception and never tries to reopen the
session. The dashboard keeps serving, the staleness banner flips
to OFFLINE, and the only fix today is a manual dashboard restart.

This watchdog:
  1. Ensures exactly one polaris dashboard process is running.
  2. Tails /tmp/polaris_dashboard.log and counts [compute_loop]
     "failed:" lines in a moving 90-second window.
  3. If the failure rate crosses DARKNESS_THRESHOLD (mostly total
     darkness across all tickers for ~1 minute), restarts the
     dashboard so it opens a fresh LSEG session.
  4. Before restarting, probes http://localhost:9000/api/status.
     If Workspace's Desktop API is dead, a polaris restart won't
     help — it'd just loop. In that case we log clearly and skip
     the restart.
  5. Rate-limits restarts to once every MIN_RESTART_INTERVAL.

Managed by launchd:
    ~/Library/LaunchAgents/com.ayush.polaris.watchdog.plist
Watchdog log:   /tmp/polaris_watchdog.log
Dashboard log:  /tmp/polaris_dashboard.log
"""
from __future__ import annotations

import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# --------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------
PYTHON = "/usr/local/bin/python3"
POLARIS_DIR = Path.home() / "Claude" / "polaris"
DASHBOARD_MODULE = "src.dashboard"
DASHBOARD_ARGS = ["--lseg"]

DASHBOARD_LOG = Path("/tmp/polaris_dashboard.log")
WATCHDOG_LOG = Path("/tmp/polaris_watchdog.log")
DASHBOARD_URL = "http://localhost:8050/"

# Workspace's Desktop API proxy port drifts (9000 is common but we've seen
# 9001, 9002, 9004 after relaunches). refinitiv.data auto-discovers the
# port natively; these watchdogs use curl directly, so we have to do the
# same dance. URLs below are built from a module-global _workspace_port
# that is discovered at startup and re-discovered whenever it stops
# responding.
WORKSPACE_PROXY_PORTS = [9000, 9001, 9002, 9004, 9060, 9061]
_workspace_port: int = 9000  # initial guess; discovery updates this

def _workspace_url(path: str) -> str:
    return f"http://localhost:{_workspace_port}{path}"

# Legacy names kept as convenience aliases (callers can still reference
# them for logging), but the actual HTTP calls now go through
# _workspace_url() so a port change is picked up immediately.
def _proxy_url()       -> str: return _workspace_url("/api/status")
def _handshake_url()   -> str: return _workspace_url("/api/handshake")
def _data_probe_url()  -> str: return _workspace_url(
    "/api/rdp/data/historical-pricing/v1/views/interday-summaries/SPY.A?count=1"
)

# Same LSEG app key that the bridge watchdog and polaris itself use.
# Only used by the stateless data-layer health probe — we never create an
# rd session from here, so we do not conflict with polaris's own session or
# any other LSEG-MCP consumer on this machine.
APP_KEY = "REDACTED_KEY_ROTATED_2026-08-24"
TOKEN_TTL_SEC = 1800         # refresh cached handshake token every 30 min

# Module-level cache for the stateless data-layer probe. We do not want to
# hit /api/handshake on every 30s poll — it's slow and churns Workspace state.
_cached_token: str | None = None
_cached_token_at: float = 0.0
# How many consecutive polls we've seen AMBER (proxy OK, data layer dead).
# Reset to 0 on any GREEN check / successful restart. Used to keep the log
# quiet after the first full hint in a streak.
consecutive_data_dead = 0

POLL_INTERVAL = 30           # sec between checks
# Empirical cadence when polaris's LSEG session is dead:
# _fetch_spot() tries 3 RIC candidates per ticker and each hits a 30s
# daemon-thread timeout before the next is attempted, so EACH ticker
# takes ~90-120s to fail. _tick() iterates tickers sequentially, so
# we see ONE failure line roughly every 2 minutes, not a burst. A full
# dark _tick through 10 tickers takes ~20 minutes.
# Window needs to be wide enough to catch multiple failures; threshold
# needs to be low enough that a one-ticker-per-2-min cadence trips it.
DARKNESS_WINDOW = 600        # sec (10 min) — sees ~5 dark failures
DARKNESS_THRESHOLD = 3       # tolerate 2 transient blips in 10 min
MIN_RESTART_INTERVAL = 300   # sec between restarts
DASHBOARD_STARTUP_GRACE = 180 # generous: slow first tick is normal

# Resource-health backstops. Added after a 9-day thread leak wedged the
# dashboard: with Workspace dead, refinitiv.data's streaming client leaked a
# thread per reconnect until "RuntimeError: can't start new thread" stopped
# compute_loop entirely. Because a stopped loop emits NO "[compute_loop]
# failed:" lines, failures_in_window() read 0 and the watchdog reported
# "healthy" while the process was actually dead. These two checks catch a
# wedged/leaking process that isn't producing failure log lines.
#
# A healthy Polaris runs ~5-50 threads; the leak reached 9,216. 300 is well
# clear of any legitimate request burst but far below the fatal ceiling.
THREAD_LIMIT = int(os.environ.get("POLARIS_THREAD_LIMIT", "300"))
# Newest gex.db snapshot older than this ⇒ compute is wedged, even if HTTP
# still answers. Source-agnostic and CBOE-safe: the backup stamps snapshots
# with time.time(), so it keeps this fresh — a Workspace outage served from
# CBOE does NOT trip it. Only a genuinely stalled loop does.
DATA_STALE_LIMIT = int(os.environ.get("POLARIS_DATA_STALE_SEC", "900"))
GEX_DB = POLARIS_DIR / "data" / "gex.db"

# Regex that matches a polaris dashboard process's argv.
# We identify by the module+args, not by PID, so the watchdog can
# adopt an already-running dashboard.
POLARIS_CMD_RE = re.compile(r"src\.dashboard.*--lseg")

# Extracts the line timestamp and whether it's a compute_loop failure.
# Dash access log uses: 127.0.0.1 - - [DD/Mon/YYYY HH:MM:SS] "..."
DASH_TS_RE = re.compile(
    r"\[(\d{1,2})/(\w{3})/(\d{4}) (\d{2}):(\d{2}):(\d{2})\]"
)
FAILURE_MARKER = b"[compute_loop]"
FAILURE_FAILED = b"failed:"

MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


# --------------------------------------------------------------------
# Plumbing
# --------------------------------------------------------------------
def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts}  {msg}\n"
    try:
        with open(WATCHDOG_LOG, "a") as f:
            f.write(line)
    except Exception:
        pass
    sys.stdout.write(line)
    sys.stdout.flush()


def run(cmd: list[str], timeout: float = 5) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def polaris_pids() -> list[int]:
    """PIDs of any running polaris dashboard process."""
    try:
        out = run(["pgrep", "-f", r"src\.dashboard.*--lseg"], timeout=3).stdout
        return [int(p) for p in out.split() if p.strip().isdigit()]
    except Exception:
        return []


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def kill_polaris() -> None:
    pids = polaris_pids()
    if not pids:
        return
    log(f"  SIGTERM polaris pids {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    for _ in range(10):
        time.sleep(0.5)
        if not any(_pid_alive(p) for p in pids):
            return
    # escalate
    for pid in pids:
        if _pid_alive(pid):
            log(f"  SIGKILL pid {pid}")
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    time.sleep(1)


def start_polaris() -> None:
    """Spawn the dashboard the same way it was originally launched."""
    # open() truncates the log so our darkness window can't count
    # old failures from before the restart.
    fh = open(DASHBOARD_LOG, "w")
    subprocess.Popen(
        [PYTHON, "-u", "-m", DASHBOARD_MODULE, *DASHBOARD_ARGS],
        cwd=str(POLARIS_DIR),
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log(f"  spawned polaris: {PYTHON} -u -m {DASHBOARD_MODULE} {' '.join(DASHBOARD_ARGS)}")


# --------------------------------------------------------------------
# Health measurement
# --------------------------------------------------------------------
def _parse_dash_ts(line: bytes, now: datetime) -> datetime | None:
    m = DASH_TS_RE.search(line.decode(errors="ignore"))
    if not m:
        return None
    day, mon, year, hh, mm, ss = m.groups()
    try:
        return datetime(
            int(year), MONTHS.get(mon, now.month), int(day),
            int(hh), int(mm), int(ss),
        )
    except ValueError:
        return None


def failures_in_window(window_sec: int) -> int:
    """Count [compute_loop] 'failed:' lines newer than window_sec seconds old."""
    if not DASHBOARD_LOG.exists():
        return 0
    try:
        # Last ~2MB covers many minutes of dashboard log.
        data = DASHBOARD_LOG.read_bytes()[-2_000_000:]
    except Exception:
        return 0

    now = datetime.now()
    cutoff = now - timedelta(seconds=window_sec)

    # Failure lines like:
    #   [compute_loop] NVDA failed: Could not resolve spot price for NVDA
    # have no timestamp of their own. We use the NEAREST preceding
    # Dash access-log line (which has one) as a proxy timestamp.
    # That's accurate to within ~1 second because both come from
    # the same process on the same polling cadence.
    count = 0
    last_ts: datetime | None = None
    for line in data.splitlines():
        ts = _parse_dash_ts(line, now)
        if ts is not None:
            last_ts = ts
            continue
        if FAILURE_MARKER in line and FAILURE_FAILED in line:
            ref = last_ts or now   # if no preceding ts, assume "now"
            if ref >= cutoff:
                count += 1
    return count


def dashboard_responsive() -> bool:
    try:
        r = run(
            ["curl", "-sm", "3", "-o", "/dev/null", "-w", "%{http_code}",
             DASHBOARD_URL],
            timeout=5,
        )
        code = r.stdout.strip()
        return code.isdigit() and int(code) < 500
    except Exception:
        return False


def proc_thread_count(pid: int) -> int:
    """Thread count for pid via `ps -M` (macOS: one header + one line per
    thread). Returns 0 on any error so a probe hiccup never forces a
    restart."""
    try:
        out = run(["ps", "-M", str(pid)], timeout=5).stdout
        lines = [ln for ln in out.splitlines() if ln.strip()]
        return max(len(lines) - 1, 0)
    except Exception:
        return 0


def data_age_seconds() -> float | None:
    """Seconds since the newest gex_snapshots row, or None if unreadable.

    Opened read-only so we never block or lock the live writer. None (missing
    DB / locked / no rows) is treated as 'unknown' by the caller — never as a
    restart trigger.

    NOTE: the connection MUST be closed explicitly in a finally block.
    `with sqlite3.connect(...) as conn` only commits/rolls back — it does NOT
    close the connection, so relying on it here leaks a file descriptor every
    poll and eventually exhausts the watchdog's fd limit (which then breaks its
    own pgrep subprocess and makes it think polaris died). Learned the hard
    way — do not "simplify" this back to a bare `with`."""
    conn = None
    try:
        uri = f"file:{GEX_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2)
        row = conn.execute("SELECT MAX(ts) FROM gex_snapshots").fetchone()
        if not row or row[0] is None:
            return None
        return max(time.time() - float(row[0]), 0.0)
    except Exception:
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _probe_status_on(port: int) -> bool:
    """Raw ST_PROXY_READY check for a specific port, no caching, no side effects."""
    try:
        r = run(
            ["curl", "-sm", "2", "-o", "-", "-w", "\n%{http_code}",
             f"http://localhost:{port}/api/status"],
            timeout=3,
        )
    except Exception:
        return False
    body, _, code = r.stdout.rpartition("\n")
    return code.strip() == "200" and "ST_PROXY_READY" in body


def _discover_workspace_port() -> int | None:
    """Scan WORKSPACE_PROXY_PORTS; return the first one serving
    ST_PROXY_READY, or None if nothing is alive."""
    global _workspace_port
    # try the currently-cached port first (common case)
    if _probe_status_on(_workspace_port):
        return _workspace_port
    for port in WORKSPACE_PROXY_PORTS:
        if port == _workspace_port:
            continue  # already tried
        if _probe_status_on(port):
            old = _workspace_port
            _workspace_port = port
            log(f"  Workspace proxy port: :{old} → :{port}")
            return port
    return None


def proxy_responsive() -> bool:
    """Cheap first-line check: is Workspace's Desktop API proxy answering
    its lightweight /api/status endpoint with ST_PROXY_READY?

    Auto-discovers the port: tries the currently-cached _workspace_port
    first, and if it fails, scans WORKSPACE_PROXY_PORTS to find wherever
    Workspace has drifted to. The refinitiv.data library does the same
    dance internally — we match its behavior so we don't get false RED
    readings just because Workspace came back on a different port.

    Note: status=ST_PROXY_READY is NOT sufficient evidence the data layer
    works — Workspace can sit in that state while every /api/rdp/data/...
    call hangs. Use data_layer_responsive() for the deeper check.
    """
    return _discover_workspace_port() is not None


def _fetch_access_token() -> str | None:
    """POST /api/handshake and return the access_token, or None on failure."""
    body = (
        '{"AppKey":"' + APP_KEY
        + '","AppScope":"trapi","ApiVersion":"1"}'
    )
    try:
        r = run(
            [
                "curl", "-sm", "3",
                "-H", "Content-Type: application/json",
                "-X", "POST",
                "--data", body,
                "-o", "-",
                "-w", "\n%{http_code}",
                _handshake_url(),
            ],
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None
    payload, _, code = r.stdout.rpartition("\n")
    if code.strip()[:1] != "2":
        return None
    # Substring scan instead of json.loads so we don't add an import.
    # Handshake response is compact JSON from Workspace; reliable enough.
    key = '"access_token"'
    idx = payload.find(key)
    if idx < 0:
        return None
    q1 = payload.find('"', idx + len(key))
    if q1 < 0:
        return None
    q2 = payload.find('"', q1 + 1)
    if q2 < 0:
        return None
    token = payload[q1 + 1:q2]
    return token or None


def _get_cached_token(force: bool = False) -> str | None:
    """Return a cached handshake access_token, refreshing if stale/forced."""
    global _cached_token, _cached_token_at
    now = time.time()
    if (
        force
        or _cached_token is None
        or (now - _cached_token_at) > TOKEN_TTL_SEC
    ):
        tok = _fetch_access_token()
        if tok is None:
            return None
        _cached_token = tok
        _cached_token_at = now
    return _cached_token


def data_layer_responsive() -> bool:
    """Deeper health probe: does Workspace's data layer actually answer a
    real /api/rdp/data/... request?

    Stateless — does NOT open an rd session, so it cannot interfere with
    polaris's own session or any other LSEG-MCP consumer. Uses the existing
    curl-based run() helper so we pick up no new imports. A True result
    means:
      - handshake succeeded (or cached token is still valid), AND
      - a 1-row SPY.A interday-summaries GET came back 2xx with a JSON array.

    A False result is the AMBER state (proxy says ready, but the data plane
    is hung) OR the handshake itself failed. The caller should already have
    confirmed proxy_responsive() is True before acting on this signal.
    """
    token = _get_cached_token(force=False)
    if token is None:
        # Handshake failed. Most common cause: Workspace came back on a
        # different port. Re-scan the known-good port list and retry the
        # token fetch once before giving up.
        if _discover_workspace_port() is not None:
            token = _get_cached_token(force=True)
        if token is None:
            return False

    def _probe(tok: str) -> tuple[bool, str]:
        try:
            r = run(
                [
                    "curl", "-sm", "3",
                    "-H", f"Authorization: Bearer {tok}",
                    "-o", "-",
                    "-w", "\n%{http_code}",
                    _data_probe_url(),
                ],
                timeout=5,
            )
        except subprocess.TimeoutExpired:
            return (False, "timeout")
        except Exception:
            return (False, "err")
        payload, _, code = r.stdout.rpartition("\n")
        code = code.strip()
        if code[:1] != "2":
            return (False, code or "nocode")
        stripped = payload.lstrip()
        if not stripped.startswith("["):
            return (False, "notarray")
        return (True, code)

    ok, reason = _probe(token)
    if ok:
        return True
    # If the token went stale mid-cycle, refresh once and retry.
    if reason == "401":
        token = _get_cached_token(force=True)
        if token is None:
            return False
        ok, _ = _probe(token)
        return ok
    return False


# --------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------
def main() -> int:
    log("=" * 60)
    log(f"Polaris watchdog starting (pid {os.getpid()})")
    log(f"  poll={POLL_INTERVAL}s  window={DARKNESS_WINDOW}s  "
        f"threshold={DARKNESS_THRESHOLD}  min_restart={MIN_RESTART_INTERVAL}s")

    global consecutive_data_dead

    last_restart = 0.0
    last_start   = 0.0   # tracks when the current process was spawned

    # If a dashboard is already running, assume it's been up a while
    # so we don't give it a 45s grace period on our first tick.
    if polaris_pids():
        last_start = 0.0
    else:
        last_start = time.time()
        log("no polaris process at startup; starting one")
        start_polaris()

    while True:
        try:
            pids = polaris_pids()

            if not pids:
                if time.time() - last_restart < MIN_RESTART_INTERVAL:
                    log("polaris not running but restart rate-limited")
                else:
                    log("polaris not running; starting")
                    start_polaris()
                    last_restart = time.time()
                    last_start = time.time()
                time.sleep(POLL_INTERVAL)
                continue

            # Respect startup grace so we don't restart a dashboard
            # that's still waking up its LSEG session.
            if time.time() - last_start < DASHBOARD_STARTUP_GRACE:
                log(f"polaris up {int(time.time()-last_start)}s (grace period)")
                time.sleep(POLL_INTERVAL)
                continue

            # Dashboard HTTP itself unresponsive = process is wedged
            if not dashboard_responsive():
                if time.time() - last_restart < MIN_RESTART_INTERVAL:
                    log("dashboard :8050 unresponsive but rate-limited")
                else:
                    log("dashboard :8050 unresponsive; restarting")
                    kill_polaris()
                    start_polaris()
                    last_restart = time.time()
                    last_start = time.time()
                time.sleep(POLL_INTERVAL)
                continue

            fails = failures_in_window(DARKNESS_WINDOW)
            if fails < DARKNESS_THRESHOLD:
                # Resource-health backstops for the blind spot where the
                # process is wedged/leaking but emits no failure lines (so
                # `fails` reads 0). Both are source-agnostic — neither trips
                # merely because Workspace is down and CBOE backup is serving.
                threads = max((proc_thread_count(p) for p in pids), default=0)
                data_age = data_age_seconds()
                unhealthy = None
                if threads > THREAD_LIMIT:
                    unhealthy = (f"thread leak: {threads} threads "
                                 f"(limit {THREAD_LIMIT})")
                elif data_age is not None and data_age > DATA_STALE_LIMIT:
                    unhealthy = (f"stale data: newest gex.db snapshot "
                                 f"{int(data_age)}s old "
                                 f"(limit {DATA_STALE_LIMIT}s)")

                if unhealthy is not None:
                    if time.time() - last_restart < MIN_RESTART_INTERVAL:
                        log(f"polaris UNHEALTHY ({unhealthy}) but rate-limited")
                    else:
                        log(f"polaris UNHEALTHY ({unhealthy}); restarting")
                        kill_polaris()
                        start_polaris()
                        last_restart = time.time()
                        last_start = time.time()
                    time.sleep(POLL_INTERVAL)
                    continue

                # Below darkness threshold — cross-check the data layer so
                # we notice early if Workspace has started hanging behind
                # the scenes (before polaris's own log catches up).
                ws_data_ok = data_layer_responsive()
                ws_str = "OK" if ws_data_ok else "DOWN"
                age_str = f"{int(data_age)}s" if data_age is not None else "?"
                log(f"polaris healthy (pids={pids}, thr={threads}, "
                    f"data_age={age_str}, "
                    f"{fails} fails in last {DARKNESS_WINDOW}s, "
                    f"ws-data={ws_str})")
                if ws_data_ok:
                    consecutive_data_dead = 0
                time.sleep(POLL_INTERVAL)
                continue

            # Dark — but is it fixable by a restart?
            # RED: proxy itself not answering → Workspace not running.
            if not proxy_responsive():
                log(
                    f"RED: Workspace proxy at :9000 not responding "
                    f"({fails} fails in last {DARKNESS_WINDOW}s). "
                    f"Launch Refinitiv Workspace."
                )
                time.sleep(POLL_INTERVAL)
                continue

            # AMBER: proxy reports ready, but the data plane is hung.
            # Restarting polaris will not fix this — Workspace itself
            # needs to be relaunched. Full hint on first AMBER in a
            # streak, terse follow-ups after.
            if not data_layer_responsive():
                consecutive_data_dead += 1
                if consecutive_data_dead == 1:
                    log(
                        f"AMBER: Workspace proxy OK but data-layer hung "
                        f"(status=ST_PROXY_READY but /api/rdp/data/... "
                        f"not returning). Polaris dark ({fails} fails in "
                        f"last {DARKNESS_WINDOW}s). Restart will not fix "
                        f"this — please fully quit and relaunch Refinitiv "
                        f"Workspace, then log back in."
                    )
                else:
                    waited = consecutive_data_dead * POLL_INTERVAL
                    log(
                        f"AMBER (still waiting, {consecutive_data_dead}th "
                        f"cycle, ~{waited}s since first AMBER; manual "
                        f"Workspace relaunch still required)"
                    )
                time.sleep(POLL_INTERVAL)
                continue

            # GREEN path reached despite darkness → both layers are fine,
            # polaris itself is the stuck component. Reset AMBER streak
            # and take the normal restart branch.
            consecutive_data_dead = 0

            if time.time() - last_restart < MIN_RESTART_INTERVAL:
                log(f"polaris dark ({fails} fails) but rate-limited")
                time.sleep(POLL_INTERVAL)
                continue

            log(f"polaris dark ({fails} fails in {DARKNESS_WINDOW}s); restarting")
            kill_polaris()
            start_polaris()
            last_restart = time.time()
            last_start = time.time()

        except Exception as e:
            log(f"watchdog error: {e!r}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    sys.exit(main() or 0)
