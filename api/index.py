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
from datetime import datetime
from pathlib import Path

# Make project modules importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dash import Dash, Input, Output, dcc, html

from src.data_feed import SyntheticOptionsFeed
from src.gex_engine import compute_grid
from src.node_classifier import classify_nodes
from src.dashboard import (
    AMBER,
    BG_BLACK,
    BG_PANEL,
    BORDER_BRIGHT,
    CYAN,
    GREEN,
    MODE_LABELS,
    MONO,
    ORANGE,
    RED,
    TEXT,
    TEXT_DIM,
    _build_heatmap_figure,
    _build_trinity_figure,
)
from src.memory_cache import GEXCache

import config as app_config


# Module-level singletons (created once per lambda warm-start)
_feed = SyntheticOptionsFeed()
_cache = GEXCache()


def _refresh_cache_for(ticker: str):
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


# Pre-warm cache for all tickers at cold start
for _t in app_config.TICKERS:
    try:
        _refresh_cache_for(_t)
    except Exception as e:
        print(f"[vercel cold-start] {_t} prime failed: {e}")


app = Dash(__name__, title="POLARIS · Dealer GEX Terminal")
server = app.server  # Vercel hooks into this Flask WSGI object


# ────────────────────────────────────────────────────────────────────
#  Reusable Bloomberg-style cell builders
# ────────────────────────────────────────────────────────────────────

def _hdr_cell(label, value, value_color=ORANGE):
    return html.Div(
        style={
            "display": "flex",
            "flexDirection": "column",
            "padding": "0 16px",
            "borderRight": f"1px solid {BORDER_BRIGHT}",
            "minWidth": 90,
            "justifyContent": "center",
        },
        children=[
            html.Div(label, style={
                "fontSize": 9,
                "color": TEXT_DIM,
                "letterSpacing": 1.2,
                "fontFamily": MONO,
                "textTransform": "uppercase",
            }),
            html.Div(value, style={
                "fontSize": 13,
                "color": value_color,
                "fontFamily": MONO,
                "fontWeight": 600,
                "marginTop": 2,
            }),
        ],
    )


# ────────────────────────────────────────────────────────────────────
#  Layout
# ────────────────────────────────────────────────────────────────────

app.layout = html.Div(
    style={
        "backgroundColor": BG_BLACK,
        "color": TEXT,
        "minHeight": "100vh",
        "fontFamily": MONO,
        "padding": 0,
        "margin": 0,
    },
    children=[
        # ═══ TOP BAR ═══
        html.Div(
            style={
                "display": "flex",
                "alignItems": "stretch",
                "borderBottom": f"2px solid {ORANGE}",
                "backgroundColor": BG_BLACK,
                "height": 56,
            },
            children=[
                html.Div(
                    style={
                        "display": "flex",
                        "alignItems": "center",
                        "padding": "0 18px",
                        "borderRight": f"1px solid {BORDER_BRIGHT}",
                    },
                    children=[
                        html.Span("★", style={
                            "fontSize": 18,
                            "color": ORANGE,
                            "marginRight": 10,
                        }),
                        html.Span("POLARIS", style={
                            "fontSize": 16,
                            "color": ORANGE,
                            "fontWeight": 700,
                            "letterSpacing": 2,
                        }),
                        html.Span(" · DEALER GEX TERMINAL", style={
                            "fontSize": 9,
                            "color": TEXT_DIM,
                            "marginLeft": 8,
                            "letterSpacing": 1,
                        }),
                    ],
                ),
                html.Div(
                    id="header-cells",
                    style={"display": "flex", "flexGrow": 1, "alignItems": "stretch"},
                ),
                # DEMO badge
                html.Div(
                    style={
                        "display": "flex",
                        "alignItems": "center",
                        "padding": "0 18px",
                        "borderLeft": f"1px solid {BORDER_BRIGHT}",
                        "fontSize": 11,
                        "letterSpacing": 0.5,
                    },
                    children=[
                        html.Span("◐", style={"color": CYAN, "marginRight": 8, "fontSize": 12}),
                        html.Span("DEMO", style={
                            "color": CYAN, "fontWeight": 700, "marginRight": 8,
                        }),
                        html.Span("synthetic data, recomputed per request",
                                  style={"color": TEXT_DIM}),
                    ],
                ),
            ],
        ),

        # ═══ FUNCTION BAR ═══
        html.Div(
            style={
                "display": "flex",
                "alignItems": "center",
                "padding": "8px 18px",
                "borderBottom": f"1px solid {BORDER_BRIGHT}",
                "backgroundColor": BG_PANEL,
                "gap": 16,
            },
            children=[
                html.Span("TICKER", style={
                    "fontSize": 9,
                    "color": TEXT_DIM,
                    "letterSpacing": 1.2,
                    "marginRight": 4,
                }),
                dcc.Dropdown(
                    id="ticker-select",
                    options=[{"label": t, "value": t} for t in app_config.TICKERS]
                            + [{"label": "TRINITY", "value": "TRINITY"}],
                    value="SPY",
                    clearable=False,
                    style={
                        "width": 140,
                        "color": "#111",
                        "fontFamily": MONO,
                        "fontSize": 12,
                    },
                ),
                html.Span("MODE", style={
                    "fontSize": 9,
                    "color": TEXT_DIM,
                    "letterSpacing": 1.2,
                    "marginLeft": 12,
                    "marginRight": 4,
                }),
                dcc.RadioItems(
                    id="mode-select",
                    options=[
                        {"label": " GEX ", "value": "gex"},
                        {"label": " GEX·√T ", "value": "gex_norm"},
                        {"label": " VEX ", "value": "vex"},
                        {"label": " Δ\u0393/Δt ", "value": "color"},
                    ],
                    value="gex",
                    labelStyle={
                        "display": "inline-block",
                        "marginRight": 14,
                        "color": ORANGE,
                        "fontSize": 11,
                        "fontFamily": MONO,
                        "letterSpacing": 1,
                        "cursor": "pointer",
                    },
                    inputStyle={"marginRight": 4, "accentColor": ORANGE},
                ),
                # Live local link, far right
                html.Div(
                    style={"marginLeft": "auto"},
                    children=[
                        html.A(
                            "GITHUB.COM/AYUSHPANDA-25/POLARIS ↗",
                            href="https://github.com/ayushpanda-25/polaris",
                            target="_blank",
                            style={
                                "color": TEXT_DIM,
                                "fontSize": 10,
                                "letterSpacing": 1,
                                "textDecoration": "none",
                                "fontFamily": MONO,
                            },
                        ),
                    ],
                ),
            ],
        ),

        # ═══ MAIN HEATMAP ═══
        html.Div(
            style={"padding": "8px 18px 0 18px"},
            children=[dcc.Graph(id="heatmap-graph", config={"displaylogo": False})],
        ),

        # ═══ STATUS BAR ═══
        html.Div(
            id="node-summary",
            style={
                "padding": "8px 18px",
                "borderTop": f"1px solid {BORDER_BRIGHT}",
                "backgroundColor": BG_PANEL,
                "fontFamily": MONO,
                "fontSize": 11,
                "color": TEXT,
                "letterSpacing": 0.5,
            },
        ),

        dcc.Interval(id="poll", interval=10_000, n_intervals=0),
    ],
)


def _build_header_cells(grid, nodes):
    if grid is None:
        return [_hdr_cell("SPOT", "—"), _hdr_cell("KING", "—")]
    spot_str = f"${grid.spot:,.2f}"
    king_str = f"{nodes.king.strike:g}" if nodes and nodes.king else "—"
    king_val = f"${nodes.king.value:+,.0f}K" if nodes and nodes.king else ""
    ts_str = datetime.fromtimestamp(grid.timestamp).strftime("%H:%M:%S")
    return [
        _hdr_cell("SPOT", spot_str, value_color=CYAN),
        _hdr_cell("KING STRIKE", king_str, value_color=AMBER),
        _hdr_cell("KING VALUE", king_val,
                  value_color=GREEN if nodes and nodes.king and nodes.king.value > 0 else RED),
        _hdr_cell("UPDATED", ts_str, value_color=TEXT),
        _hdr_cell("TICKER", grid.ticker, value_color=ORANGE),
    ]


def _format_status_bar(grid, nodes, mode, ticker):
    parts = []
    parts.append(html.Span(
        f"{ticker:>6}",
        style={"color": ORANGE, "marginRight": 12, "fontWeight": 700},
    ))
    parts.append(html.Span(f"MODE {MODE_LABELS.get(mode, mode).upper():<10}",
                           style={"color": TEXT_DIM, "marginRight": 12}))
    if nodes and nodes.king:
        parts.append(html.Span(
            f"KING {nodes.king.strike:>6g} @ {nodes.king.expiry}",
            style={"color": AMBER, "marginRight": 12},
        ))
        v_color = GREEN if nodes.king.value > 0 else RED
        parts.append(html.Span(
            f"{nodes.king.value:+,.0f}K",
            style={"color": v_color, "marginRight": 16, "fontWeight": 700},
        ))
    if nodes and nodes.gatekeepers:
        parts.append(html.Span("GATEKEEPERS ", style={"color": TEXT_DIM, "marginRight": 4}))
        for g in nodes.gatekeepers[:3]:
            col = GREEN if g.value > 0 else RED
            parts.append(html.Span(f"{g.strike:g} ", style={"color": ORANGE}))
            parts.append(html.Span(f"{g.value:+,.0f}K  ", style={"color": col}))
    if not (nodes and (nodes.king or nodes.gatekeepers)):
        parts.append(html.Span("(awaiting data)", style={"color": TEXT_DIM}))
    return parts


@app.callback(
    [
        Output("heatmap-graph", "figure"),
        Output("header-cells", "children"),
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
        for t in ("SPY", "SPX", "QQQ"):
            try:
                _refresh_cache_for(t)
            except Exception as e:
                print(f"[vercel] refresh {t} failed: {e}")
        fig = _build_trinity_figure(_cache, mode)
        # Use first available ticker for header
        for t in ("SPY", "SPX", "QQQ"):
            grid = _cache.get_grid(t)
            nodes = _cache.get_nodes(t)
            if grid is not None:
                break
        return fig, _build_header_cells(grid, nodes), _format_status_bar(grid, nodes, mode, "TRINITY")

    try:
        grid, nodes = _refresh_cache_for(ticker)
    except Exception as e:
        print(f"[vercel] refresh {ticker} failed: {e}")
        grid = _cache.get_grid(ticker)
        nodes = _cache.get_nodes(ticker)

    fig = _build_heatmap_figure(grid, nodes, mode)
    return fig, _build_header_cells(grid, nodes), _format_status_bar(grid, nodes, mode, ticker)


if __name__ == "__main__":
    app.run(debug=False, port=8050)
