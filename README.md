# Skylit HeatSeeker Replica

Local replica of Skylit.ai's HeatSeeker — a real-time dealer gamma/vanna exposure heatmap for US equity options.

## Quick start (synthetic mode, no bridge needed)

```bash
cd /Users/ayushpanda/Claude/skylit-replica
/usr/local/bin/python3 -m pytest tests/     # 26 tests should pass
/usr/local/bin/python3 -m src.dashboard --synthetic
# open http://localhost:8050
```

## Live mode (Milestone 2)

Requires:
1. LSEG Workspace running
2. `~/flow-terminal/local_bridge.py` running
3. `desktop.workspace` session configured

```bash
./scripts/start_all.sh --lseg
```

## Architecture

```
LSEG Workspace
    ↓
flow-terminal bridge
    ↓
data_feed.py ──► gex_engine.py (math + sign imputation)
                        ↓
                 memory_cache.py ──► dashboard.py (fast read path)
                        ↓
                 sqlite_writer.py ──► data/gex.db ──► other tools
```

## Key modules

| File | Purpose |
|---|---|
| `src/gex_engine.py` | GEX/VEX formulas, grid aggregation |
| `src/node_classifier.py` | King/Gatekeeper/Midpoint detection |
| `src/sign_imputation.py` | Grauer depth rule + OI reconciliation + prior |
| `src/data_feed.py` | LSEG + synthetic feeds |
| `src/memory_cache.py` | Thread-safe hot cache |
| `src/sqlite_writer.py` | Background cold-path persistence |
| `src/compute_loop.py` | Pulls feed → computes grid → updates cache |
| `src/dashboard.py` | Plotly Dash UI |
| `src/gex_reader.py` | Read-only API for AlphaForge / other consumers |

## Consuming from other tools

```python
from skylit_replica.src.gex_reader import get_latest_king_node
king = get_latest_king_node("SPY")
print(king.strike, king.value)
```

## Validation

```bash
python3 scripts/validate_vs_skylit.py SPY 588.0 --king-strike 587
```

## Methodology (research-backed)

- **GEX formula**: `Σ gamma × OI × 100 × spot² × 0.01 × dealer_sign` (SqueezeMetrics white paper)
- **Dealer sign imputation**: 3-layer classifier
  1. Grauer-Schuster-Uhrig-Homburg 2023 depth rule (~83-85% per-trade accuracy)
  2. EOD open-interest reconciliation via constrained least squares
  3. Customer-side Bayesian prior with overwriting whitelist (AAPL/NVDA/MSFT/TSLA) and structured-product strike bands (SPX/JPM collar)
- **Target accuracy**: King Nodes within ±1 strike of Skylit on ~85% of days; regime (pos vs neg dealer gamma) ~95%.

## Known limitations

- No historical OPRA tick backtest (needs Databento/Polygon)
- Ticker scope limited by bridge throughput (~10 tickers comfortable, 300+ needs cadence tiers)
- Absolute GEX values will differ from Skylit by 20-40% (different sign imputation)
- Structured-product whitelist needs quarterly refresh
