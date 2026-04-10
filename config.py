"""Global configuration for skylit-replica."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "gex.db"

# Ticker universe (v1)
TICKERS = [
    "SPY", "SPX", "QQQ",
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
]
INDEX_TICKERS = {"SPX"}  # Use SPXW weeklies

# Refresh cadences (seconds)
COMPUTE_INTERVAL = 15     # Recompute GEX from cache
DB_FLUSH_INTERVAL = 60    # Async writer flushes cache → SQLite
DASHBOARD_POLL = 5        # Dash polls memory cache

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
# Flask session signing key. Override via env var in production.
SESSION_SECRET = os.environ.get(
    "POLARIS_SESSION_SECRET", "polaris-dev-key-change-in-prod"
)
# Astraios member access codes. Friends enter one of these instead of
# an LSEG key. Case-insensitive matching.
FRIEND_CODES = [
    c.strip()
    for c in os.environ.get("POLARIS_FRIEND_CODES", "ASTRAIOS-2026").split(",")
    if c.strip()
]
# Public demo URL (Vercel synthetic data deployment)
DEMO_URL = "https://polaris-omega-five.vercel.app"
