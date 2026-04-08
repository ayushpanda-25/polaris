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
    from src.staleness import (
        FreshnessState,
        evaluate_freshness,
        latest_cache_timestamp,
    )
    import config as app_config
else:
    from .compute_loop import ComputeLoop
    from .data_feed import make_feed
    from .gex_engine import GEXGrid
    from .memory_cache import get_cache
    from .node_classifier import NodeMap
    from .sqlite_writer import SQLiteWriter
    from .staleness import (
        FreshnessState,
        evaluate_freshness,
        latest_cache_timestamp,
    )
    import importlib
    app_config = importlib.import_module("config")


# ════════════════════════════════════════════════════════════════════
#  BLOOMBERG-STYLE THEME
# ════════════════════════════════════════════════════════════════════
BG_BLACK = "#000000"
BG_PANEL = "#0a0a0a"
BG_ROW = "#0d0d0d"
BORDER = "#1a1a1a"
BORDER_BRIGHT = "#2a2a2a"

ORANGE = "#fa8c00"          # Bloomberg primary
ORANGE_DIM = "#7a4400"
AMBER = "#ffb627"           # accent / king node
WHITE = "#ffffff"
TEXT = "#d4d4d4"
TEXT_DIM = "#7a7a7a"
CYAN = "#00b4d8"            # tickers / numerics
GREEN = "#00ff7f"           # positive / live
RED = "#ff3333"             # negative / stale
YELLOW = "#ffd60a"

MONO = "'JetBrains Mono', 'IBM Plex Mono', 'Menlo', 'Consolas', monospace"

# Bloomberg-style heatmap colorscale:
#   Strong negative → deep red
#   Mid negative   → dark red
#   Near zero      → black
#   Mid positive   → dim amber
#   Strong positive→ bright orange/amber (King Node territory)
SKYLIT_COLORSCALE = [
    [0.00, "#660000"],   # deepest red (most negative GEX)
    [0.20, "#990000"],
    [0.40, "#3a0a0a"],
    [0.50, "#000000"],   # zero
    [0.60, "#3a2a00"],
    [0.80, "#a86600"],
    [1.00, "#fa8c00"],   # bright Bloomberg orange (King Node)
]


MODE_LABELS = {
    "gex": "GEX",
    "gex_norm": "GEX · √T",
    "vex": "VEX",
    "color": "Color (∂Γ/∂t)",
}


def _add_bracket_corners(
    fig: go.Figure,
    x_idx: float,
    y_idx: float,
    color: str,
    half: float = 0.46,
    length: float = 0.20,
    width: float = 2.5,
    row: int = None,
    col: int = None,
) -> None:
    """
    Draw 4 L-shaped corner brackets around a categorical-axis cell at
    (x_idx, y_idx), plus a mini north star icon in the top-left corner
    of the cell (echoing the brand mark).

    With type='category' axes, the cell at column index N spans from N-0.5
    to N+0.5 in plot coordinates.
    """
    # 4 corners × (horizontal segment + vertical segment) = 8 lines
    corners = [
        (x_idx - half, y_idx - half, +1, +1),   # top-left
        (x_idx + half, y_idx - half, -1, +1),   # top-right
        (x_idx - half, y_idx + half, +1, -1),   # bottom-left
        (x_idx + half, y_idx + half, -1, -1),   # bottom-right
    ]
    line_style = dict(color=color, width=width)
    add_shape_kwargs = {}
    if row is not None and col is not None:
        add_shape_kwargs = {"row": row, "col": col}
    for cx, cy, xd, yd in corners:
        fig.add_shape(
            type="line", xref="x", yref="y",
            x0=cx, y0=cy,
            x1=cx + xd * length, y1=cy,
            line=line_style, layer="above",
            **add_shape_kwargs,
        )
        fig.add_shape(
            type="line", xref="x", yref="y",
            x0=cx, y0=cy,
            x1=cx, y1=cy + yd * length,
            line=line_style, layer="above",
            **add_shape_kwargs,
        )


def _build_heatmap_figure(grid: GEXGrid, nodes: NodeMap, mode: str = "gex") -> go.Figure:
    if grid is None or not grid.cells:
        return go.Figure(
            layout=go.Layout(
                template="plotly_dark",
                title=dict(
                    text="<NO DATA — PRIMING CACHE>",
                    font=dict(family=MONO, size=14, color=ORANGE),
                ),
                paper_bgcolor=BG_BLACK,
                plot_bgcolor=BG_BLACK,
            )
        )

    mat, strikes, expiries = grid.as_matrix(mode)

    # Trim to ±3% window around spot for readability
    spot = grid.spot
    lo, hi = spot * 0.97, spot * 1.03
    keep_strikes = [i for i, s in enumerate(strikes) if lo <= s <= hi]
    if keep_strikes:
        strikes = [strikes[i] for i in keep_strikes]
        mat = mat[keep_strikes, :]

    # Limit to first 6 expiries for readability (Skylit shows 5)
    if len(expiries) > 6:
        expiries = expiries[:6]
        mat = mat[:, :6]

    # Format expiry headers as MM-DD for compactness
    def _format_exp(e: str) -> str:
        try:
            return datetime.fromisoformat(e).strftime("%b %-d")
        except Exception:
            return e
    expiry_labels = [_format_exp(e) for e in expiries]
    strike_labels = [f"{s:g}" for s in strikes]

    # Cap the color scale at a percentile to suppress outliers.
    nz = np.abs(mat[mat != 0])
    if nz.size == 0:
        vmax = 1.0
    elif mode == "color":
        vmax = float(np.percentile(nz, 75))
    else:
        vmax = float(np.percentile(nz, 95))
    if vmax == 0:
        vmax = 1.0

    # Format every cell value compactly. Skylit labels EVERY cell — and we
    # can too now that columns are equal-width. For values < $1k, hide.
    def _fmt(v: float) -> str:
        if abs(v) < 0.5:
            return ""
        if abs(v) >= 1000:
            return f"${v / 1000:,.1f}M"
        return f"${v:,.0f}K"

    text_grid = [[_fmt(mat[i, j]) for j in range(mat.shape[1])]
                 for i in range(mat.shape[0])]

    mode_label = MODE_LABELS.get(mode, mode.upper())

    # Symmetric scale around zero
    heat = go.Heatmap(
        z=mat,
        x=expiry_labels,
        y=strike_labels,
        zmin=-vmax,
        zmax=vmax,
        colorscale=SKYLIT_COLORSCALE,
        colorbar=dict(
            title=dict(
                text=f"{mode_label}",
                side="right",
                font=dict(family=MONO, size=10, color=ORANGE),
            ),
            thickness=10,
            len=0.85,
            outlinewidth=0,
            tickfont=dict(family=MONO, size=9, color=TEXT_DIM),
            bgcolor=BG_BLACK,
        ),
        hovertemplate=(
            "<span style='font-family:monospace'>"
            "STRIKE  %{y}<br>"
            "EXPIRY  %{x}<br>"
            f"{mode_label.upper()}    $%{{z:,.1f}}K"
            "</span><extra></extra>"
        ),
        text=text_grid,
        texttemplate="%{text}",
        textfont=dict(size=10, color=TEXT, family=MONO),
        xgap=1,
        ygap=1,
    )

    fig = go.Figure(data=[heat])

    # King node marker — Bloomberg-style bracketed corners (viewfinder reticle)
    if nodes and nodes.king is not None:
        king_x_label = _format_exp(nodes.king.expiry)
        king_y_label = f"{nodes.king.strike:g}"
        if king_x_label in expiry_labels and king_y_label in strike_labels:
            x_idx = expiry_labels.index(king_x_label)
            y_idx = strike_labels.index(king_y_label)
            _add_bracket_corners(fig, x_idx, y_idx, AMBER)
            # Invisible scatter marker for hover info
            fig.add_trace(
                go.Scatter(
                    x=[king_x_label],
                    y=[king_y_label],
                    mode="markers",
                    marker=dict(size=30, color="rgba(0,0,0,0)"),
                    name="King Node",
                    showlegend=False,
                    hovertemplate=(
                        f"<span style='font-family:monospace'>"
                        f"<b>KING NODE</b><br>"
                        f"STRIKE  {nodes.king.strike}<br>"
                        f"EXPIRY  {nodes.king.expiry}<br>"
                        f"VALUE   ${nodes.king.value:,.0f}K"
                        f"</span><extra></extra>"
                    ),
                )
            )

    # Spot price horizontal reference line
    if strike_labels:
        spot_strike_idx = min(
            range(len(strikes)),
            key=lambda i: abs(strikes[i] - grid.spot),
        )
        fig.add_shape(
            type="line",
            xref="paper", yref="y",
            x0=0, x1=1,
            y0=spot_strike_idx, y1=spot_strike_idx,
            line=dict(color=CYAN, width=1, dash="dot"),
        )
        fig.add_annotation(
            xref="paper", yref="y",
            x=1, y=spot_strike_idx,
            text=f"SPOT {grid.spot:.2f}  ",
            showarrow=False,
            xanchor="right",
            yanchor="bottom",
            font=dict(color=CYAN, size=10, family=MONO),
        )

    fig.update_layout(
        template="plotly_dark",
        showlegend=False,
        paper_bgcolor=BG_BLACK,
        plot_bgcolor=BG_BLACK,
        xaxis=dict(
            title="",
            tickangle=0,
            type="category",
            side="top",
            tickfont=dict(size=10, color=ORANGE, family=MONO),
            showgrid=False,
            showline=True,
            linecolor=BORDER_BRIGHT,
            linewidth=1,
            zeroline=False,
        ),
        yaxis=dict(
            title="",
            autorange="reversed",
            type="category",
            tickfont=dict(size=10, color=ORANGE, family=MONO),
            showgrid=False,
            showline=True,
            linecolor=BORDER_BRIGHT,
            linewidth=1,
            zeroline=False,
        ),
        height=680,
        margin=dict(l=70, r=70, t=40, b=20),
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

    def _trinity_format_exp(e: str) -> str:
        try:
            return datetime.fromisoformat(e).strftime("%b %-d")
        except Exception:
            return e

    for idx, tkr in enumerate(trinity_tickers, start=1):
        grid = cache.get_grid(tkr)
        nodes = cache.get_nodes(tkr)
        if grid is None or not grid.cells:
            continue
        mat, strikes, expiries = grid.as_matrix(mode)

        # Trim ±3% strike window
        spot = grid.spot
        lo, hi = spot * 0.97, spot * 1.03
        keep = [i for i, s in enumerate(strikes) if lo <= s <= hi]
        if keep:
            strikes = [strikes[i] for i in keep]
            mat = mat[keep, :]

        # Limit to first 5 expiries
        if len(expiries) > 5:
            expiries = expiries[:5]
            mat = mat[:, :5]

        exp_labels = [_trinity_format_exp(e) for e in expiries]
        strike_labels = [f"{s:g}" for s in strikes]

        nz = np.abs(mat[mat != 0])
        vmax = float(np.percentile(nz, 95)) if nz.size else 1.0
        if vmax == 0:
            vmax = 1.0

        fig.add_trace(
            go.Heatmap(
                z=mat,
                x=exp_labels,
                y=strike_labels,
                zmin=-vmax, zmax=vmax,
                colorscale=SKYLIT_COLORSCALE,
                showscale=(idx == 3),
                xgap=2, ygap=2,
                hovertemplate=f"{tkr}<br>Strike %{{y}}<br>Expiry %{{x}}<br>{MODE_LABELS.get(mode, mode.upper())} $%{{z:.0f}}k<extra></extra>",
            ),
            row=1, col=idx,
        )
        if nodes and nodes.king is not None:
            kx = _trinity_format_exp(nodes.king.expiry)
            ky = f"{nodes.king.strike:g}"
            if kx in exp_labels and ky in strike_labels:
                x_idx = exp_labels.index(kx)
                y_idx = strike_labels.index(ky)
                _add_bracket_corners(
                    fig, x_idx, y_idx, AMBER,
                    length=0.22, width=2,
                    row=1, col=idx,
                )

    fig.update_layout(
        template="plotly_dark",
        showlegend=False,
        paper_bgcolor=BG_BLACK,
        plot_bgcolor=BG_BLACK,
        height=660,
        title=dict(
            text=f"TRINITY  ·  {MODE_LABELS.get(mode, mode.upper()).upper()}",
            font=dict(family=MONO, size=11, color=ORANGE),
            x=0.01,
            xanchor="left",
        ),
        margin=dict(l=50, r=70, t=50, b=20),
    )
    # Tone subplot titles to Bloomberg style
    for i, ann in enumerate(fig.layout.annotations):
        ann.font = dict(family=MONO, size=11, color=ORANGE)
    for i in range(1, 4):
        fig.update_xaxes(
            type="category", side="top",
            tickfont=dict(size=9, color=ORANGE, family=MONO),
            showgrid=False, showline=True, linecolor=BORDER_BRIGHT,
            row=1, col=i,
        )
        fig.update_yaxes(
            type="category", autorange="reversed",
            tickfont=dict(size=9, color=ORANGE, family=MONO),
            showgrid=False, showline=True, linecolor=BORDER_BRIGHT,
            row=1, col=i,
        )
    return fig


# --------------- App layout ---------------

def create_app(cache, tickers: list[str]) -> Dash:
    # Assets folder is at project root, not next to this script
    assets_path = str(Path(__file__).resolve().parents[1] / "assets")
    app = Dash(__name__, title="Polaris", assets_folder=assets_path)

    # Reusable cell builders ---------------------------------------
    def _hdr_cell(label, value, color=ORANGE, value_color=None):
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
                html.Div(value, id=f"hdr-{label.lower().replace(' ', '-')}", style={
                    "fontSize": 13,
                    "color": value_color or color,
                    "fontFamily": MONO,
                    "fontWeight": 600,
                    "marginTop": 2,
                }),
            ],
        )

    def _func_btn(label, value, selected=False):
        return {
            "label": html.Span(
                label,
                style={
                    "padding": "4px 12px",
                    "marginRight": 4,
                    "fontFamily": MONO,
                    "fontSize": 11,
                    "letterSpacing": 0.8,
                    "color": TEXT,
                    "backgroundColor": BG_PANEL,
                    "border": f"1px solid {BORDER_BRIGHT}",
                    "cursor": "pointer",
                    "display": "inline-block",
                },
            ),
            "value": value,
        }

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
            # ═══ TOP BAR (Bloomberg-style) ═══
            html.Div(
                style={
                    "display": "flex",
                    "alignItems": "stretch",
                    "borderBottom": f"2px solid {ORANGE}",
                    "backgroundColor": BG_BLACK,
                    "height": 56,
                },
                children=[
                    # Brand
                    html.Div(
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "padding": "0 18px",
                            "borderRight": f"1px solid {BORDER_BRIGHT}",
                            "backgroundColor": BG_BLACK,
                        },
                        children=[
                            html.Img(
                                src="/assets/northstar.svg",
                                style={
                                    "width": 24,
                                    "height": 24,
                                    "marginRight": 12,
                                    "filter": "drop-shadow(0 0 4px rgba(250,140,0,0.5))",
                                },
                            ),
                            html.Span("POLARIS", style={
                                "fontSize": 16,
                                "color": ORANGE,
                                "fontWeight": 700,
                                "letterSpacing": 2,
                                "fontFamily": MONO,
                            }),
                            html.Span(" · DEALER GEX TERMINAL", style={
                                "fontSize": 9,
                                "color": TEXT_DIM,
                                "marginLeft": 8,
                                "letterSpacing": 1,
                            }),
                        ],
                    ),
                    # Header data cells (populated by callback)
                    html.Div(
                        id="header-cells",
                        style={"display": "flex", "flexGrow": 1, "alignItems": "stretch"},
                    ),
                    # Freshness badge — far right
                    html.Div(
                        id="freshness-badge",
                        style={
                            "display": "flex",
                            "alignItems": "center",
                            "padding": "0 18px",
                            "borderLeft": f"1px solid {BORDER_BRIGHT}",
                            "backgroundColor": BG_BLACK,
                            "fontFamily": MONO,
                            "fontSize": 11,
                            "letterSpacing": 0.5,
                        },
                    ),
                ],
            ),

            # ═══ FUNCTION BAR (controls) ═══
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
                        options=[{"label": t, "value": t} for t in tickers]
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
                ],
            ),

            # ═══ STALE BANNER (only when stale/offline) ═══
            html.Div(id="stale-banner"),

            # ═══ MAIN HEATMAP ═══
            html.Div(
                style={"padding": "8px 18px 0 18px"},
                children=[dcc.Graph(id="heatmap-graph", config={"displaylogo": False})],
            ),

            # ═══ STATUS BAR (bottom) ═══
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

            dcc.Interval(id="poll", interval=app_config.DASHBOARD_POLL * 1000, n_intervals=0),
        ],
    )

    def _build_freshness_badge(status):
        """Bloomberg-style status indicator — colored word, no dot."""
        return [
            html.Span(status.label, style={
                "color": status.color,
                "fontWeight": 700,
                "marginRight": 10,
                "letterSpacing": 1.5,
            }),
            html.Span(
                status.message.replace("Live · ", "").split(" — ")[0]
                    if status.state == FreshnessState.LIVE
                    else status.message.split(" — ")[0],
                style={"color": TEXT_DIM},
            ),
        ]

    def _build_stale_banner(status):
        """Full-width stripe for STALE / OFFLINE states."""
        if status.state in (FreshnessState.LIVE, FreshnessState.LAGGING):
            return None
        return html.Div(
            style={
                "padding": "10px 18px",
                "backgroundColor": "#1a0000" if status.state == FreshnessState.STALE else "#0f0f0f",
                "borderTop": f"1px solid {status.color}",
                "borderBottom": f"1px solid {status.color}",
                "color": status.color,
                "fontFamily": MONO,
                "fontSize": 12,
                "fontWeight": 700,
                "letterSpacing": 1,
                "textAlign": "center",
            },
            children=status.message.upper(),
        )

    def _build_header_cells(grid, nodes):
        """The Bloomberg-style header data row."""
        if grid is None:
            return [_hdr_cell("SPOT", "—"), _hdr_cell("KING", "—"), _hdr_cell("TIME", "—")]
        spot_str = f"${grid.spot:,.2f}"
        king_str = f"{nodes.king.strike:g}" if nodes and nodes.king else "—"
        king_val = f"${nodes.king.value:+,.0f}K" if nodes and nodes.king else ""
        ts_str = datetime.fromtimestamp(grid.timestamp).strftime("%H:%M:%S")
        return [
            _hdr_cell("SPOT", spot_str, color=CYAN),
            _hdr_cell("KING STRIKE", king_str, color=AMBER),
            _hdr_cell("KING VALUE", king_val,
                      color=GREEN if nodes and nodes.king and nodes.king.value > 0 else RED),
            _hdr_cell("UPDATED", ts_str, color=TEXT),
            _hdr_cell("TICKER", grid.ticker, color=ORANGE),
        ]

    @app.callback(
        [
            Output("heatmap-graph", "figure"),
            Output("freshness-badge", "children"),
            Output("stale-banner", "children"),
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
        latest_ts = latest_cache_timestamp(cache)
        status = evaluate_freshness(latest_ts)
        badge = _build_freshness_badge(status)
        banner = _build_stale_banner(status)

        if ticker == "TRINITY":
            fig = _build_trinity_figure(cache, mode)
            # Use first available ticker for header info in trinity mode
            for t in ("SPY", "SPX", "QQQ"):
                grid = cache.get_grid(t)
                nodes = cache.get_nodes(t)
                if grid is not None:
                    break
            header = _build_header_cells(grid, nodes)
            status_bar = self_format_status_bar(grid, nodes, mode, "TRINITY")
            return fig, badge, banner, header, status_bar

        grid = cache.get_grid(ticker)
        nodes = cache.get_nodes(ticker)
        fig = _build_heatmap_figure(grid, nodes, mode)
        header = _build_header_cells(grid, nodes)
        status_bar = self_format_status_bar(grid, nodes, mode, ticker)

        return fig, badge, banner, header, status_bar

    def self_format_status_bar(grid, nodes, mode, ticker):
        """Bloomberg-style bottom status bar with monospace fixed-width fields."""
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
            gk_strs = []
            for g in nodes.gatekeepers[:3]:
                col = GREEN if g.value > 0 else RED
                gk_strs.append(html.Span(
                    f"{g.strike:g} ",
                    style={"color": ORANGE},
                ))
                gk_strs.append(html.Span(
                    f"{g.value:+,.0f}K  ",
                    style={"color": col},
                ))
            parts.append(html.Span("GATEKEEPERS ", style={"color": TEXT_DIM, "marginRight": 4}))
            parts.extend(gk_strs)
        if not (nodes and (nodes.king or nodes.gatekeepers)):
            parts.append(html.Span("(awaiting data)", style={"color": TEXT_DIM}))
        return parts

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
