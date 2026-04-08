"""
Plotly Dash dashboard for the HeatSeeker replica.

Layout mirrors Skylit:
    • Header: ticker tabs, GEX/VEX toggle, last-update timestamp
    • Main heatmap: strikes (y) × expiries (x), magnitude-weighted color
    • Overlays: King Node crown, spot price line
    • Refresh: dcc.Interval polling memory cache every 5s

Run:
    python3 -m src.dashboard --synthetic
    python3 -m src.dashboard --lseg       (Milestone 2)
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import dash
import numpy as np
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, dcc, html

# Allow running as "python -m src.dashboard" from project root
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from src.compute_loop import ComputeLoop
    from src.data_feed import make_feed
    from src.gex_engine import GEXGrid
    from src.memory_cache import get_cache
    from src.node_classifier import NodeMap
    from src.sqlite_writer import SQLiteWriter
    import config as app_config
else:
    from .compute_loop import ComputeLoop
    from .data_feed import make_feed
    from .gex_engine import GEXGrid
    from .memory_cache import get_cache
    from .node_classifier import NodeMap
    from .sqlite_writer import SQLiteWriter
    import importlib
    app_config = importlib.import_module("config")


# --------------- Colorscale tuned to Skylit ---------------
# Negative = blue → dark purple, Positive = green → yellow
SKYLIT_COLORSCALE = [
    [0.00, "#2b0b3a"],  # deep purple (strong negative)
    [0.25, "#3a1654"],
    [0.45, "#1e3a5f"],  # blue
    [0.50, "#1b2838"],  # near-zero neutral
    [0.55, "#1e5f3a"],  # green
    [0.75, "#3a8f4f"],
    [1.00, "#e8d936"],  # bright yellow (strong positive)
]


MODE_LABELS = {
    "gex": "GEX",
    "gex_norm": "GEX · √T",
    "vex": "VEX",
    "color": "Color (∂Γ/∂t)",
}


def _build_heatmap_figure(grid: GEXGrid, nodes: NodeMap, mode: str = "gex") -> go.Figure:
    if grid is None or not grid.cells:
        return go.Figure(
            layout=go.Layout(
                template="plotly_dark",
                title="(no data yet — priming cache)",
                paper_bgcolor="#0b0f17",
                plot_bgcolor="#0b0f17",
            )
        )

    mat, strikes, expiries = grid.as_matrix(mode)

    # Trim to ±3% window around spot for readability
    spot = grid.spot
    lo, hi = spot * 0.97, spot * 1.03
    keep = [i for i, s in enumerate(strikes) if lo <= s <= hi]
    if keep:
        strikes = [strikes[i] for i in keep]
        mat = mat[keep, :]

    # Cap the color scale at a percentile to suppress outliers.
    # Color (∂Γ/∂t) has extreme spikes near 0DTE that drown out the rest
    # of the grid; use a tighter percentile for that mode.
    nz = np.abs(mat[mat != 0])
    if nz.size == 0:
        vmax = 1.0
    elif mode == "color":
        vmax = float(np.percentile(nz, 75))  # tighter for color
    else:
        vmax = float(np.percentile(nz, 95))
    if vmax == 0:
        vmax = 1.0

    # Only label the top-K cells by magnitude per column (Skylit does this).
    # That way each expiry column has 4-5 labeled cells spread vertically,
    # instead of the first column hoarding all labels.
    show_label = np.zeros_like(mat, dtype=bool)
    per_col = 6
    for j in range(mat.shape[1]):
        col = np.abs(mat[:, j])
        if col.sum() == 0:
            continue
        top_rows = np.argsort(col)[::-1][:per_col]
        for i in top_rows:
            if col[i] > 0:
                show_label[i, j] = True

    mode_label = MODE_LABELS.get(mode, mode.upper())

    # Symmetric scale around zero
    heat = go.Heatmap(
        z=mat,
        x=expiries,
        y=strikes,
        zmin=-vmax,
        zmax=vmax,
        colorscale=SKYLIT_COLORSCALE,
        colorbar=dict(
            title=dict(text=f"{mode_label} ($k)", side="right"),
            thickness=14,
            len=0.9,
        ),
        hovertemplate=(
            "Strike: %{y}<br>"
            "Expiry: %{x}<br>"
            f"{mode_label}: $%{{z:.1f}}k"
            "<extra></extra>"
        ),
        text=[
            [f"${mat[i, j]:,.0f}K" if show_label[i, j] else ""
             for j in range(mat.shape[1])]
            for i in range(mat.shape[0])
        ],
        texttemplate="%{text}",
        textfont=dict(size=9, color="#e8eef8", family="monospace"),
    )

    fig = go.Figure(data=[heat])

    # King node marker
    if nodes and nodes.king is not None:
        fig.add_trace(
            go.Scatter(
                x=[nodes.king.expiry],
                y=[nodes.king.strike],
                mode="markers+text",
                marker=dict(symbol="star", size=18, color="#f9d649",
                            line=dict(width=1.5, color="#1b1b1b")),
                text=["♛"],
                textposition="top center",
                textfont=dict(size=14, color="#f9d649"),
                name="King Node",
                hovertemplate=(
                    f"<b>King Node</b><br>"
                    f"Strike: {nodes.king.strike}<br>"
                    f"Expiry: {nodes.king.expiry}<br>"
                    f"GEX: ${nodes.king.value:,.0f}k<extra></extra>"
                ),
            )
        )

    # Spot price horizontal reference
    fig.add_hline(
        y=grid.spot,
        line=dict(color="#9cc4ff", width=1, dash="dot"),
        annotation_text=f"spot {grid.spot:.2f}",
        annotation_position="top left",
        annotation_font_color="#9cc4ff",
    )

    fig.update_layout(
        template="plotly_dark",
        title=dict(
            text=f"{grid.ticker} — {mode_label} Heatmap  ·  spot ${grid.spot:.2f}",
            font=dict(size=16),
        ),
        paper_bgcolor="#0b0f17",
        plot_bgcolor="#0b0f17",
        xaxis=dict(title="Expiry", tickangle=-30),
        yaxis=dict(title="Strike", autorange="reversed"),
        height=720,
        margin=dict(l=70, r=40, t=60, b=80),
    )
    return fig


def _build_trinity_figure(cache, mode: str = "gex") -> go.Figure:
    from plotly.subplots import make_subplots

    trinity_tickers = ["SPY", "SPX", "QQQ"]
    fig = make_subplots(
        rows=1,
        cols=3,
        subplot_titles=trinity_tickers,
        horizontal_spacing=0.06,
    )

    for idx, tkr in enumerate(trinity_tickers, start=1):
        grid = cache.get_grid(tkr)
        nodes = cache.get_nodes(tkr)
        if grid is None or not grid.cells:
            continue
        mat, strikes, expiries = grid.as_matrix(mode)
        nz = np.abs(mat[mat != 0])
        vmax = float(np.percentile(nz, 95)) if nz.size else 1.0
        fig.add_trace(
            go.Heatmap(
                z=mat, x=expiries, y=strikes,
                zmin=-vmax, zmax=vmax,
                colorscale=SKYLIT_COLORSCALE,
                showscale=(idx == 3),
                hovertemplate=f"{tkr}<br>Strike %{{y}}<br>Expiry %{{x}}<br>{MODE_LABELS.get(mode, mode.upper())} $%{{z:.0f}}k<extra></extra>",
            ),
            row=1, col=idx,
        )
        if nodes and nodes.king is not None:
            fig.add_trace(
                go.Scatter(
                    x=[nodes.king.expiry], y=[nodes.king.strike],
                    mode="markers", marker=dict(symbol="star", size=14, color="#f9d649"),
                    showlegend=False,
                ),
                row=1, col=idx,
            )

    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="#0b0f17",
        plot_bgcolor="#0b0f17",
        height=680,
        title=f"Trinity Mode — {MODE_LABELS.get(mode, mode.upper())}",
        margin=dict(l=50, r=50, t=80, b=60),
    )
    for i in range(1, 4):
        fig.update_yaxes(autorange="reversed", row=1, col=i)
    return fig


# --------------- App layout ---------------

def create_app(cache, tickers: list[str]) -> Dash:
    app = Dash(__name__, title="Polaris")

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
                    html.H2("★  Polaris", style={"margin": 0, "color": "#f9d649"}),
                    html.Div(id="last-update", style={"color": "#9cc4ff", "fontSize": 13}),
                ],
            ),
            html.Div(
                style={"marginTop": 16, "display": "flex", "gap": 12, "alignItems": "center"},
                children=[
                    dcc.Dropdown(
                        id="ticker-select",
                        options=[{"label": t, "value": t} for t in tickers] + [{"label": "TRINITY", "value": "TRINITY"}],
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
            dcc.Interval(id="poll", interval=app_config.DASHBOARD_POLL * 1000, n_intervals=0),
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
        if ticker == "TRINITY":
            fig = _build_trinity_figure(cache, mode)
            ts = time.strftime("%H:%M:%S")
            return fig, f"Last update: {ts}", ""

        grid = cache.get_grid(ticker)
        nodes = cache.get_nodes(ticker)
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

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic feed")
    parser.add_argument("--lseg", action="store_true", help="Use live LSEG feed (Milestone 2)")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--no-db", action="store_true", help="Disable SQLite writer")
    args = parser.parse_args()

    if not args.synthetic and not args.lseg:
        args.synthetic = True  # default

    mode = "synthetic" if args.synthetic else "lseg"
    print(f"[dashboard] starting in {mode} mode")

    cache = get_cache()
    feed = make_feed(mode)

    # Compute loop
    loop = ComputeLoop(
        feed=feed,
        cache=cache,
        tickers=app_config.TICKERS,
        interval=app_config.COMPUTE_INTERVAL,
    )
    loop.start()

    # SQLite writer
    if not args.no_db:
        writer = SQLiteWriter(
            cache=cache,
            db_path=app_config.DB_PATH,
            interval=app_config.DB_FLUSH_INTERVAL,
        )
        writer.start()
        print(f"[dashboard] sqlite writer → {app_config.DB_PATH}")

    app = create_app(cache, app_config.TICKERS)
    print(f"[dashboard] open http://localhost:{args.port}")
    app.run(debug=False, port=args.port, host="127.0.0.1")


if __name__ == "__main__":
    main()
