"""Global configuration for skylit-replica."""
import os
import secrets
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "gex.db"


def _local_secret(filename: str, factory) -> str:
    """Read a secret from data/<filename>, creating it (0600) if absent.

    Keeps real secrets OUT of source: nothing usable is committed, the value
    survives restarts, and it can never lock the owner out (it's always in the
    file). Env vars still win over this for explicit prod overrides.
    """
    path = DATA_DIR / filename
    try:
        if path.exists():
            val = path.read_text().strip()
            if val:
                return val
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        val = factory()
        path.write_text(val)
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return val
    except OSError:
        # Filesystem unavailable — fall back to an ephemeral per-process secret
        # (sessions won't survive a restart, but we still never ship a default).
        return factory()

# Ticker universe (v1)
TICKERS = [
    "SPY", "SPX", "QQQ", "VIX",
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
]
INDEX_TICKERS = {"SPX"}  # Use SPXW weeklies

# Refresh cadences (seconds)
COMPUTE_INTERVAL = 15     # Recompute GEX from cache
DB_FLUSH_INTERVAL = 60    # Async writer flushes cache → SQLite
DASHBOARD_POLL = 15       # Dash polls memory cache (matches COMPUTE_INTERVAL —
                          # polling faster just re-renders identical data and
                          # lags the board; control changes are instant regardless)

# Sign imputation layer config
OVERWRITING_WHITELIST = {"AAPL", "NVDA", "MSFT", "TSLA"}
STRUCTURED_PRODUCT_STRIKES = {
    # (low, high) SPX strike bands where JPM Hedged Equity collar sits.
    # The collar rolls quarterly; these are approximate Q2 2026 levels
    # with SPX ~6800. Update after each quarterly roll.
    "SPX": [(6100, 6300), (7100, 7300)],
}

# GEX heatmap presentation
STRIKE_WINDOW_PCT = 0.05  # +/- 5% around spot
MAX_EXPIRIES = 8          # Columns in heatmap

# ── Authentication ─────────────────────────────────────────────────
# Flask session signing key. Env var wins in prod; otherwise a strong random
# key is generated once into data/.session_secret (never a committed default —
# the old 'polaris-dev-key-change-in-prod' made every session cookie forgeable).
SESSION_SECRET = os.environ.get("POLARIS_SESSION_SECRET") or _local_secret(
    ".session_secret", lambda: secrets.token_hex(32)
)
# Astraios member access codes. Friends enter one of these instead of an LSEG
# key. Case-insensitive. Env var wins; otherwise a random code is generated into
# data/.polaris_access.txt (read it to learn / share the active code). No code is
# committed to source anymore — the old literal "ASTRAIOS-2026" was the live one.
FRIEND_CODES = [
    c.strip()
    for c in (
        os.environ.get("POLARIS_FRIEND_CODES")
        or _local_secret(".polaris_access.txt", lambda: "ASTRAIOS-" + secrets.token_hex(4).upper())
    ).split(",")
    if c.strip()
]
# BYOK login (accept a raw LSEG-shaped key as auth). Off by default: the served
# data comes from the OWNER's feed regardless, so a bare 32-hex string was a
# format-only check that let anyone in. Set POLARIS_ALLOW_BYOK=1 to re-enable.
ALLOW_BYOK = os.environ.get("POLARIS_ALLOW_BYOK", "").strip() in ("1", "true", "yes")
# The cloud terminal. Not a demo any more — it signs members in with their
# Astraios account, so this link lands on a sign-in card, not open data.
DEMO_URL = "https://polaris-omega-five.vercel.app"
