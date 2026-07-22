"""
Plotly Dash dashboard for the HeatSeeker replica.

Liquid-glass UI (Astraios house language — Polaris runs cold/celestial):
    • Floating glass nav: brand, freshness, learn/sign-out
    • Control deck: ticker rail, view mode, palette picker (persisted)
    • Stat chips: spot, Star Node, value, reshuffle, updated
    • Glass heatmap board: strikes (y) × expiries (x), transparent tiles
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
    from src.learn_page import register_learn_route
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
    from .learn_page import register_learn_route
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
#  LIQUID GLASS THEME
#  (assets/polaris.css carries the page chrome; these constants style
#   everything Plotly renders inside the figure itself.)
# ════════════════════════════════════════════════════════════════════
BG = "#04060d"
INK = "rgba(255,255,255,0.92)"
INK_2 = "rgba(255,255,255,0.56)"
INK_3 = "rgba(255,255,255,0.34)"
EDGE = "rgba(255,255,255,0.10)"
EDGE_BRIGHT = "rgba(255,255,255,0.18)"
POLAR = "#6ee7ff"

FONT = ('-apple-system, BlinkMacSystemFont, "SF Pro Display", '
        '"SF Pro Text", "Helvetica Neue", sans-serif')
MONO = 'ui-monospace, "SF Mono", SFMono-Regular, "JetBrains Mono", Menlo, monospace'

# ── Legacy export surface ──────────────────────────────────────────
# api/index.py (the Vercel synthetic-demo entry) imports these names to
# style its own layout. Mapped onto the new glass theme so that entry
# keeps importing cleanly; values are intentionally rebranded.
BG_BLACK = BG
BG_PANEL = "rgba(255,255,255,0.05)"
BG_ROW = "rgba(255,255,255,0.03)"
BORDER = EDGE
BORDER_BRIGHT = EDGE_BRIGHT
ORANGE = POLAR              # brand accent is now polar cyan
ORANGE_DIM = "#2a5a6e"
AMBER = "#8ceeff"           # accent / Star Node
WHITE = "#ffffff"
TEXT = "rgba(255,255,255,0.88)"
TEXT_DIM = "rgba(255,255,255,0.40)"
CYAN = "#64d2ff"            # spot / numerics
GREEN = "#30d158"           # positive / live
RED = "#ff453a"             # negative / stale
YELLOW = "#ffd60a"


# ════════════════════════════════════════════════════════════════════
#  HEATMAP PALETTES — user-selectable, persisted in localStorage
#  Diverging scales: index 0 = strongest NEGATIVE GEX, 1.0 = strongest
#  POSITIVE. Zero is fully transparent so empty cells read as bare
#  glass and the populated tiles appear to float ("liquid" signature).
# ════════════════════════════════════════════════════════════════════
PALETTES = {
    "nebula": dict(
        label="Nebula",
        neg="#ff3d9e", pos="#45e3ff", accent="#8ceeff",
        scale=[
            [0.00, "#ff3d9e"],
            [0.18, "rgba(216,42,128,0.92)"],
            [0.40, "rgba(140,30,92,0.45)"],
            [0.50, "rgba(10,12,24,0.0)"],
            [0.60, "rgba(18,92,122,0.45)"],
            [0.82, "rgba(36,176,216,0.92)"],
            [1.00, "#45e3ff"],
        ],
    ),
    "aurora": dict(
        label="Aurora",
        neg="#b18cff", pos="#3ef2ae", accent="#7dffd4",
        scale=[
            [0.00, "#b18cff"],
            [0.18, "rgba(139,108,250,0.92)"],
            [0.40, "rgba(92,70,190,0.45)"],
            [0.50, "rgba(10,12,24,0.0)"],
            [0.60, "rgba(18,116,88,0.45)"],
            [0.82, "rgba(34,200,150,0.92)"],
            [1.00, "#3ef2ae"],
        ],
    ),
    "solar": dict(
        label="Solar",
        neg="#ff5249", pos="#ffb340", accent="#ffd08a",
        scale=[
            [0.00, "#ff5249"],
            [0.18, "rgba(214,62,52,0.92)"],
            [0.40, "rgba(140,42,34,0.45)"],
            [0.50, "rgba(10,12,24,0.0)"],
            [0.60, "rgba(150,86,18,0.45)"],
            [0.82, "rgba(232,142,40,0.92)"],
            [1.00, "#ffb340"],
        ],
    ),
    "ion": dict(
        label="Ion",
        neg="#ff6b63", pos="#3ddc84", accent="#86ffc2",
        scale=[
            [0.00, "#ff6b63"],
            [0.18, "rgba(220,70,62,0.92)"],
            [0.40, "rgba(142,44,40,0.45)"],
            [0.50, "rgba(10,12,24,0.0)"],
            [0.60, "rgba(22,120,70,0.45)"],
            [0.82, "rgba(40,196,116,0.92)"],
            [1.00, "#3ddc84"],
        ],
    ),
}
DEFAULT_PALETTE = "nebula"


def _palette(key: str) -> dict:
    return PALETTES.get(key, PALETTES[DEFAULT_PALETTE])


def _hex_to_rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


MODE_LABELS = {
    "gex": "GEX",
    "gex_norm": "GEX · √T",
    "vex": "VEX",
    "color": "Color (∂Γ/∂t)",
}

# One-line explanation of each view mode — shown under the control deck
# and in the /learn page. Palette-neutral wording (colors are themeable).
MODE_BLURBS = {
    "gex": "Raw dealer gamma exposure — where dealers will hedge as price moves. Bright positive cells pin price; bright negative cells repel it.",
    "gex_norm": "GEX scaled by √T to even out 0DTE dominance. Use in the morning to see longer-dated structure.",
    "vex": "Vanna exposure — dealer hedges driven by volatility changes. Aligns with GEX on conviction days, fights it on whipsaw days.",
    "color": "∂Γ/∂t — rate of gamma growth into expiry. Spikes mark strikes about to become magnetic into the close.",
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
    (x_idx, y_idx) — the Star Node viewfinder reticle — with a soft
    glow pass underneath so it reads on glass.

    With type='category' axes, the cell at column index N spans from N-0.5
    to N+0.5 in plot coordinates.
    """
    corners = [
        (x_idx - half, y_idx - half, +1, +1),   # top-left
        (x_idx + half, y_idx - half, -1, +1),   # top-right
        (x_idx - half, y_idx + half, +1, -1),   # bottom-left
        (x_idx + half, y_idx + half, -1, -1),   # bottom-right
    ]
    glow = _hex_to_rgba(color, 0.30) if color.startswith("#") else color
    add_shape_kwargs = {}
    if row is not None and col is not None:
        add_shape_kwargs = {"row": row, "col": col}
    # glow pass first (wide, faint), crisp pass on top
    for line_style in (dict(color=glow, width=width * 3), dict(color=color, width=width)):
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


def _color_transform(mat: np.ndarray) -> np.ndarray:
    """
    Signed cube-root transform applied to the z-matrix for coloring only.

    Dealer GEX spans 5-6 orders of magnitude within one grid (from ~$100
    for deep-OTM 1DTE strikes up to ~$2B at ATM pins). On a linear scale,
    ATM outliers force the color ramp wide enough that anything below 1%
    of vmax renders invisible — half the grid looks empty even though
    every cell has real populated data.

    Taking sign(v) * |v|^(1/3) compresses the 1,000,000:1 raw dynamic
    range into a 100:1 color range, so a $400K cell shows ~10% of the
    intensity of a $400B cell instead of 0.001%. The sign is preserved
    so positive/negative coloring still works.

    Text labels continue to show real dollar values — only the color
    mapping is transformed.
    """
    sign = np.sign(mat)
    return sign * np.power(np.abs(mat), 1.0 / 3.0)


def _compute_color_scale(mat: np.ndarray, mode: str) -> float:
    """
    Symmetric color-scale half-range for the transformed GEX matrix.

    `mat` here is expected to be the output of `_color_transform` — the
    cube-root-compressed matrix, not raw dollars. We clamp against the
    90th percentile of the transformed values so ATM outliers still peg
    the top of the scale but don't drag the bottom into invisibility.
    """
    nz = np.abs(mat[mat != 0])
    if nz.size == 0:
        return 1.0
    if mode == "color":
        vmax = float(np.percentile(nz, 80))
    else:
        vmax = float(np.percentile(nz, 90))
    return vmax if vmax > 0 else 1.0


def _fmt_cell(v: float) -> str:
    """
    Format one heatmap cell label.

    Four tiers:
        >= $1M       "$12.3M"
        >= $0.5K     "$620K"
        0 < |v| < $0.5K   "·"   (populated cell, dollar value sub-$500)
        v == 0       ""        (truly empty cell — stays bare glass)

    The combination of (a) the cube-root color transform on the z-matrix
    and (b) the '·' marker ensures tiny-but-real cells both tint and
    label themselves visibly, while genuinely-empty cells stay clear.
    """
    if v == 0:
        return ""
    if abs(v) < 0.5:
        return "·"
    if abs(v) >= 1000:
        return f"${v / 1000:,.1f}M"
    return f"${v:,.0f}K"


def _hoverlabel() -> dict:
    return dict(
        bgcolor="rgba(12,15,26,0.94)",
        bordercolor=EDGE_BRIGHT,
        font=dict(family=MONO, size=12, color="#eaf2ff"),
    )


# ── Adaptive cell-label color ───────────────────────────────────────
# A go.Heatmap's textfont.color takes only ONE color in Plotly 6.x (a 2D
# array is rejected on validation), and near-white labels wash out on the
# bright tiles of the lighter palettes (Solar amber, the cyan/emerald
# positive ends). So we render the dollar labels ourselves as annotations,
# choosing dark ink on bright tiles and starlight on faint ones — readable
# contrast on every palette. (Vijay: "white text on some styles is hard to
# read.")
_TEXT_DARK = "#0a1420"
_TEXT_LIGHT = "rgba(234,242,255,0.95)"


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _label_color(v: float, vmax: float, pos_lum: float, neg_lum: float) -> str:
    """Dark ink once the displayed tile is light enough to wash out white
    text. A cell's tile ≈ the palette's pos/neg endpoint at an intensity
    ∝ |v|/vmax over the near-black board, so its effective luminance ≈
    endpoint_luminance × intensity; crossing ~0.5 flips to dark ink."""
    intensity = min(abs(v) / vmax, 1.0) if vmax > 0 else 0.0
    eff = (pos_lum if v > 0 else neg_lum) * intensity
    return _TEXT_DARK if eff >= 0.5 else _TEXT_LIGHT


def _add_cell_labels(fig, text_grid, z_color, vmax, x_labels, y_labels,
                     pal, size=10.5, row=None, col=None) -> None:
    """Render per-cell dollar labels as ONE scatter-text trace with per-point
    adaptive contrast — dark ink on bright tiles, starlight on faint ones.

    Why a single trace and not annotations: a full board is ~276 cells (ORION
    ~680), and re-drawing that many layout annotations on every 5s poll lags
    the whole site (it's what made ORION crawl). A single Scatter(mode="text")
    is batch-rendered — Scatter's textfont.color takes a per-point array
    (heatmap's does not), so we keep the adaptive coloring for free.

    Positions use the category LABEL strings (x_labels[j], y_labels[i]) — a
    scatter trace matches its x/y against the axis's category NAMES, so the
    labels must be the same strings the heatmap defined. (Passing integer
    indices here instead makes Plotly append "0","1",… as brand-new columns —
    the opposite of how annotations behave, which take the index.) cliponaxis
    keeps labels inside the plot under zoom."""
    pos_lum, neg_lum = _luminance(pal["pos"]), _luminance(pal["neg"])
    xs, ys, texts, colors = [], [], [], []
    for i in range(len(y_labels)):
        for j in range(len(x_labels)):
            txt = text_grid[i][j]
            if not txt:
                continue
            xs.append(x_labels[j])
            ys.append(y_labels[i])
            texts.append(txt)
            colors.append(_label_color(z_color[i, j], vmax, pos_lum, neg_lum))
    if not xs:
        return
    extra = dict(row=row, col=col) if row is not None else {}
    fig.add_trace(
        go.Scatter(
            x=xs, y=ys, mode="text", text=texts,
            textfont=dict(family=MONO, size=size, color=colors),
            hoverinfo="skip", showlegend=False, cliponaxis=True,
        ),
        **extra,
    )


def _empty_figure(message: str = "PRIMING CACHE — first data in ~15s") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        xref="paper", yref="paper", x=0.5, y=0.5,
        showarrow=False,
        font=dict(family=MONO, size=13, color=INK_3),
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        height=660,
        margin=dict(l=20, r=20, t=20, b=20),
    )
    return fig


def _build_heatmap_figure(
    grid: GEXGrid,
    nodes: NodeMap,
    mode: str = "gex",
    palette: str = DEFAULT_PALETTE,
) -> go.Figure:
    pal = _palette(palette)
    if grid is None or not grid.cells:
        return _empty_figure()

    mat, strikes, expiries = grid.as_matrix(mode)

    # Trim to ±3% window around spot for readability. VIX is exempt: its
    # feed already applies the wide upward window (spot→~3.5×) that captures
    # the far-OTM crash-hedge call walls — a ±3% trim would gut it.
    spot = grid.spot
    if grid.ticker != "VIX":
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

    # Apply the cube-root color transform to the z-matrix so tiny cells
    # still show visible color. Text labels keep the real dollar values.
    z_color = _color_transform(mat)
    vmax = _compute_color_scale(z_color, mode)

    text_grid = [[_fmt_cell(mat[i, j]) for j in range(mat.shape[1])]
                 for i in range(mat.shape[0])]

    # Custom hover template with real dollar values from the untransformed
    # matrix via customdata (z is the transformed colorspace value).
    customdata = mat  # raw dollars, for hover display

    mode_label = MODE_LABELS.get(mode, mode.upper())

    heat = go.Heatmap(
        z=z_color,
        customdata=customdata,
        x=expiry_labels,
        y=strike_labels,
        zmin=-vmax,
        zmax=vmax,
        colorscale=pal["scale"],
        colorbar=dict(
            title=dict(
                text=f"{mode_label}",
                side="right",
                font=dict(family=FONT, size=10, color=INK_3),
            ),
            thickness=8,
            len=0.72,
            outlinewidth=0,
            tickfont=dict(family=MONO, size=9, color=INK_3),
            bgcolor="rgba(0,0,0,0)",
            # Hide tick labels on the transformed scale — they're meaningless
            # (cube-root dollars) and the text labels on cells carry the
            # actual values.
            showticklabels=False,
        ),
        hovertemplate=(
            "STRIKE  %{y}<br>"
            "EXPIRY  %{x}<br>"
            f"{mode_label.upper()}    $%{{customdata:,.1f}}K"
            "<extra></extra>"
        ),
        xgap=2.5,
        ygap=2.5,
    )

    fig = go.Figure(data=[heat])

    # Cell labels as adaptive-contrast annotations (see _add_cell_labels).
    _add_cell_labels(fig, text_grid, z_color, vmax,
                     expiry_labels, strike_labels, pal, size=10.5)

    # Star Node marker — glowing bracketed corners (viewfinder reticle)
    if nodes and nodes.sirius is not None:
        sx_label = _format_exp(nodes.sirius.expiry)
        sy_label = f"{nodes.sirius.strike:g}"
        if sx_label in expiry_labels and sy_label in strike_labels:
            x_idx = expiry_labels.index(sx_label)
            y_idx = strike_labels.index(sy_label)
            _add_bracket_corners(fig, x_idx, y_idx, pal["accent"])
            # Invisible scatter marker for hover info
            fig.add_trace(
                go.Scatter(
                    x=[sx_label],
                    y=[sy_label],
                    mode="markers",
                    marker=dict(size=30, color="rgba(0,0,0,0)"),
                    name="Star Node",
                    showlegend=False,
                    hovertemplate=(
                        f"<b>★ STAR NODE</b><br>"
                        f"STRIKE  {nodes.sirius.strike}<br>"
                        f"EXPIRY  {nodes.sirius.expiry}<br>"
                        f"VALUE   ${nodes.sirius.value:,.0f}K"
                        f"<extra></extra>"
                    ),
                )
            )

    # Spot price horizontal reference line — starlight white
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
            line=dict(color="rgba(255,255,255,0.85)", width=1.2, dash="dot"),
        )
        fig.add_annotation(
            xref="paper", yref="y",
            x=1, y=spot_strike_idx,
            text=f"SPOT {grid.spot:.2f}",
            showarrow=False,
            xanchor="right",
            yanchor="bottom",
            font=dict(color="#ffffff", size=10, family=MONO),
            bgcolor="rgba(255,255,255,0.10)",
            bordercolor=EDGE_BRIGHT,
            borderwidth=1,
            borderpad=3,
        )

    fig.update_layout(
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=_hoverlabel(),
        xaxis=dict(
            title="",
            tickangle=0,
            type="category",
            side="top",
            tickfont=dict(size=11, color=INK_2, family=FONT),
            showgrid=False,
            showline=False,
            zeroline=False,
        ),
        yaxis=dict(
            title="",
            autorange="reversed",
            type="category",
            tickfont=dict(size=10.5, color=INK_2, family=MONO),
            showgrid=False,
            showline=False,
            zeroline=False,
        ),
        height=660,
        margin=dict(l=64, r=86, t=44, b=16),
        # Persist the viewer's zoom/pan across the 5s poll refresh. Keyed to
        # ticker so switching instrument resets the view, but a refresh of the
        # SAME ticker keeps you zoomed in (Vijay: "every refresh zooms me
        # out"). Mode/palette changes keep the zoom — same axes.
        uirevision=grid.ticker,
    )
    return fig


def _build_orion_figure(cache, mode: str = "gex", palette: str = DEFAULT_PALETTE) -> go.Figure:
    from plotly.subplots import make_subplots

    pal = _palette(palette)
    orion_tickers = ["SPY", "SPX", "QQQ", "NVDA", "VIX"]
    fig = make_subplots(
        rows=1,
        cols=len(orion_tickers),
        subplot_titles=orion_tickers,
        horizontal_spacing=0.028,
    )

    def _orion_format_exp(e: str) -> str:
        try:
            return datetime.fromisoformat(e).strftime("%b %-d")
        except Exception:
            return e

    for idx, tkr in enumerate(orion_tickers, start=1):
        grid = cache.get_grid(tkr)
        nodes = cache.get_nodes(tkr)
        if grid is None or not grid.cells:
            continue
        mat, strikes, expiries = grid.as_matrix(mode)

        # Trim ±3% strike window (VIX exempt — its feed already windows wide
        # for the far-OTM crash-hedge call walls).
        spot = grid.spot
        if tkr != "VIX":
            lo, hi = spot * 0.97, spot * 1.03
            keep = [i for i, s in enumerate(strikes) if lo <= s <= hi]
            if keep:
                strikes = [strikes[i] for i in keep]
                mat = mat[keep, :]

        # Limit expiries — 5 panels are narrow, keep the front 4
        if len(expiries) > 4:
            expiries = expiries[:4]
            mat = mat[:, :4]

        exp_labels = [_orion_format_exp(e) for e in expiries]
        strike_labels = [f"{s:g}" for s in strikes]

        # Same cube-root color transform as the single-ticker view —
        # text labels keep raw dollars, z carries compressed magnitudes.
        z_color = _color_transform(mat)
        vmax = _compute_color_scale(z_color, mode)

        text_grid = [[_fmt_cell(mat[i, j]) for j in range(mat.shape[1])]
                     for i in range(mat.shape[0])]

        fig.add_trace(
            go.Heatmap(
                z=z_color,
                customdata=mat,
                x=exp_labels,
                y=strike_labels,
                zmin=-vmax, zmax=vmax,
                colorscale=pal["scale"],
                showscale=(idx == 3),
                colorbar=(
                    dict(thickness=8, len=0.7, outlinewidth=0,
                         showticklabels=False, bgcolor="rgba(0,0,0,0)")
                    if idx == 3 else None
                ),
                xgap=2, ygap=2,
                hovertemplate=f"{tkr}<br>Strike %{{y}}<br>Expiry %{{x}}<br>{MODE_LABELS.get(mode, mode.upper())} $%{{customdata:.0f}}k<extra></extra>",
            ),
            row=1, col=idx,
        )
        # Adaptive-contrast cell labels for this subplot (added AFTER the
        # subplot-title annotations so the title-styling loop below can skip
        # them by text).
        _add_cell_labels(fig, text_grid, z_color, vmax,
                         exp_labels, strike_labels, pal, size=7,
                         row=1, col=idx)
        if nodes and nodes.sirius is not None:
            kx = _orion_format_exp(nodes.sirius.expiry)
            ky = f"{nodes.sirius.strike:g}"
            if kx in exp_labels and ky in strike_labels:
                x_idx = exp_labels.index(kx)
                y_idx = strike_labels.index(ky)
                _add_bracket_corners(
                    fig, x_idx, y_idx, pal["accent"],
                    length=0.22, width=2,
                    row=1, col=idx,
                )

    fig.update_layout(
        showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hoverlabel=_hoverlabel(),
        height=660,
        title=dict(
            text=f"ORION · {MODE_LABELS.get(mode, mode.upper()).upper()}",
            font=dict(family=FONT, size=12, color=INK_3),
            x=0.01,
            y=0.985,
            xanchor="left",
            yanchor="top",
        ),
        # Extra top margin so subplot titles + x-axis labels don't collide
        margin=dict(l=50, r=70, t=96, b=20),
        # Preserve zoom/pan across the poll refresh (item #4).
        uirevision="ORION",
    )
    # Tone subplot titles AND push them above the x-axis tick labels.
    # subplot_titles default to y≈1.0 which collides with side='top' x-ticks;
    # bumping y to >1.0 puts them in the top margin. Filter by text so the
    # per-cell label annotations added above are left untouched.
    for ann in fig.layout.annotations:
        if ann.text not in orion_tickers:
            continue
        ann.font = dict(family=FONT, size=12, color=INK, weight=700)
        ann.y = 1.06   # above the plot area, in the top margin
        ann.yanchor = "bottom"
    for i in range(1, len(orion_tickers) + 1):
        fig.update_xaxes(
            type="category", side="top",
            tickfont=dict(size=8.5, color=INK_2, family=FONT),
            showgrid=False, showline=False,
            row=1, col=i,
        )
        fig.update_yaxes(
            type="category", autorange="reversed",
            tickfont=dict(size=8, color=INK_2, family=MONO),
            showgrid=False, showline=False,
            row=1, col=i,
        )
    return fig


# api/index.py (Vercel demo entry) still imports the pre-rename symbol.
_build_trinity_figure = _build_orion_figure


# ── Star Node lifecycle (freshness) ─────────────────────────────────
_LIFECYCLE_META = {
    "fresh":  ("Fresh",   "Star Node just moved here — unproven. Wait for it "
                          "to hold a few cycles before trusting the level."),
    "charge": ("Charging","∂Γ/∂t is positive — gamma is still building at this "
                          "strike, so the magnet is strengthening into expiry."),
    "held":   ("Held",    "Established Star Node — gamma stable here. A proven, "
                          "settled magnet."),
    "decay":  ("Decaying","∂Γ/∂t has flipped against it — gamma is rolling off, "
                          "so this magnet is weakening and its pull should fade."),
}


def _node_lifecycle(nodes, grid, reshuffle_age):
    """Classify the Star Node's lifecycle from data Polaris already has:
    how long it has held its strike (reshuffle age) and its ∂Γ/∂t (whether
    |gamma| at that cell is still building or rolling off). Directly answers
    Vijay's "can ΔΓ/Δt flag fresh→…→decaying?" — yes.

    Returns (state_key, label, tooltip) or None. NOTE: Skylit's TESTED /
    DELIVERED stages need spot-vs-node *touch* history, which Polaris doesn't
    track yet — that's the documented follow-up; this covers the ∂Γ/∂t-derived
    stages (fresh / charging / held / decaying)."""
    if not (nodes and nodes.sirius and nodes.sirius.significant):
        return None
    star = nodes.sirius
    # Fresh: the Star Node strike just changed — unproven.
    if reshuffle_age is not None and reshuffle_age < 120:
        return ("fresh",) + _LIFECYCLE_META["fresh"]
    # ∂Γ/∂t at the Star Node's own cell.
    color_v = 0.0
    if grid:
        for c in grid.cells:
            if c.strike == star.strike and c.expiry == star.expiry:
                color_v = c.color_value
                break
    # Is |GEX| growing or shrinking? d|GEX|/dt = sign(GEX) · ∂Γ/∂t. Threshold
    # relative to the node's own size so we don't flag noise as a trend.
    signed = color_v if star.value >= 0 else -color_v
    thresh = max(abs(star.value) * 0.04, 1.0)
    if signed >= thresh:
        return ("charge",) + _LIFECYCLE_META["charge"]
    if signed <= -thresh:
        return ("decay",) + _LIFECYCLE_META["decay"]
    return ("held",) + _LIFECYCLE_META["held"]


# ── In-terminal reading guide ───────────────────────────────────────
def _reading_guide():
    """Collapsible legend + 'what to look for' cues, right on the board.
    Simple by design; the deep dive lives in Academy (/learn). Vijay: 'a
    short instruction on how to read some of these maps has to be in
    polaris … simple explanations with deep dives in academy would be
    ideal.'"""
    def item(term, desc):
        return html.Div(className="g-item", children=[
            html.Span(term, className="g-term"),
            html.Span(desc, className="g-desc"),
        ])
    return html.Details(
        className="guide panel glass reveal",
        style={"--d": "0.12s"},
        children=[
            html.Summary(className="g-sum", children=[
                html.Span("How to read this map", className="g-title"),
                html.Span("legend + what to look for", className="g-hint"),
                html.Span("▾", className="g-chev"),
            ]),
            html.Div(className="g-body", children=[
                html.Div(className="g-cols", children=[
                    html.Div(className="g-col", children=[
                        html.Div("THE MAP", className="g-head"),
                        item("Grid", "Strikes stack vertically; expiries run "
                                     "left → right, nearest first."),
                        item("Color", "Brightness = how much dealer gamma sits "
                                      "there. One side pins price, the other "
                                      "repels it — the palette legends the two "
                                      "directions."),
                        item("★ Star Node", "The bracketed cell: the single "
                                            "strongest magnet. Price tends to "
                                            "gravitate here by expiry."),
                        item("Spot line", "The dotted white line is where price "
                                          "is right now. Read the walls above "
                                          "vs below it."),
                    ]),
                    html.Div(className="g-col", children=[
                        html.Div("THE VIEWS", className="g-head"),
                        item("GEX", "Raw dealer gamma — the magnets, as they "
                                    "are."),
                        item("GEX·√T", "Evens out 0DTE dominance so longer-dated "
                                       "structure shows. Use it in the morning."),
                        item("VEX", "Vanna — vol-driven hedging. Confirms GEX on "
                                    "trend days, fights it on chop."),
                        item("ΔΓ/Δt", "Rate of change — which strikes are "
                                      "charging UP into the close. A quiet "
                                      "strike lighting up here is turning "
                                      "magnetic."),
                    ]),
                    html.Div(className="g-col g-col-wide", children=[
                        html.Div("WHAT TO LOOK FOR", className="g-head"),
                        html.Ul(className="g-cues", children=[
                            html.Li("Bright same-side walls just above AND below "
                                    "spot → price boxed in; expect a pinned, "
                                    "range-bound day."),
                            html.Li("Star Node sitting away from spot with a "
                                    "clear path → a magnet pulling price toward "
                                    "it."),
                            html.Li("A sign flip right at spot → the "
                                    "acceleration zone; momentum tends to run "
                                    "if price breaks through."),
                            html.Li("ΔΓ/Δt lighting up a strike that was quiet → "
                                    "it's turning magnetic into the close. "
                                    "Watch it."),
                            html.Li("A 'Fresh' Star Node state → it just moved. "
                                    "Don't trust the new level until it holds."),
                        ]),
                    ]),
                ]),
                html.A("Full deep dive → Academy", href="/learn",
                       target="_blank", className="g-more"),
            ]),
        ],
    )


# --------------- App layout ---------------

def create_app(cache, tickers: list[str]) -> Dash:
    # Assets folder is at project root, not next to this script
    assets_path = str(Path(__file__).resolve().parents[1] / "assets")
    app = Dash(__name__, title="Polaris", assets_folder=assets_path)

    app.index_string = """<!DOCTYPE html>
<html lang="en">
    <head>
        {%metas%}
        <title>{%title%}</title>
        <meta name="theme-color" content="#04060d">
        <link rel="icon" type="image/svg+xml" href="/assets/star.svg">
        {%favicon%}
        {%css%}
    </head>
    <body>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>"""

    # Auth gate — login page before Dash loads. Must come before
    # register_learn_route so /login takes priority.
    from .auth import register_auth
    register_auth(app.server)

    register_learn_route(app.server)

    # Reusable builders ---------------------------------------------
    def _chip(label, value, value_style=None, pulse=False, dim=False):
        cls = "chip glass-soft" + (" chip-pulse" if pulse else "")
        val_cls = "c-val" + (" dim" if dim else "")
        return html.Div(
            className=cls,
            children=[
                html.Div(label, className="c-label"),
                html.Div(value, className=val_cls, style=value_style or {}),
            ],
        )

    def _seg(id_, options, value, extra_class="", label=""):
        return html.Div(
            className="ctl",
            children=[
                html.Span(label, className="ctl-label"),
                dcc.RadioItems(
                    id=id_,
                    options=options,
                    value=value,
                    className=f"seg {extra_class}".strip(),
                    labelClassName="seg-btn",
                    inputClassName="seg-input",
                ),
            ],
        )

    palette_options = [
        {
            "label": html.Span(
                [
                    html.Span(
                        className="pal-dot",
                        style={
                            "background": (
                                f"linear-gradient(135deg, {p['neg']}, {p['pos']})"
                            ),
                        },
                    ),
                    html.Span(p["label"]),
                ],
                style={"display": "inline-flex", "alignItems": "center", "gap": "7px"},
            ),
            "value": key,
        }
        for key, p in PALETTES.items()
    ]

    app.layout = html.Div(
        className="app-root",
        children=[
            # ═══ ATMOSPHERE ═══
            html.Div(
                className="aurora",
                children=[
                    html.Div(className="orb orb-polar"),
                    html.Div(className="orb orb-violet"),
                    html.Div(className="orb orb-rose"),
                ],
            ),
            html.Div(className="stars"),
            html.Div(className="stars-b"),
            html.Div(className="grain"),

            # ═══ FLOATING NAV ═══
            html.Header(
                className="nav glass",
                children=[
                    html.Div(
                        className="nav-brand",
                        children=[
                            html.Img(src="/assets/star.svg", alt=""),
                            html.Span("POLARIS", className="wordmark"),
                            html.Span("Dealer GEX", className="nav-sub"),
                        ],
                    ),
                    html.Div(
                        className="nav-right",
                        children=[
                            html.Div(id="freshness-badge", className="nav-status"),
                            html.A("Learn", href="/learn", target="_blank",
                                   className="nav-link"),
                            html.A("Sign out", href="/logout",
                                   className="nav-link nav-link-dim"),
                        ],
                    ),
                ],
            ),

            # ═══ SHELL ═══
            html.Main(
                className="shell",
                children=[
                    # Stale banner (only when stale/offline)
                    html.Div(id="stale-banner"),

                    # Control deck
                    html.Section(
                        className="panel glass deck reveal",
                        style={"--d": "0.08s"},
                        children=[
                            html.Div(
                                className="deck-row",
                                children=[
                                    _seg(
                                        "ticker-select",
                                        [{"label": t, "value": t} for t in tickers]
                                        + [{"label": "ORION", "value": "ORION"}],
                                        "SPY",
                                        extra_class="seg-scroll",
                                        label="Ticker",
                                    ),
                                    _seg(
                                        "mode-select",
                                        [
                                            {"label": "GEX", "value": "gex"},
                                            {"label": "GEX·√T", "value": "gex_norm"},
                                            {"label": "VEX", "value": "vex"},
                                            {"label": "ΔΓ/Δt", "value": "color"},
                                        ],
                                        "gex",
                                        label="View",
                                    ),
                                    _seg(
                                        "palette-select",
                                        palette_options,
                                        DEFAULT_PALETTE,
                                        label="Palette",
                                    ),
                                ],
                            ),
                            html.Div(id="mode-blurb", className="deck-blurb"),
                        ],
                    ),

                    # How-to-read guide (collapsible; deep dive → /learn)
                    _reading_guide(),

                    # Stat chips
                    html.Div(
                        id="header-cells",
                        className="stat-chips reveal",
                        style={"--d": "0.16s"},
                    ),

                    # Heatmap board
                    html.Section(
                        className="panel glass board reveal",
                        style={"--d": "0.24s"},
                        children=[
                            dcc.Graph(
                                id="heatmap-graph",
                                className="board-graph",
                                config={
                                    "displaylogo": False,
                                    "displayModeBar": "hover",
                                    "modeBarButtonsToRemove": [
                                        "lasso2d", "select2d", "autoScale2d",
                                    ],
                                },
                            ),
                            html.Div(id="node-summary", className="node-row"),
                        ],
                    ),
                ],
            ),

            # Palette persistence (localStorage) + poll
            dcc.Store(id="palette-store", storage_type="local"),
            dcc.Interval(id="poll", interval=app_config.DASHBOARD_POLL * 1000, n_intervals=0),
        ],
    )

    _DOT_CLASS = {
        FreshnessState.LIVE: "live-dot",
        FreshnessState.LAGGING: "live-dot lagging",
        FreshnessState.STALE: "live-dot stale",
        FreshnessState.OFFLINE: "live-dot offline",
    }

    def _build_freshness_badge(status, source=None):
        """Pulsing dot + state + age, in the floating nav — plus an explicit
        disclosure pill whenever data is CBOE, so a LIVE badge can't quietly
        mean delayed data. (CBOE is the standard member-facing feed.)"""
        age = ""
        if status.age_seconds is not None:
            age = status.message.split("updated ")[-1].split(" — ")[0] \
                if "updated" in status.message else ""
        badge = [
            html.Span(className=_DOT_CLASS[status.state]),
            html.Span(status.label.title(), style={"fontWeight": 600, "color": status.color}),
            html.Span(age, className="age"),
        ]
        if source == "cboe":
            badge.append(html.Span(
                "CBOE · 15m delayed",
                className="src-pill",
                title=("Polaris runs on CBOE delayed data. Read the map for "
                       "levels; trigger from live price on your own chart."),
            ))
        return badge

    def _build_stale_banner(status):
        """Full-width glass stripe for STALE / OFFLINE states."""
        if status.state in (FreshnessState.LIVE, FreshnessState.LAGGING):
            return None
        cls = "banner banner-stale" if status.state == FreshnessState.STALE \
            else "banner banner-offline"
        return html.Div(className=cls, children=status.message)

    def _build_header_cells(grid, nodes, reshuffle_age, palette=DEFAULT_PALETTE):
        """The stat-chip row: ticker, spot, Star Node, value, updated."""
        pal = _palette(palette)
        if grid is None:
            return [
                _chip("Spot", "—", dim=True),
                _chip("Star Node", "—", dim=True),
                _chip("Updated", "—", dim=True),
            ]
        spot_str = f"${grid.spot:,.2f}"

        star = nodes.sirius if nodes else None
        is_reshuffled = reshuffle_age is not None and reshuffle_age < 120  # 2 min window

        chips = [
            _chip("Ticker", grid.ticker, value_style={"color": POLAR}),
            _chip("Spot", spot_str, value_style={"color": CYAN}),
        ]
        if star is None:
            chips.append(_chip("Star Node", "—", dim=True))
        elif not star.significant:
            # No clear leader — magnitude gap too thin to trust
            chips.append(_chip("Star Node", f"{star.strike:g}", dim=True))
            chips.append(_chip("Star Value", "no clear leader", dim=True))
        else:
            chips.append(_chip(
                "Star Node", f"{star.strike:g}",
                value_style={"color": pal["accent"]},
            ))
            chips.append(_chip(
                "Star Value", f"${star.value:+,.0f}K",
                value_style={"color": pal["pos"] if star.value > 0 else pal["neg"]},
            ))
        # Reshuffle flag — only show if recently changed
        if is_reshuffled:
            chips.append(_chip(
                "Reshuffled", f"{int(reshuffle_age)}s ago",
                value_style={"color": "#ffb340"}, pulse=True,
            ))
        ts_str = datetime.fromtimestamp(grid.timestamp).strftime("%H:%M:%S")
        chips.append(_chip("Updated", ts_str, value_style={"color": INK_2}))
        return chips

    def _build_node_row(grid, nodes, mode, ticker, reshuffle_age=None,
                        palette=DEFAULT_PALETTE):
        """Bottom-of-board pill row: Star Node, value, reshuffle, gatekeepers."""
        pal = _palette(palette)
        parts = [
            html.Span(
                className="np",
                children=[
                    html.Span("Mode", className="np-label"),
                    html.B(MODE_LABELS.get(mode, mode).upper()),
                ],
            ),
        ]
        if nodes and nodes.sirius:
            star = nodes.sirius
            if not star.significant:
                parts.append(html.Span(
                    className="np",
                    children=[
                        html.Span("★ Star Node", className="np-label"),
                        html.B(f"{star.strike:g}"),
                        html.Span("no clear leader", style={"fontStyle": "italic"}),
                    ],
                ))
            else:
                parts.append(html.Span(
                    className="np np-star",
                    children=[
                        html.Span("★ Star Node", className="np-label",
                                  style={"color": pal["accent"]}),
                        html.B(f"{star.strike:g}"),
                        html.Span(f"@ {star.expiry}"),
                        html.B(
                            f"{star.value:+,.0f}K",
                            style={"color": pal["pos"] if star.value > 0 else pal["neg"]},
                        ),
                    ],
                ))
            if reshuffle_age is not None and reshuffle_age < 120:
                parts.append(html.Span(
                    className="np np-warn",
                    children=f"⚠ Reshuffled {int(reshuffle_age)}s ago",
                ))
            # Lifecycle state (fresh / charging / held / decaying) from ∂Γ/∂t.
            life = _node_lifecycle(nodes, grid, reshuffle_age)
            if life:
                state_key, state_label, state_tip = life
                parts.append(html.Span(
                    className=f"np np-life np-life-{state_key}",
                    title=state_tip,
                    children=[
                        html.Span("State", className="np-label"),
                        html.B(state_label),
                    ],
                ))
        if nodes and nodes.gatekeepers and (not nodes.sirius or nodes.sirius.significant):
            gk_children = [html.Span("Gatekeepers", className="np-label")]
            for g in nodes.gatekeepers[:3]:
                col = pal["pos"] if g.value > 0 else pal["neg"]
                gk_children.append(html.B(f"{g.strike:g}"))
                gk_children.append(html.Span(
                    f"{g.value:+,.0f}K", style={"color": col},
                ))
            parts.append(html.Span(className="np", children=gk_children))
        if not (nodes and (nodes.sirius or nodes.gatekeepers)):
            parts.append(html.Span(className="np", children="awaiting data…"))
        return parts

    # ── Palette persistence: localStorage ↔ control sync ───────────
    @app.callback(
        Output("palette-select", "value"),
        Input("palette-store", "data"),
    )
    def _restore_palette(saved):
        return saved if saved in PALETTES else DEFAULT_PALETTE

    @app.callback(
        Output("palette-store", "data"),
        Input("palette-select", "value"),
        prevent_initial_call=True,
    )
    def _persist_palette(value):
        return value

    @app.callback(
        [
            Output("heatmap-graph", "figure"),
            Output("freshness-badge", "children"),
            Output("stale-banner", "children"),
            Output("header-cells", "children"),
            Output("node-summary", "children"),
            Output("mode-blurb", "children"),
        ],
        [
            Input("poll", "n_intervals"),
            Input("ticker-select", "value"),
            Input("mode-select", "value"),
            Input("palette-select", "value"),
        ],
    )
    def _update(_n, ticker, mode, palette):
        latest_ts = latest_cache_timestamp(cache)
        status = evaluate_freshness(latest_ts)
        banner = _build_stale_banner(status)
        blurb = MODE_BLURBS.get(mode, "")

        if ticker == "ORION":
            fig = _build_orion_figure(cache, mode, palette)
            # Use first available ticker for header info in Orion mode
            for t in ("SPY", "SPX", "QQQ", "NVDA", "VIX"):
                grid = cache.get_grid(t)
                nodes = cache.get_nodes(t)
                if grid is not None:
                    break
            badge = _build_freshness_badge(status, cache.get_source(t))
            reshuffle_age = cache.sirius_reshuffle_age(t) if grid else None
            header = _build_header_cells(grid, nodes, reshuffle_age, palette)
            node_row = _build_node_row(grid, nodes, mode, "ORION",
                                       reshuffle_age, palette)
            return fig, badge, banner, header, node_row, blurb

        grid = cache.get_grid(ticker)
        nodes = cache.get_nodes(ticker)
        badge = _build_freshness_badge(status, cache.get_source(ticker))
        reshuffle_age = cache.sirius_reshuffle_age(ticker)
        fig = _build_heatmap_figure(grid, nodes, mode, palette)
        header = _build_header_cells(grid, nodes, reshuffle_age, palette)
        node_row = _build_node_row(grid, nodes, mode, ticker,
                                   reshuffle_age, palette)

        return fig, badge, banner, header, node_row, blurb

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--synthetic", action="store_true", help="Use synthetic feed")
    parser.add_argument("--cboe", action="store_true",
                        help="CBOE delayed feed — the member-facing mode (no LSEG)")
    parser.add_argument("--lseg", action="store_true",
                        help="LSEG feed w/ CBOE fallback — Ayush's PERSONAL use only "
                             "(single-user license; never run this for members)")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--no-db", action="store_true", help="Disable SQLite writer")
    args = parser.parse_args()

    if not args.synthetic and not args.lseg and not args.cboe:
        args.synthetic = True  # default (dev)

    mode = "synthetic" if args.synthetic else ("cboe" if args.cboe else "lseg")
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
