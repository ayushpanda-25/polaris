"""
Vercel serverless entry point for Polaris.

Vercel runs each request in a fresh Python lambda — background threads
don't survive between requests. So this entry point uses an on-demand
compute model: every dashboard refresh recomputes the synthetic GEX
grid in the request handler itself.

For local + live LSEG mode, use `python3 -m src.dashboard --lseg` instead.
This file is ONLY for the public Vercel deployment.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

# Make project modules importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import dash
import numpy as np
import plotly.graph_objects as go
from dash import Dash, Input, Output, dcc, html

from src.compute_loop import ComputeLoop  # noqa: F401  (kept for parity)
from src.data_feed import SyntheticOptionsFeed
from src.gex_engine import compute_grid
from src.node_classifier import classify_nodes
from src.dashboard import (
    MODE_LABELS,
    SKYLIT_COLORSCALE,
    _build_heatmap_figure,
    _build_trinity_figure,
)
from src.memory_cache import GEXCache

import config as app_config


# Module-level singletons (created once per lambda warm-start)
_feed = SyntheticOptionsFeed()
_cache = GEXCache()


def _refresh_cache_for(ticker: str):
    """Pull a fresh snapshot for one ticker and update the in-memory cache."""
    snap = _feed.get_chain_snapshot(ticker)
    grid = compute_grid(
        ticker=snap.ticker,
        spot=snap.spot,
        contracts=snap.contracts,
        timestamp=snap.timestamp,
    )
    nodes = classify_nodes(grid)
    _cache.update(ticker, grid, nodes)
    return grid, nodes


# Pre-warm the cache for all tickers at cold start
for _t in app_config.TICKERS:
    try:
        _refresh_cache_for(_t)
    except Exception as e:
        print(f"[vercel cold-start] {_t} prime failed: {e}")


# Build the Dash app
app = Dash(__name__, title="Polaris — Dealer GEX")
server = app.server  # Vercel hooks into this Flask WSGI object

app.layout = html.Div(
    style={
        "backgroundColor": "#0b0f17",
        "color": "#e8eef8",
        "minHeight": "100vh",
        "fontFamily": "-apple-system, system-ui, sans-serif",
        "padding": "16px 24px",
    },
    children=[
        html.Div(
            style={"display": "flex", "alignItems": "center", "justifyContent": "space-between"},
            children=[
                html.Div(
                    children=[
                        html.H2("★  Polaris", style={"margin": 0, "color": "#f9d649", "display": "inline-block"}),
                        html.Span(
                            " — synthetic data demo",
                            style={"color": "#9cc4ff", "fontSize": 13, "marginLeft": 12},
                        ),
                    ],
                ),
                html.Div(id="last-update", style={"color": "#9cc4ff", "fontSize": 13}),
            ],
        ),
        html.Div(
            style={
                "marginTop": 8,
                "color": "#5d6f8a",
                "fontSize": 11,
                "fontStyle": "italic",
            },
            children=[
                "Public deploy uses on-request synthetic data (no LSEG access). "
                "Run locally with --lseg for real options chains."
            ],
        ),
        html.Div(
            style={"marginTop": 16, "display": "flex", "gap": 12, "alignItems": "center", "flexWrap": "wrap"},
            children=[
                dcc.Dropdown(
                    id="ticker-select",
                    options=[{"label": t, "value": t} for t in app_config.TICKERS]
                            + [{"label": "TRINITY", "value": "TRINITY"}],
                    value="SPY",
                    clearable=False,
                    style={"width": 180, "color": "#111"},
                ),
                dcc.RadioItems(
                    id="mode-select",
                    options=[
                        {"label": " GEX ", "value": "gex"},
                        {"label": " GEX·√T ", "value": "gex_norm"},
                        {"label": " VEX ", "value": "vex"},
                        {"label": " Color ", "value": "color"},
                    ],
                    value="gex",
                    labelStyle={"display": "inline-block", "marginRight": 14},
                    inputStyle={"marginRight": 5},
                ),
            ],
        ),
        dcc.Graph(id="heatmap-graph", style={"marginTop": 16}),
        html.Div(
            id="node-summary",
            style={"marginTop": 8, "color": "#9cc4ff", "fontSize": 13},
        ),
        # Slower poll on the public deploy (10s) to keep lambda invocations down
        dcc.Interval(id="poll", interval=10_000, n_intervals=0),
    ],
)


@app.callback(
    [
        Output("heatmap-graph", "figure"),
        Output("last-update", "children"),
        Output("node-summary", "children"),
    ],
    [
        Input("poll", "n_intervals"),
        Input("ticker-select", "value"),
        Input("mode-select", "value"),
    ],
)
def _update(_n, ticker, mode):
    # On-demand recompute — no background thread on serverless.
    if ticker == "TRINITY":
        for t in ("SPY", "SPX", "QQQ"):
            try:
                _refresh_cache_for(t)
            except Exception as e:
                print(f"[vercel] refresh {t} failed: {e}")
        fig = _build_trinity_figure(_cache, mode)
        ts = time.strftime("%H:%M:%S")
        return fig, f"Last update: {ts}", ""

    try:
        grid, nodes = _refresh_cache_for(ticker)
    except Exception as e:
        print(f"[vercel] refresh {ticker} failed: {e}")
        grid = _cache.get_grid(ticker)
        nodes = _cache.get_nodes(ticker)

    fig = _build_heatmap_figure(grid, nodes, mode)

    if grid:
        ts = datetime.fromtimestamp(grid.timestamp).strftime("%H:%M:%S")
        last = f"Last update: {ts}  ·  spot ${grid.spot:.2f}"
    else:
        last = "Last update: —"

    summary_parts = []
    if nodes and nodes.king:
        summary_parts.append(
            f"♛ King: {nodes.king.strike} @ {nodes.king.expiry}  ·  ${nodes.king.value:,.0f}k"
        )
    if nodes and nodes.gatekeepers:
        gk = ", ".join(
            f"{g.strike}({g.value:+.0f}k)" for g in nodes.gatekeepers[:3]
        )
        summary_parts.append(f"Gatekeepers: {gk}")
    summary = "  ·  ".join(summary_parts) if summary_parts else "(no nodes yet)"

    return fig, last, summary


# Vercel entry — exports `app` (Flask WSGI). Vercel's @vercel/python builder
# detects this and routes requests to it.
if __name__ == "__main__":
    app.run(debug=False, port=8050)
