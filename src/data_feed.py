"""
Data feeds for Polaris.

Three concrete implementations:
    • LSEGOptionsFeed — real LSEG data via refinitiv-data desktop session
    • SyntheticOptionsFeed — Black-Scholes fake chain for off-hours dev
    • ReplayOptionsFeed — loads pickled ChainSnapshots from disk

All return ChainSnapshot ready for the GEX engine.
"""
from __future__ import annotations

import math
import os
import pickle
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, Protocol

from .gex_engine import OptionContract
from .ric_builder import build_option_ric
from .sign_imputation import blended_dealer_sign


@dataclass
class ChainSnapshot:
    ticker: str
    spot: float
    timestamp: int
    contracts: list[OptionContract] = field(default_factory=list)


class OptionsFeed(Protocol):
    def get_chain_snapshot(self, ticker: str) -> ChainSnapshot: ...


# ======================================================================
# Black-Scholes helpers — used by synthetic feed AND by LSEG feed to
# compute gamma/vanna (LSEG returns delta + IV but not gamma/vanna)
# ======================================================================

def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs_gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes gamma (same for calls and puts)."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return _norm_pdf(d1) / (S * sigma * math.sqrt(T))


def bs_vanna(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """Black-Scholes vanna = dDelta/dSigma = dVega/dSpot."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return -_norm_pdf(d1) * d2 / sigma


def _norm_cdf(x: float) -> float:
    # Abramowitz & Stegun approximation, good to ~1e-7
    import math as _m
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x) / _m.sqrt(2)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * _m.exp(-x * x)
    return 0.5 * (1.0 + sign * y)


def implied_vol_from_delta(
    S: float, K: float, T: float, r: float,
    delta: float, option_type: str,
) -> float:
    """
    Back out implied volatility from Black-Scholes delta using Newton's
    method. Fast, robust, and doesn't need the option price at all.

    This is the escape hatch when LSEG returns DELTA but not IMPL_VOL
    (which is what happens on the desktop-feed tier for SPY options).
    """
    import math as _m
    if T <= 0 or S <= 0 or K <= 0 or abs(delta) < 1e-6 or abs(delta) > 0.9999:
        return 0.0
    # For puts, work with positive-equivalent delta
    d = abs(delta) if option_type.upper() == "C" else 1.0 - abs(delta)
    if d <= 1e-6 or d >= 0.9999:
        return 0.0
    # Initial guess from Brenner-Subrahmanyam style
    sigma = 0.20
    for _ in range(50):
        sqrt_T = _m.sqrt(T)
        if sigma <= 0 or sqrt_T == 0:
            return 0.0
        d1 = (_m.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
        nd1 = _norm_cdf(d1)
        # dDelta/dSigma = vega / S  (approximately)
        vega_term = _norm_pdf(d1) * sqrt_T
        if abs(vega_term) < 1e-10:
            break
        diff = nd1 - d
        if abs(diff) < 1e-5:
            return sigma
        sigma -= diff / vega_term
        if sigma < 0.01:
            sigma = 0.01
        if sigma > 5.0:
            sigma = 5.0
    return max(sigma, 0.0)


def bs_color(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Color = ∂Γ/∂t. Positive values mean gamma is growing as time passes
    (the "0DTE gamma spike" phenomenon around ATM strikes near expiry).

    Textbook formula (dividend-free). Units: gamma per year.
    """
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T
    prefactor = -_norm_pdf(d1) / (2 * S * T * sigma * sqrt_T)
    bracket = (
        2 * r * T + 1
        + d1 * (2 * r * T - d2 * sigma * sqrt_T) / (sigma * sqrt_T)
    )
    return prefactor * bracket


# ======================================================================
# Synthetic feed
# ======================================================================

SYNTHETIC_SPOTS = {
    "SPY": 588.0,
    "SPX": 6941.0,
    "QQQ": 508.0,
    "AAPL": 228.0,
    "MSFT": 430.0,
    "GOOGL": 195.0,
    "AMZN": 215.0,
    "META": 590.0,
    "NVDA": 140.0,
    "TSLA": 346.0,
}


class SyntheticOptionsFeed:
    """Deterministic-ish synthetic option chain for off-hours dev/testing."""

    def __init__(self, seed: int = 42):
        self._rng = random.Random(seed)

    def get_chain_snapshot(self, ticker: str) -> ChainSnapshot:
        spot = SYNTHETIC_SPOTS.get(ticker, 500.0)
        spot *= 1 + self._rng.uniform(-0.001, 0.001)

        contracts: list[OptionContract] = []
        today = date.today()

        if ticker in ("SPX",):
            strike_step = 25.0
        elif ticker in ("SPY", "QQQ"):
            strike_step = 2.0
        elif spot > 300:
            strike_step = 5.0
        else:
            strike_step = 2.5
        n_strikes_each_side = 12
        expiries_days = [1, 2, 3, 6, 9, 16, 30, 45]

        for days in expiries_days:
            expiry_date = today + timedelta(days=days)
            T = days / 365.0
            base_iv = 0.15 if ticker in ("SPY", "SPX", "QQQ") else 0.30
            iv = base_iv + 0.02 * (1.0 / max(T, 0.01)) ** 0.5

            atm_strike = round(spot / strike_step) * strike_step
            for i in range(-n_strikes_each_side, n_strikes_each_side + 1):
                strike = round(atm_strike + i * strike_step, 2)
                if strike <= 0:
                    continue

                for otype in ("C", "P"):
                    gamma = bs_gamma(spot, strike, T, 0.04, iv)
                    vanna = bs_vanna(spot, strike, T, 0.04, iv)
                    color = bs_color(spot, strike, T, 0.04, iv)

                    moneyness = abs(strike - spot) / spot
                    atm_factor = math.exp(-20 * moneyness ** 2)
                    round_factor = 1.5 if strike % 10 == 0 else 1.0
                    base_oi = self._rng.randint(100, 2000)
                    oi = base_oi * atm_factor * round_factor * (1 + 2 / max(days, 1))

                    sign = blended_dealer_sign(
                        ticker, strike, otype, days_to_expiry=days
                    )

                    contracts.append(
                        OptionContract(
                            strike=strike,
                            expiry=expiry_date.isoformat(),
                            option_type=otype,
                            gamma=gamma,
                            vanna=vanna,
                            open_interest=oi,
                            dealer_sign=sign,
                            color=color,
                        )
                    )

        return ChainSnapshot(
            ticker=ticker,
            spot=spot,
            timestamp=int(time.time()),
            contracts=contracts,
        )


# ======================================================================
# LSEG feed — real data
# ======================================================================

N_EXPIRIES = 8
RISK_FREE_RATE = 0.045

STRIKE_STEPS = {
    "SPY": 1.0, "QQQ": 1.0,
    "SPX": 25.0,
    "AAPL": 2.5, "MSFT": 2.5, "NVDA": 2.5,
    "TSLA": 5.0, "AMZN": 5.0, "GOOGL": 5.0, "META": 5.0,
}

OPTION_FIELDS = [
    "CF_BID", "CF_ASK", "CF_LAST", "CF_VOLUME",
    # Open interest: OPEN_INT returns <NA> on desktop tier for US listed
    # options, but OPINT_1 returns real values via the same REST call.
    # Confirmed for SPY and SPX option RICs on 2026-04-08. OPEN_INT is
    # kept in the list as a secondary probe in case OPINT_1 ever goes
    # dark or the tier changes.
    "OPINT_1", "OPEN_INT",
    "IMPL_VOL", "DELTA",
]


def _next_expiries(n: int = N_EXPIRIES) -> list[date]:
    """Next N weekdays starting from today."""
    today = date.today()
    out = []
    d = today
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _generate_strikes(spot: float, step: float, n_each_side: int = 15) -> list[float]:
    atm = round(spot / step) * step
    return [round(atm + i * step, 2) for i in range(-n_each_side, n_each_side + 1)]


class LSEGOptionsFeed:
    """
    Real LSEG data via refinitiv-data desktop session.

    Requires LSEG Workspace running and signed in. The bridge
    (flow-terminal/local_bridge.py) must also be running if you want
    streaming quotes — but this feed pulls snapshots via rd.get_data(),
    not streaming, so the bridge is optional for Polaris specifically.

    Gotchas:
        • rd.get_data can hang off-hours → guarded with daemon-thread timeout
        • IMPL_VOL / DELTA sometimes percent, sometimes decimal → normalized
        • Empty fields (pre-market / delayed feed) → skipped
        • LSEG doesn't return gamma/vanna — we compute via Black-Scholes
          from IV and spot
    """

    def __init__(self, timeout_sec: int = 15):
        self._started = False
        self._rd = None
        self.timeout_sec = timeout_sec
        # LSEG TR.OpenInterest cache: ticker -> (fetch_date, {ric: oi}).
        # Dormant on desktop tier (TR.OpenInterest not entitled for US
        # option RICs) but still runs once per day in case entitlements
        # change — failures are fail-fast at 30s so startup stays snappy.
        self._eod_oi_cache: dict[str, tuple[date, dict[str, float]]] = {}
        # yfinance EOD OI cache: ticker -> (fetch_date, {(strike,expiry,otype): oi}).
        # Primary OI source. Data is yesterday's close, updated once per
        # trading day. Values are true OI, not volume.
        self._yf_oi_cache: dict[
            str, tuple[date, dict[tuple[float, str, str], float]]
        ] = {}

    def _ensure_session(self):
        if self._started:
            return
        try:
            import refinitiv.data as rd
            rd.open_session(name="desktop.workspace")
            self._rd = rd
            self._started = True
        except Exception as e:
            raise RuntimeError(
                f"Could not open LSEG desktop session. "
                f"Is LSEG Workspace running and signed in? Error: {e}"
            ) from e

    # ────────────────────────────────────────────────────────────────
    # yfinance EOD OI — primary OI source
    # ────────────────────────────────────────────────────────────────
    @staticmethod
    def _yf_symbol(ticker: str) -> str:
        """Map polaris ticker → yfinance symbol."""
        t = ticker.upper()
        if t == "SPX":
            return "^SPX"
        return t

    def _fetch_yf_oi(
        self, ticker: str
    ) -> dict[tuple[float, str, str], float]:
        """
        Pull EOD OI from yfinance for every available expiry of `ticker`,
        return {(strike, expiry_iso, 'C'|'P'): open_interest}.

        yfinance serves the previous session's close, so values are stable
        through the day and update once per trading day. On a network
        failure or rate-limit this returns an empty dict — caller falls
        through to the LSEG OI chain.
        """
        try:
            import yfinance as yf
        except ImportError:
            print("[lseg_feed] yfinance not installed — skipping EOD OI", flush=True)
            return {}

        sym = self._yf_symbol(ticker)
        out: dict[tuple[float, str, str], float] = {}
        try:
            yticker = yf.Ticker(sym)
            expiries = yticker.options or ()
        except Exception as e:
            print(
                f"[lseg_feed] yfinance {sym} options list failed: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            return {}

        # Only pull the first 8 expiries — matches our chain window and
        # keeps the number of HTTP requests bounded.
        n_ok = 0
        for exp in expiries[:8]:
            try:
                chain = yticker.option_chain(exp)
            except Exception as e:
                print(
                    f"[lseg_feed] yfinance {sym} {exp} chain failed: "
                    f"{type(e).__name__}: {e}",
                    flush=True,
                )
                continue
            for df, otype in ((chain.calls, "C"), (chain.puts, "P")):
                if df is None or df.empty:
                    continue
                if "strike" not in df.columns or "openInterest" not in df.columns:
                    continue
                for _, row in df.iterrows():
                    try:
                        strike = float(row["strike"])
                        oi_raw = row["openInterest"]
                    except (KeyError, TypeError, ValueError):
                        continue
                    oi = self._clean_float(oi_raw)
                    if oi is not None and oi > 0:
                        out[(strike, exp, otype)] = oi
            n_ok += 1
        print(
            f"[lseg_feed] yfinance {sym}: {len(out)} contracts with OI "
            f"across {n_ok} expiries",
            flush=True,
        )
        return out

    @staticmethod
    def _calibrate_zero_dte_scale(
        rics_meta: list[tuple[str, float, str, "date"]],
        row_by_ric: dict[str, dict],
        yf_oi_by_key: dict[tuple[float, str, str], float],
        today: "date",
    ) -> float:
        """
        Compute median(OI/volume) across contracts where we have both
        yfinance OI and live-feed volume. Used to scale 0DTE volume
        fallback into an OI-like magnitude.

        Returns 1.0 (pass-through) if there aren't enough matched pairs
        — don't invent a correction from noise.
        """
        ratios = []
        for ric, strike, otype, exp in rics_meta:
            if not hasattr(exp, "isoformat"):
                continue
            if (exp - today).days <= 0:
                continue  # skip 0DTE itself — that's what we're calibrating FOR
            key = (float(strike), exp.isoformat(), otype)
            oi = yf_oi_by_key.get(key)
            if oi is None or oi <= 0:
                continue
            row = row_by_ric.get(ric)
            if row is None:
                continue
            vol_raw = row.get("CF_VOLUME")
            try:
                import pandas as pd
                if vol_raw is None or pd.isna(vol_raw):
                    continue
                vol = float(vol_raw)
            except (TypeError, ValueError):
                continue
            if vol <= 0:
                continue
            ratios.append(oi / vol)
        if len(ratios) < 20:
            # Too few matches — don't fabricate a correction.
            print(
                f"[lseg_feed] 0DTE scale calibration: only "
                f"{len(ratios)} matched pairs, using pass-through",
                flush=True,
            )
            return 1.0
        import statistics
        scale = statistics.median(ratios)
        print(
            f"[lseg_feed] 0DTE scale: median OI/volume ratio = "
            f"{scale:.4f} (from {len(ratios)} matched pairs)",
            flush=True,
        )
        # Sanity clamp: scale should be in (0, 1] since OI < volume is
        # the normal case. If our calibration is outside that range,
        # something is off — pass through raw volume instead.
        if not (0 < scale <= 1):
            return 1.0
        return scale

    def _get_yf_oi_cached(
        self, ticker: str
    ) -> dict[tuple[float, str, str], float]:
        """Fetch-once-per-day wrapper around _fetch_yf_oi."""
        today = date.today()
        cached = self._yf_oi_cache.get(ticker)
        if cached and cached[0] == today:
            return cached[1]
        oi_map = self._fetch_yf_oi(ticker)
        self._yf_oi_cache[ticker] = (today, oi_map)
        return oi_map

    def _fetch_eod_oi(self, rics: list[str]) -> dict[str, float]:
        """
        Fetch the most recent EOD Open Interest per RIC via the TR.* data
        item namespace (point-in-time historical fields, accessible through
        rd.get_data rather than real-time get_history).

        Tried in order until one returns a wide DataFrame with OI values:
            TR.OpenInterest
            TR.OpenInterest.date
            OPEN_INT_ACC        (accumulated OI, sometimes populated on
                                 desktop tier when OPEN_INT is NA)

        Returns {ric: oi}. RICs with no data are absent. All failures are
        caught and logged — caller falls back to the legacy OI chain.
        """
        if not rics:
            return {}
        out: dict[str, float] = {}
        # TR.* fields are much slower than real-time fields on desktop tier.
        # Small chunks + long per-chunk timeout, AND fail-fast if the first
        # chunk times out entirely (don't burn 60s across 4 failing chunks).
        chunk_size = 25
        per_chunk_timeout = 30
        n_chunks = (len(rics) + chunk_size - 1) // chunk_size
        # Try fields in priority order. First one that returns any populated
        # rows wins for the rest of the batch.
        candidate_fieldsets = [
            ["TR.OpenInterest"],
            ["OPEN_INT_ACC"],
        ]
        first_chunk_timed_out = False
        for i in range(0, len(rics), chunk_size):
            chunk_idx = i // chunk_size + 1
            chunk = rics[i : i + chunk_size]
            if first_chunk_timed_out:
                # Abandon the rest — TR.* is clearly unresponsive right now.
                break
            chunk_out: dict[str, float] = {}
            for fields in candidate_fieldsets:
                try:
                    df = self._get_data_with_timeout(
                        chunk, fields, timeout_override=per_chunk_timeout
                    )
                except TimeoutError as e:
                    print(
                        f"[lseg_feed] EOD OI chunk {chunk_idx}/{n_chunks} "
                        f"{fields[0]} timed out",
                        flush=True,
                    )
                    if chunk_idx == 1:
                        first_chunk_timed_out = True
                    continue
                except Exception as e:
                    print(
                        f"[lseg_feed] EOD OI chunk {chunk_idx}/{n_chunks} "
                        f"{fields[0]} failed: {type(e).__name__}: {e}",
                        flush=True,
                    )
                    continue
                if df is None or getattr(df, "empty", True):
                    continue
                # rd.get_data returns a flat DataFrame with an Instrument
                # column + one column per field. Find the instrument column
                # (same logic as the main snapshot path).
                ric_col = None
                for col in ("Instrument", "instrument", "RIC", "Ric"):
                    if col in df.columns:
                        ric_col = col
                        break
                if ric_col is None:
                    continue
                # Value column: the first non-instrument column (TR.* field
                # names can come back as "Open Interest" rather than the
                # literal field name).
                val_col = None
                for col in df.columns:
                    if col != ric_col:
                        val_col = col
                        break
                if val_col is None:
                    continue
                for _, row in df.iterrows():
                    ric = str(row[ric_col])
                    raw = row[val_col]
                    val = self._clean_float(raw)
                    if val is not None and val > 0:
                        chunk_out[ric] = val
                if chunk_out:
                    break  # this fieldset worked, don't try the next
            out.update(chunk_out)
        return out

    def _get_eod_oi_cached(
        self, ticker: str, rics: list[str]
    ) -> dict[str, float]:
        """Fetch-once-per-day wrapper around _fetch_eod_oi. Swallows all
        errors and returns an empty dict on failure, so the caller can
        always fall through to the legacy OI chain."""
        today = date.today()
        cached = self._eod_oi_cache.get(ticker)
        if cached and cached[0] == today:
            return cached[1]
        try:
            print(
                f"[lseg_feed] fetching EOD OI for {ticker} "
                f"({len(rics)} rics)...",
                flush=True,
            )
            oi_map = self._fetch_eod_oi(rics)
            print(
                f"[lseg_feed] EOD OI for {ticker}: "
                f"{len(oi_map)}/{len(rics)} populated",
                flush=True,
            )
        except Exception as e:
            print(
                f"[lseg_feed] EOD OI fetch failed for {ticker}: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            oi_map = {}
        self._eod_oi_cache[ticker] = (today, oi_map)
        return oi_map

    def _get_data_with_timeout(
        self, rics: list[str], fields: list[str], timeout_override: int | None = None
    ):
        """rd.get_data wrapped in a daemon thread to enforce hard timeout."""
        import threading
        result = {"df": None, "err": None}

        def _runner():
            try:
                result["df"] = self._rd.get_data(rics, fields)
            except Exception as e:
                result["err"] = e

        t = threading.Thread(target=_runner, daemon=True)
        t.start()
        timeout = timeout_override if timeout_override is not None else self.timeout_sec
        t.join(timeout=timeout)
        if t.is_alive():
            raise TimeoutError(
                f"rd.get_data timed out after {timeout}s "
                f"(off-hours or feed unresponsive)"
            )
        if result["err"]:
            raise result["err"]
        return result["df"]

    @staticmethod
    def _ticker_spot_candidates(ticker: str) -> list[str]:
        t = ticker.upper()
        if t == "SPX":
            return [".SPX"]
        if t == "SPY":
            return ["SPY.A", "SPY.P", "SPY"]
        if t == "QQQ":
            return ["QQQ.O", "QQQ.OQ", "QQQ.P"]
        return [f"{t}.O", f"{t}.OQ", f"{t}.P", t]

    def _fetch_spot(self, ticker: str) -> float:
        candidates = self._ticker_spot_candidates(ticker)
        for ric in candidates:
            try:
                df = self._get_data_with_timeout([ric], ["TRDPRC_1"])
                if df is None or df.empty:
                    continue
                row = df.iloc[0]
                val = row.get("TRDPRC_1") if hasattr(row, "get") else row["TRDPRC_1"]
                val = self._clean_float(val)
                if val and val > 0:
                    return val
            except Exception:
                continue
        raise RuntimeError(f"Could not resolve spot price for {ticker}")

    def get_chain_snapshot(self, ticker: str) -> ChainSnapshot:
        self._ensure_session()

        spot = self._fetch_spot(ticker)
        step = STRIKE_STEPS.get(ticker.upper(), 5.0)
        strikes = _generate_strikes(spot, step, n_each_side=15)
        expiries = _next_expiries(N_EXPIRIES)

        rics_meta: list[tuple[str, float, str, date]] = []
        for exp in expiries:
            for strike in strikes:
                for otype in ("C", "P"):
                    ric = build_option_ric(ticker, exp, otype, strike)
                    rics_meta.append((ric, strike, otype, exp))

        all_rics = [m[0] for m in rics_meta]

        # Chunk to avoid massive single requests. Track failures.
        chunk_size = 300
        frames = []
        n_chunks = (len(all_rics) + chunk_size - 1) // chunk_size
        n_failed = 0
        for i in range(0, len(all_rics), chunk_size):
            chunk = all_rics[i : i + chunk_size]
            try:
                df = self._get_data_with_timeout(chunk, OPTION_FIELDS)
                if df is not None and not df.empty:
                    frames.append(df)
                else:
                    n_failed += 1
                    print(f"[lseg_feed] {ticker} chunk {i // chunk_size + 1}/{n_chunks}: empty result")
            except TimeoutError:
                raise
            except Exception as e:
                n_failed += 1
                print(f"[lseg_feed] {ticker} chunk {i // chunk_size + 1}/{n_chunks} failed: {type(e).__name__}: {e}")

        if not frames:
            raise RuntimeError(
                f"All {n_chunks} chunks failed for {ticker} — feed may be down "
                f"or RIC format may be wrong. Check check_bridge.py output."
            )
        if n_failed > n_chunks // 2:
            raise RuntimeError(
                f"{n_failed}/{n_chunks} chunks failed for {ticker} — too sparse to trust. "
                f"Aborting rather than building a misleading grid."
            )

        import pandas as pd
        df = pd.concat(frames, ignore_index=True)

        # ── EOD OI resolution ─────────────────────────────────────────
        # Primary: yfinance (real EOD OI, free, network-only). The LSEG
        # TR.OpenInterest probe is opt-in via POLARIS_PROBE_LSEG_OI=1
        # because it hangs ~5 minutes per startup on the desktop tier
        # (TR.* fields aren't entitled for US option RICs). It stays
        # wired up so it activates automatically if entitlements ever
        # change. Snapshot OPEN_INT and volume fallbacks live in the
        # per-row loop.
        yf_oi_by_key = self._get_yf_oi_cached(ticker)
        if os.environ.get("POLARIS_PROBE_LSEG_OI") == "1":
            eod_oi_by_ric = self._get_eod_oi_cached(ticker, all_rics)
        else:
            eod_oi_by_ric: dict[str, float] = {}

        # CRITICAL: rows MUST be matched by an Instrument column.
        # Falling back to positional matching is unsafe — LSEG can reorder
        # results, especially after failed lookups within a batch.
        ric_col = None
        for col in ("Instrument", "instrument", "RIC", "Ric"):
            if col in df.columns:
                ric_col = col
                break

        if ric_col is None:
            raise RuntimeError(
                f"LSEG response for {ticker} has no Instrument/RIC column. "
                f"Available columns: {list(df.columns)}. "
                f"Cannot safely match rows to contracts."
            )

        row_by_ric: dict[str, dict] = {}
        for _, row in df.iterrows():
            row_by_ric[str(row[ric_col])] = row.to_dict()

        contracts: list[OptionContract] = []
        today = date.today()

        # Calibrate a 0DTE volume→OI scale correction. yfinance drops
        # same-day-expiring contracts, so today's 0DTE column otherwise
        # falls through to raw volume and inflates 30-60x. Use the
        # observed OI/volume ratio from the EARLIEST non-0DTE expiry
        # where both are populated, median across all its contracts.
        # Falls back to 1.0 (pass-through) if no calibration data.
        zero_dte_scale = self._calibrate_zero_dte_scale(
            rics_meta, row_by_ric, yf_oi_by_key, today
        )

        for ric, strike, otype, exp in rics_meta:
            row = row_by_ric.get(ric)
            if row is None:
                continue

            iv = self._clean_float(row.get("IMPL_VOL"))
            delta = self._clean_float(row.get("DELTA"))
            volume = self._clean_float(row.get("CF_VOLUME"))

            # ── OI resolution order (best → worst):
            #   1. LSEG snapshot OPINT_1 — real OI, populated for both
            #      SPY AND SPX on desktop tier (confirmed 2026-04-08).
            #      This is the primary source.
            #   2. LSEG snapshot OPEN_INT — the "old" field we used to
            #      try. Almost always <NA> but checked as a secondary.
            #   3. yfinance EOD OI — previous-close OI per contract.
            #      Kept as a fallback in case LSEG has a gap.
            #   4. LSEG TR.OpenInterest — dormant probe path.
            #   5. Today's volume (with 0DTE calibration) — last resort.
            oi = self._clean_float(row.get("OPINT_1"))
            if oi is None or oi <= 0:
                oi = self._clean_float(row.get("OPEN_INT"))
            if oi is None or oi <= 0:
                exp_iso = exp.isoformat() if hasattr(exp, "isoformat") else str(exp)
                oi = yf_oi_by_key.get((float(strike), exp_iso, otype))
            if oi is None or oi <= 0:
                oi = eod_oi_by_ric.get(ric)
            if oi is None or oi <= 0:
                if volume is not None and volume > 0:
                    is_zero_dte = hasattr(exp, "__sub__") and (exp - today).days <= 0
                    oi = volume * (zero_dte_scale if is_zero_dte else 1.0)
                else:
                    continue  # truly no data, skip

            # ── IV fallback: invert from delta if LSEG didn't provide IV.
            #    The desktop tier has DELTA populated for most liquid strikes
            #    even when IMPL_VOL is NA.
            if iv is None or iv <= 0:
                days_to_expiry_tmp = max((exp - today).days, 0)
                T_tmp = max(days_to_expiry_tmp, 0) / 365.0
                if T_tmp == 0:
                    T_tmp = 0.5 / 365.0
                if delta is not None and abs(delta) > 0.01:
                    iv = implied_vol_from_delta(
                        spot, strike, T_tmp, RISK_FREE_RATE, delta, otype
                    )
                if iv is None or iv <= 0:
                    continue

            # Normalize IV: LSEG returns percent (15.0 = 15%) most of the time
            if iv > 3:
                iv = iv / 100

            days_to_expiry = (exp - today).days
            T = max(days_to_expiry, 0) / 365.0
            if T == 0:
                T = 0.5 / 365.0  # 0DTE: half a day keeps BS math sane

            gamma = bs_gamma(spot, strike, T, RISK_FREE_RATE, iv)
            vanna = bs_vanna(spot, strike, T, RISK_FREE_RATE, iv)
            color = bs_color(spot, strike, T, RISK_FREE_RATE, iv)

            sign = blended_dealer_sign(
                ticker, strike, otype, days_to_expiry=days_to_expiry
            )

            contracts.append(
                OptionContract(
                    strike=strike,
                    expiry=exp.isoformat(),
                    option_type=otype,
                    gamma=gamma,
                    vanna=vanna,
                    open_interest=oi,
                    dealer_sign=sign,
                    color=color,
                )
            )

        if not contracts:
            raise RuntimeError(
                f"All chain rows empty for {ticker} — neither OI nor volume "
                f"populated. Check LSEG entitlements or try again later."
            )

        return ChainSnapshot(
            ticker=ticker,
            spot=spot,
            timestamp=int(time.time()),
            contracts=contracts,
        )

    @staticmethod
    def _clean_float(val) -> Optional[float]:
        if val is None:
            return None
        try:
            import pandas as pd
            if pd.isna(val):
                return None
        except Exception:
            pass
        try:
            f = float(val)
            return None if f != f else f
        except (TypeError, ValueError):
            return None


# ======================================================================
# Replay feed — deterministic test from pickled snapshots
# ======================================================================

class ReplayOptionsFeed:
    """Replays pre-captured ChainSnapshots from disk."""

    def __init__(self, snapshots_dir: Path):
        self.snapshots_dir = Path(snapshots_dir)
        self._cache: dict[str, ChainSnapshot] = {}
        self._load()

    def _load(self):
        if not self.snapshots_dir.exists():
            return
        for pkl in sorted(self.snapshots_dir.glob("*.pkl")):
            try:
                with open(pkl, "rb") as f:
                    snap: ChainSnapshot = pickle.load(f)
                self._cache[snap.ticker] = snap
            except Exception as e:
                print(f"[replay] failed to load {pkl}: {e}")

    def get_chain_snapshot(self, ticker: str) -> ChainSnapshot:
        if ticker not in self._cache:
            raise KeyError(f"No replay snapshot for {ticker}")
        return self._cache[ticker]


def save_snapshot(snap: ChainSnapshot, directory: Path) -> Path:
    """Pickle a ChainSnapshot to disk for later replay."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{snap.ticker}_{snap.timestamp}.pkl"
    with open(path, "wb") as f:
        pickle.dump(snap, f)
    return path


# ======================================================================
# Factory
# ======================================================================

def make_feed(mode: str = "synthetic", replay_dir: Optional[Path] = None) -> OptionsFeed:
    if mode == "synthetic":
        return SyntheticOptionsFeed()
    if mode == "lseg":
        return LSEGOptionsFeed()
    if mode == "replay":
        if replay_dir is None:
            raise ValueError("replay mode requires replay_dir")
        return ReplayOptionsFeed(replay_dir)
    raise ValueError(f"Unknown feed mode: {mode}")
