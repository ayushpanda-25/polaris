"""Global configuration for skylit-replica."""
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
    # (low, high) SPX strike bands where JPM collar typically sits
    "SPX": [(5800, 6200)],
}

# GEX heatmap presentation
STRIKE_WINDOW_PCT = 0.05  # +/- 5% around spot
MAX_EXPIRIES = 8          # Columns in heatmap
