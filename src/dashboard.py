"""
Plotly Dash dashboard for the HeatSeeker replica.

Liquid-glass UI (Astraios house language — Polaris runs cold/celestial):
    • Floating glass nav: brand, freshness, learn/sign-out
    • Control deck: ticker rail, view mode
    • Stat chips: spot, Star Node, value, reshuffle, updated
    • Glass heatmap board: strikes (y) × expiries (x), transparent tiles
    • Strike tape: hover a cell for its history and rate of change
      (assets/tape.js, fed by cell_history off the snapshot store)
    • Refresh: dcc.Interval polling memory cache every 5s

Run:
    python3 -m src.dashboard --synthetic
    python3 -m src.dashboard --lseg       (Milestone 2)
"""
from __future__ import annotations

import argparse
import os
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
    from src.cell_history import (build_tape, cell_key, latest_snapshot,
                                  quantize)
    from src.compute_loop import ComputeLoop
    from src.data_feed import make_feed
    from src.flow_confidence import assess_confidence, sirius_confidence
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
    from .cell_history import (build_tape, cell_key, latest_snapshot,
                               quantize)
    from .compute_loop import ComputeLoop
    from .data_feed import make_feed
    from .flow_confidence import assess_confidence, sirius_confidence
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
#  HEATMAP PALETTE — Nebula, and only Nebula.
#  Diverging scale: index 0 = strongest NEGATIVE GEX, 1.0 = strongest
#  POSITIVE. Zero is fully transparent so empty cells read as bare
#  glass and the populated tiles appear to float ("liquid" signature).
#
#  This used to be a four-way picker (Aurora / Solar / Ion alongside it),
#  persisted to localStorage. It is now a constant: one board, one scale,
#  one thing for a reader to learn. Nothing downstream picks a palette, so
#  `pal` is threaded from here rather than from a callback input.
# ════════════════════════════════════════════════════════════════════
# NOTE: the in-terminal copy now names these two colours outright ("cyan
# pins, magenta accelerates" — reading guide + GEX blurb). That is only safe
# because the scale is fixed; if this ever becomes a setting again, that copy
# has to go back to naming the SIGN instead.
PALETTE = dict(
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
)


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
    "gex": "Raw dealer gamma exposure — where dealers will hedge as price moves. Bright cyan cells pin price; bright magenta cells accelerate it.",
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
# bright tiles at either end of the scale. So we render the dollar labels
# ourselves as annotations, choosing dark ink on bright tiles and starlight
# on faint ones — readable contrast at every intensity. (Vijay: "white text
# on some styles is hard to read.")
_TEXT_DARK = "#0a1420"
_TEXT_LIGHT = "rgba(234,242,255,0.95)"


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _label_color(v: float, vmax: float, pos_lum: float, neg_lum: float) -> str:
    """Dark ink once the displayed tile is light enough to wash out white
    text. A cell's tile ≈ the scale's pos/neg endpoint at an intensity
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


def _format_exp(e: str) -> str:
    """Expiry header, compact: 2026-08-26 → "Aug 26"."""
    try:
        return datetime.fromisoformat(e).strftime("%b %-d")
    except Exception:
        return e


def _visible_board(grid: GEXGrid, mode: str, max_expiries: int = 6):
    """The board exactly as drawn: (matrix, strikes, expiries, x/y labels).

    The trim lives here rather than inline in the figure builder because the
    hover tape has to cover the SAME cells the figure shows — two copies of
    this arithmetic would drift apart the first time either window changes.
    ORION passes max_expiries=4: five panels side by side are narrow.
    """
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

    # Limit expiries for readability (Skylit shows 5)
    if len(expiries) > max_expiries:
        expiries = expiries[:max_expiries]
        mat = mat[:, :max_expiries]

    return mat, strikes, expiries, [f"{s:g}" for s in strikes], [
        _format_exp(e) for e in expiries
    ]


def _build_heatmap_figure(
    grid: GEXGrid,
    nodes: NodeMap,
    mode: str = "gex",
    tape: bool = False,
) -> go.Figure:
    pal = PALETTE
    if grid is None or not grid.cells:
        return _empty_figure()

    mat, strikes, expiries, strike_labels, expiry_labels = _visible_board(grid, mode)

    # With the tape on, the strike card is the only thing that appears on
    # hover, so the native tooltips are switched off: hoverinfo="none" draws
    # no label while STILL firing the hover event (unlike "skip", which kills
    # the event and the card with it).
    #
    # The hovertemplates have to go with it. In Plotly.js a hovertemplate
    # OVERRIDES hoverinfo — set both and the label renders anyway, which is
    # how the old tooltips ended up drawing underneath the card. Everything
    # they carried (value, the sign caveat, the Star Node) is in the card.
    _hoverinfo = "none" if tape else None

    def _ht(template):
        return None if tape else template

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
        hoverinfo=_hoverinfo,
        hovertemplate=_ht(
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

    # ── Sign-confidence layer (GEX mode only) ────────────────────────────
    # Flags near-spot, short-dated cells where the textbook call+/put- sign
    # is unreliable (0DTE churn or balanced call/put OI). It does NOT change
    # any value — it only caveats the sign on the cells where Polaris and
    # flow-aware maps disagree. Off for VEX/Color/√T (different metric).
    cmap = assess_confidence(grid) if mode == "gex" else None
    star_trace = None  # built below, added AFTER the ring so its card wins hover

    # Star Node marker — glowing bracketed corners (viewfinder reticle)
    if nodes and nodes.sirius is not None:
        sx_label = _format_exp(nodes.sirius.expiry)
        sy_label = f"{nodes.sirius.strike:g}"
        if sx_label in expiry_labels and sy_label in strike_labels:
            x_idx = expiry_labels.index(sx_label)
            y_idx = strike_labels.index(sy_label)
            _add_bracket_corners(fig, x_idx, y_idx, pal["accent"])
            star_caveat = ""
            if cmap is not None:
                sc = cmap.get(nodes.sirius.strike, nodes.sirius.expiry)
                if sc and sc.is_low:
                    star_caveat = (
                        "<br><b>⚠ SIGN UNCERTAIN</b><br>"
                        + "<br>".join(sc.reasons)
                    )
            # Built here, added LAST (after the ring overlay) so this richer
            # caveated card wins the hover tie at the star cell instead of the
            # ring's generic "SIGN UNCERTAIN" hover.
            star_trace = go.Scatter(
                x=[sx_label],
                y=[sy_label],
                mode="markers",
                marker=dict(size=30, color="rgba(0,0,0,0)"),
                name="Star Node",
                showlegend=False,
                hoverinfo=_hoverinfo,
                hovertemplate=_ht(
                    f"<b>★ STAR NODE</b><br>"
                    f"STRIKE  {nodes.sirius.strike}<br>"
                    f"EXPIRY  {nodes.sirius.expiry}<br>"
                    f"VALUE   ${nodes.sirius.value:,.0f}K"
                    f"{star_caveat}"
                    f"<extra></extra>"
                ),
            )

    # Ring every low-confidence cell so the caveat is visible on the map,
    # not just in the Star Node hover.
    if cmap is not None:
        lx, ly, lhover = [], [], []
        for (strike, expiry), cc in cmap.cells.items():
            if not cc.is_low:
                continue
            xl = _format_exp(expiry)
            yl = f"{strike:g}"
            if xl in expiry_labels and yl in strike_labels:
                lx.append(xl)
                ly.append(yl)
                lhover.append("⚠ SIGN UNCERTAIN<br>" + "<br>".join(cc.reasons))
        if lx:
            fig.add_trace(
                go.Scatter(
                    x=lx,
                    y=ly,
                    mode="markers",
                    marker=dict(
                        symbol="circle-open",
                        size=13,
                        color="rgba(0,0,0,0)",
                        line=dict(color="rgba(255,190,80,0.85)", width=1.4),
                    ),
                    name="sign uncertain",
                    showlegend=False,
                    customdata=lhover,
                    hoverinfo=_hoverinfo,
                    hovertemplate=_ht("%{customdata}<extra></extra>"),
                )
            )

    # Star hover added LAST so its full caveated card sits on top of the ring
    # at the star cell (hovermode="closest" breaks ties to the later trace).
    if star_trace is not None:
        fig.add_trace(star_trace)

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
        # out"). Mode changes keep the zoom — same axes.
        uirevision=grid.ticker,
    )
    return fig


# ── Strike tape ─────────────────────────────────────────────────────
# The hover card. Everything it draws is shipped once per poll and rendered
# in the browser (assets/tape.js), so moving the mouse across the board costs
# ZERO server round-trips — the same reason the visibility heartbeat is
# clientside. A hover-triggered server callback would fire dozens of times a
# minute and put Vercel's Active CPU budget right back where it was.
#
# POLARIS_TAPE=0 falls back to Plotly's native tooltips.
TAPE_ENABLED = os.environ.get("POLARIS_TAPE", "1").strip().lower() not in (
    "0", "false", "no", "off",
)

# HOT / FADING thresholds. These live on the SERVER and ride out in the payload
# rather than sitting as constants in tape.js, so they can be retuned with an
# env var and a restart instead of a JS edit and a redeploy.
#
# Calibrated against real sessions with scripts/tape_calibrate.py rather than
# by eye — 5 days of market hours across SPY/SPX/QQQ put +35% at roughly the
# 95th-99th percentile of 5-minute moves, firing on 0.37-0.55 cells per board.
# Rare enough to mean something, often enough to see. Re-run that script before
# changing these.
ORION_TAPE_POINTS = int(os.environ.get("POLARIS_ORION_TAPE_POINTS", "12"))
# ORION rebuilds its history far less often than the single board. Five boards
# means five reads, and the wide ones (SPX at 92 strikes, VIX at 48) dominate:
# ~1.1s all told, which is not something to spend every flush on the heaviest
# view on the site. Coarsening the memo key to a 5-minute bucket pays that once
# per bucket instead. Nothing is lost in the gap — live values still refresh on
# every poll, and the browser's ring buffer is appending the recent end of the
# series anyway. The DB is there for DEPTH, not recency.
ORION_TAPE_BUCKET = int(os.environ.get("POLARIS_ORION_TAPE_BUCKET", "300"))
TAPE_HOT_PCT = float(os.environ.get("POLARIS_TAPE_HOT_PCT", "35"))
TAPE_HOT_FLOOR = float(os.environ.get("POLARIS_TAPE_HOT_FLOOR", "0.08"))

# The store only gains rows once a flush interval (~69s) but the board polls
# every 15s, so the history read is memoized. The memo is keyed on the store's
# NEWEST ROW, not on a clock: a plain TTL would let the cached series fall up
# to a TTL behind the payload's own timestamp, and the card — which measures
# its lookbacks from that timestamp — would report the 1-minute row as two
# minutes old on every board. One indexed MAX(ts) per poll costs ~1ms and
# keeps the two exactly in step.
_tape_memo: dict[tuple, dict] = {}


def _cell_history(db_path, ticker, mode, strikes, expiries,
                  max_points=None, bucket=0) -> dict:
    """`build_tape`, rebuilt only when the store has something new to say.

    `bucket` coarsens the freshness key to that many seconds, so a caller that
    doesn't need the newest row every time rebuilds on its own slower cadence.

    Never raises: a missing or locked store costs the card its history, not
    the board."""
    try:
        latest = latest_snapshot(db_path, ticker)
    except Exception:
        latest = None
    stamp = latest
    if latest and bucket > 0:
        stamp = (latest // bucket) * bucket
    key = (str(db_path), ticker, mode, strikes[0], strikes[-1],
           len(strikes), tuple(expiries), stamp, max_points)
    hit = _tape_memo.get(key)
    if hit is not None:
        return hit
    try:
        kw = {} if max_points is None else {"max_points": max_points}
        tape = build_tape(db_path, ticker, mode, strikes, expiries,
                          now=latest, **kw)
    except Exception as e:            # sqlite locked, schema drift, …
        print(f"[tape] history unavailable for {ticker}/{mode}: {e}", flush=True)
        tape = {"cells": {}, "stored": False, "reason": "error"}
    _tape_memo[key] = tape
    # The key carries the store's newest row, so a fresh entry lands on every
    # flush. Evict oldest-first (dicts keep insertion order) rather than
    # clearing outright — a wholesale clear would drop the OTHER tickers'
    # tapes too and make the next poll on each of them pay a rebuild.
    while len(_tape_memo) > 24:
        _tape_memo.pop(next(iter(_tape_memo)))
    return tape


def _build_tape_payload(grid: GEXGrid, nodes: NodeMap, mode: str,
                        ticker: str, db_path=None) -> dict:
    """Everything the hover card needs for the whole visible board.

    Live values come from the cache; the history under them comes from
    gex_snapshots. Both are keyed by the LABEL pair Plotly reports on hover
    ("765|Aug 26"), so the browser can go straight from a hover event to a
    card with no lookup table of its own.
    """
    if grid is None or not grid.cells:
        return {"ok": False, "cells": {}}

    mat, strikes, expiries, strike_labels, expiry_labels = _visible_board(grid, mode)
    tape = _cell_history(db_path, ticker, mode, strikes, expiries) if db_path else {}
    hist = tape.get("cells", {})

    cmap = assess_confidence(grid) if mode == "gex" else None
    star = None
    if nodes is not None and nodes.sirius is not None:
        sy, sx = f"{nodes.sirius.strike:g}", _format_exp(nodes.sirius.expiry)
        if sy in strike_labels and sx in expiry_labels:
            star = f"{sy}|{sx}"

    today = datetime.now().date()
    exp_meta = {}
    for e, label in zip(expiries, expiry_labels):
        try:
            dte = max((datetime.fromisoformat(e).date() - today).days, 0)
        except Exception:
            dte = None
        exp_meta[label] = {"iso": e, "dte": dte}

    cells: dict[str, dict] = {}
    vmax = 0.0
    for i, strike in enumerate(strikes):
        for j, expiry in enumerate(expiries):
            v = float(mat[i, j])
            vmax = max(vmax, abs(v))
            entry: dict = {"v": quantize(v)}
            h = hist.get(cell_key(strike, expiry))
            if h:
                entry["s"] = h["s"]      # sparkline values, aligned to tape["t"]
                entry["x"] = h["e"]      # 1h / 4h / 1d anchors
            if cmap is not None:
                cc = cmap.get(strike, expiry)
                if cc is not None and cc.is_low:
                    entry["w"] = list(cc.reasons)
            cells[f"{strike_labels[i]}|{expiry_labels[j]}"] = entry

    return {
        "ok": True,
        "ticker": ticker,
        "mode": mode,
        "modeLabel": MODE_LABELS.get(mode, mode.upper()),
        "spot": round(grid.spot, 4),
        "ts": grid.timestamp,
        "served": int(time.time()),
        "vmax": round(vmax, 2),
        "star": star,
        "exp": exp_meta,
        "cells": cells,
        # History axis — absolute epoch seconds, plus the clock the server was
        # reading when it built them (the browser corrects its own skew
        # against `served` before merging in what it recorded itself).
        "t": tape.get("t", []),
        "lags": tape.get("lags", []),
        "labels": tape.get("labels", []),
        "extTs": tape.get("ext_ts", []),
        "stored": bool(tape.get("stored")),
        "reason": tape.get("reason"),
        # Badge thresholds, server-side so they're tunable without a redeploy.
        "hot": {"pct": TAPE_HOT_PCT, "floor": TAPE_HOT_FLOOR},
    }


ORION_TICKERS = ["SPY", "SPX", "QQQ", "NVDA", "VIX"]


def _build_orion_tape(cache, mode: str, curve_map: dict, db_path=None) -> dict:
    """One tape covering all five ORION boards.

    Cells are keyed "TICKER|strike|expiry" and `curves` maps Plotly's
    curveNumber back to a ticker, which is the only way the card can tell
    SPY 765 from SPX 765 when five panels share one figure.

    The sparkline is deliberately coarser here (ORION_TAPE_POINTS) — this
    envelope carries five boards instead of one, and ORION is already the
    heaviest view on the site. The extended 1h/4h/1d anchors still come
    through at full fidelity; they cost three values per cell, not thirty.
    """
    out: dict[str, dict] = {}
    meta: dict[str, dict] = {}
    t_axis: list[int] = []
    lags: list = []
    labels: list = []
    ext_ts: list = []
    stored = False
    vmax = 0.0
    served = int(time.time())
    today = datetime.now().date()

    for tkr in set(curve_map.values()):
        grid = cache.get_grid(tkr)
        if grid is None or not grid.cells:
            continue
        mat, strikes, expiries, s_labels, e_labels = _visible_board(
            grid, mode, max_expiries=4
        )
        hist = {}
        if db_path:
            tp = _cell_history(db_path, tkr, mode, strikes, expiries,
                               max_points=ORION_TAPE_POINTS,
                               bucket=ORION_TAPE_BUCKET)
            hist = tp.get("cells", {})
            if tp.get("stored") and not t_axis:
                t_axis, lags = tp.get("t", []), tp.get("lags", [])
                labels, ext_ts = tp.get("labels", []), tp.get("ext_ts", [])
                stored = True
        for j, e in enumerate(expiries):
            try:
                dte = max((datetime.fromisoformat(e).date() - today).days, 0)
            except Exception:
                dte = None
            meta[f"{tkr}|{e_labels[j]}"] = {"iso": e, "dte": dte}
        for i, strike in enumerate(strikes):
            for j, expiry in enumerate(expiries):
                v = float(mat[i, j])
                vmax = max(vmax, abs(v))
                entry: dict = {"v": quantize(v)}
                h = hist.get(cell_key(strike, expiry))
                if h:
                    entry["s"], entry["x"] = h["s"], h["e"]
                out[f"{tkr}|{s_labels[i]}|{e_labels[j]}"] = entry

    return {
        "ok": bool(out),
        "ticker": "ORION",
        "mode": mode,
        "modeLabel": MODE_LABELS.get(mode, mode.upper()),
        "ts": int(latest_cache_timestamp(cache) or served),
        "served": served,
        "vmax": round(vmax, 2),
        "star": None,
        "exp": meta,
        "cells": out,
        "curves": {str(k): v for k, v in curve_map.items()},
        "t": t_axis,
        "lags": lags,
        "labels": labels,
        "extTs": ext_ts,
        "stored": stored,
        "reason": None if stored else "no-store",
        "hot": {"pct": TAPE_HOT_PCT, "floor": TAPE_HOT_FLOOR},
    }


def _build_orion_figure(cache, mode: str = "gex", tape: bool = False):
    """Returns (figure, curve_map).

    `curve_map` is {trace index: ticker}. Five boards share one figure and
    Plotly reports only a curveNumber on hover, so the card needs this to know
    whether the "765" it was handed is SPY's or SPX's.
    """
    from plotly.subplots import make_subplots

    pal = PALETTE
    orion_tickers = ORION_TICKERS
    curve_map: dict[int, str] = {}
    _hoverinfo = "none" if tape else None
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
        mat, strikes, expiries, strike_labels, exp_labels = _visible_board(
            grid, mode, max_expiries=4
        )

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
                hoverinfo=_hoverinfo,
                hovertemplate=None if tape else (
                    f"{tkr}<br>Strike %{{y}}<br>Expiry %{{x}}<br>"
                    f"{MODE_LABELS.get(mode, mode.upper())} "
                    f"$%{{customdata:.0f}}k<extra></extra>"
                ),
            ),
            row=1, col=idx,
        )
        curve_map[len(fig.data) - 1] = tkr
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
    return fig, curve_map


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
                        item("Cyan", "Positive gamma. Dealers hedge AGAINST "
                                     "the move here, so price gets pinned. "
                                     "Brighter = more of it."),
                        item("Magenta", "Negative gamma. Dealers hedge WITH the "
                                        "move, so price accelerates through. "
                                        "Brighter = more of it."),
                        item("★ Star Node", "The bracketed cell: the single "
                                            "strongest magnet. Price tends to "
                                            "gravitate here by expiry."),
                        item("Spot line", "The dotted white line is where price "
                                          "is right now. Read the walls above "
                                          "vs below it."),
                        item("⚠ Amber ring", "A near-spot 0DTE cell whose sign "
                                             "is unreliable — churned by same-day "
                                             "flow or split 50/50 calls vs puts. "
                                             "Trust the wall, not its + / − here."),
                        item("Hover a cell", "The strike card: its line over the "
                                             "last half hour, and how far it "
                                             "moved over 1/5/10/15 min and "
                                             "1h/4h/1d. HOT = built fast, "
                                             "FADING = bleeding out, SIGN FLIP = "
                                             "it crossed zero."),
                        item("First hour", "Cells move ~4x faster at the open "
                                           "than into the close, and FADING is "
                                           "common before 9:30 as overnight "
                                           "positioning unwinds. Weigh a badge "
                                           "by when you're seeing it."),
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
                            html.Li("Bright cyan just above AND below spot → "
                                    "price boxed in; expect a pinned, "
                                    "range-bound day."),
                            html.Li("Star Node sitting away from spot with a "
                                    "clear path → a magnet pulling price toward "
                                    "it."),
                            html.Li("Cyan flipping to magenta right at spot → "
                                    "the acceleration zone; momentum tends to "
                                    "run if price breaks through."),
                            html.Li("ΔΓ/Δt lighting up a strike that was quiet → "
                                    "it's turning magnetic into the close. "
                                    "Watch it."),
                            html.Li("A 'Fresh' Star Node state → it just moved. "
                                    "Don't trust the new level until it holds."),
                            html.Li("A big wall that's flat on the tape has been "
                                    "there all session; one marked HOT was just "
                                    "built. Same size on the map, different "
                                    "conviction behind it."),
                        ]),
                    ]),
                ]),
                html.A("Full deep dive → Academy", href="/learn",
                       target="_blank", className="g-more"),
            ]),
        ],
    )


# --------------- App layout ---------------

def create_app(cache, tickers: list[str], gate_auth: bool = True,
               poll_seconds: int | None = None, auth_mode: str = "code",
               db_path=None) -> Dash:
    # The tape reads history straight out of the snapshot store. On the Mac
    # that's the DB the writer thread has been filling since June; serverless
    # has no such file, `cell_history` finds nothing, and the card falls back
    # to what the browser records while you watch.
    db_path = app_config.DB_PATH if db_path is None else db_path
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
    #
    # Two doors, because the two deployments have different problems to solve:
    #   "code"     — the Mac terminal. One shared access code, read from
    #                data/.polaris_access.txt. Fine for a loopback-bound app
    #                only Ayush reaches.
    #   "astraios" — the public cloud terminal. Real members arrive over the
    #                open internet, so they sign in with their Astraios account
    #                (Supabase, same as Meridian) and are checked for live desk
    #                access. A shared code would have to be handed around and
    #                could not be revoked per person.
    # Neither serves anything LSEG-derived; the cloud feed is CBOE-only.
    if gate_auth:
        if auth_mode == "astraios":
            from .astraios_auth import register_astraios_auth
            register_astraios_auth(app.server)
        else:
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
                            # Sign out only exists where there's a login gate.
                            # The public cloud terminal (gate_auth=False) has no
                            # session, so a "Sign out" link would be a dead end.
                            *([html.A("Sign out", href="/logout",
                                      className="nav-link nav-link-dim")]
                              if gate_auth else []),
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
                                clear_on_unhover=True,
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

            # The strike tape: one floating card, drawn by assets/tape.js from
            # `tape-store`. It lives out here rather than inside the board panel
            # because `.panel` clips its overflow — and it is fixed-positioned,
            # so its place in the DOM costs it nothing.
            html.Div(id="cell-tape", className="tape", **{"data-open": "0"}),
            dcc.Store(id="tape-store"),
            dcc.Store(id="tape-sink"),

            dcc.Interval(id="poll",
                         interval=(poll_seconds or app_config.DASHBOARD_POLL) * 1000,
                         n_intervals=0),
            # Browser-only heartbeat (fires a clientside callback, never hits the
            # server) that pauses `poll` while the tab is hidden — a backgrounded
            # cloud terminal shouldn't burn Vercel Active CPU rebuilding a figure
            # nobody's looking at.
            dcc.Interval(id="visbeat", interval=4000, n_intervals=0),
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

    def _build_header_cells(grid, nodes, reshuffle_age):
        """The stat-chip row: ticker, spot, Star Node, value, updated."""
        pal = PALETTE
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

    def _build_node_row(grid, nodes, mode, ticker, reshuffle_age=None):
        """Bottom-of-board pill row: Star Node, value, reshuffle, gatekeepers."""
        pal = PALETTE
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
        [
            Output("heatmap-graph", "figure"),
            Output("freshness-badge", "children"),
            Output("stale-banner", "children"),
            Output("header-cells", "children"),
            Output("node-summary", "children"),
            Output("mode-blurb", "children"),
            Output("tape-store", "data"),
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
        banner = _build_stale_banner(status)
        blurb = MODE_BLURBS.get(mode, "")

        if ticker == "ORION":
            fig, curve_map = _build_orion_figure(cache, mode, tape=TAPE_ENABLED)
            # Use first available ticker for header info in Orion mode
            for t in ("SPY", "SPX", "QQQ", "NVDA", "VIX"):
                grid = cache.get_grid(t)
                nodes = cache.get_nodes(t)
                if grid is not None:
                    break
            badge = _build_freshness_badge(status, cache.get_source(t))
            reshuffle_age = cache.sirius_reshuffle_age(t) if grid else None
            header = _build_header_cells(grid, nodes, reshuffle_age)
            node_row = _build_node_row(grid, nodes, mode, "ORION", reshuffle_age)
            tape = (
                _build_orion_tape(cache, mode, curve_map, db_path)
                if TAPE_ENABLED else {"ok": False}
            )
            return fig, badge, banner, header, node_row, blurb, tape

        grid = cache.get_grid(ticker)
        nodes = cache.get_nodes(ticker)
        badge = _build_freshness_badge(status, cache.get_source(ticker))
        reshuffle_age = cache.sirius_reshuffle_age(ticker)
        fig = _build_heatmap_figure(grid, nodes, mode, tape=TAPE_ENABLED)
        header = _build_header_cells(grid, nodes, reshuffle_age)
        node_row = _build_node_row(grid, nodes, mode, ticker, reshuffle_age)
        tape = (
            _build_tape_payload(grid, nodes, mode, ticker, db_path)
            if TAPE_ENABLED else {"ok": False}
        )

        return fig, badge, banner, header, node_row, blurb, tape

    # Gate the server poll on tab visibility. `visbeat` is a clientside-only
    # interval, so this callback runs in the browser and costs ZERO serverless
    # invocations — it just sets poll.disabled = document.hidden every few
    # seconds. Hidden/backgrounded tab → poll stops → no figure rebuilds → no
    # Active CPU. Tab visible again → visbeat (never disabled) flips it back on.
    app.clientside_callback(
        "function(_n){ return document.hidden; }",
        Output("poll", "disabled"),
        Input("visbeat", "n_intervals"),
    )

    # Strike tape — one clientside callback doing both halves (assets/tape.js):
    #   record — each poll, append the board to a ring buffer, so a terminal
    #            with no snapshot store behind it still grows a tape while you
    #            watch it (deduped on the payload timestamp, so re-running it
    #            on a hover event is free)
    #   render — hover event → card, drawn from data already in the page
    # Neither costs a server round-trip, which is the whole point: hover fires
    # dozens of times a minute, and a server-side hover callback would put
    # Vercel's Active CPU budget right back where it was. `tape-sink` is a
    # sink — the real work is DOM-side, so the callback returns no_update.
    app.clientside_callback(
        "function(hover, tape){ return window.polarisTape.tick(hover, tape); }",
        Output("tape-sink", "data"),
        Input("heatmap-graph", "hoverData"),
        Input("tape-store", "data"),
    )

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
