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
    "OPEN_INT", "IMPL_VOL", "DELTA",
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

    def _get_data_with_timeout(self, rics: list[str], fields: list[str]):
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
        t.join(timeout=self.timeout_sec)
        if t.is_alive():
            raise TimeoutError(
                f"rd.get_data timed out after {self.timeout_sec}s "
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

        for ric, strike, otype, exp in rics_meta:
            row = row_by_ric.get(ric)
            if row is None:
                continue

            oi = self._clean_float(row.get("OPEN_INT"))
            iv = self._clean_float(row.get("IMPL_VOL"))
            delta = self._clean_float(row.get("DELTA"))
            volume = self._clean_float(row.get("CF_VOLUME"))

            # ── OI fallback: LSEG desktop tier often returns NA for OI on
            #    US single-name options. Fall back to same-day volume as
            #    a proxy — it's not perfect (volume != OI) but it's a
            #    real magnitude signal that lets us produce a usable GEX
            #    surface intraday. EOD OI reconciliation (scripts/
            #    backfill_eod.py) will tighten this later.
            if oi is None or oi <= 0:
                if volume is not None and volume > 0:
                    oi = volume
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
