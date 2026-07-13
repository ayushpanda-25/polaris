"""
Tests for the Prometheus/CBOE backup feed and the ResilientFeed breaker.

The mapper test reuses Prometheus's committed CBOE fixture
(tests/fixtures/cboe_spy_atm.json in the prometheus repo) so no network
is involved; it is skipped if the prometheus repo isn't on disk.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.data_feed import (
    ChainSnapshot,
    PrometheusBackupFeed,
    ResilientFeed,
    PROMETHEUS_DIR,
    _import_prometheus_cboe,
)

PROM_FIXTURE = (
    Path(PROMETHEUS_DIR) / "tests" / "fixtures" / "cboe_spy_atm.json"
)

prometheus_available = PROM_FIXTURE.exists()


# ──────────────────────────────────────────────────────────────────────
# CBOE payload → ChainSnapshot mapping
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not prometheus_available,
    reason=f"prometheus repo/fixture not found at {PROM_FIXTURE}",
)
def test_snapshot_from_raw_maps_fixture(monkeypatch):
    _, parse_occ = _import_prometheus_cboe()
    raw = json.loads(PROM_FIXTURE.read_text())

    # The mapper windows expiries to the next 8 weekdays from *today*;
    # pin that window to the expiries actually present in the (static)
    # fixture so this test never goes stale.
    fixture_exps = set()
    for o in raw["data"]["options"]:
        try:
            _, exp, _, _ = parse_occ(o.get("option", ""))
        except ValueError:
            continue
        fixture_exps.add(exp)
    pinned = sorted(fixture_exps)
    import src.data_feed as df_mod
    monkeypatch.setattr(df_mod, "_next_expiries", lambda n=8: pinned[:n])

    snap = PrometheusBackupFeed._snapshot_from_raw(raw, "SPY", parse_occ)

    assert isinstance(snap, ChainSnapshot)
    assert snap.source == "cboe"
    assert snap.spot > 0
    assert len(snap.contracts) > 0
    # Every mapped contract must be GEX-engine ready
    for c in snap.contracts:
        assert c.gamma >= 0
        assert c.open_interest > 0
        assert c.option_type in ("C", "P")
        assert c.dealer_sign in (-1, 1)
    # At least some contracts carry real (non-BS-fallback) greeks
    assert any(c.gamma > 0 for c in snap.contracts)
    # Strike window respected (±3.5% of spot)
    lo = snap.spot * (1 - PrometheusBackupFeed._STRIKE_WINDOW_PCT)
    hi = snap.spot * (1 + PrometheusBackupFeed._STRIKE_WINDOW_PCT)
    assert all(lo <= c.strike <= hi for c in snap.contracts)


@pytest.mark.skipif(
    not prometheus_available,
    reason=f"prometheus repo/fixture not found at {PROM_FIXTURE}",
)
def test_snapshot_from_raw_rejects_empty():
    _, parse_occ = _import_prometheus_cboe()
    with pytest.raises(RuntimeError, match="no current_price"):
        PrometheusBackupFeed._snapshot_from_raw(
            {"data": {"options": []}}, "SPY", parse_occ
        )


# ──────────────────────────────────────────────────────────────────────
# ResilientFeed circuit breaker
# ──────────────────────────────────────────────────────────────────────

def _snap(ticker: str, source: str) -> ChainSnapshot:
    return ChainSnapshot(
        ticker=ticker, spot=100.0, timestamp=int(time.time()),
        contracts=[], source=source,
    )


class FlakyPrimary:
    """Primary that fails until .healthy is flipped, counting calls."""
    def __init__(self):
        self.calls = 0
        self.healthy = False

    def get_chain_snapshot(self, ticker):
        self.calls += 1
        if not self.healthy:
            raise RuntimeError("LSEG down (simulated)")
        return _snap(ticker, "lseg")


class StubBackup:
    def __init__(self):
        self.calls = 0

    def get_chain_snapshot(self, ticker):
        self.calls += 1
        return _snap(ticker, "cboe")


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_breaker_serves_backup_and_opens():
    clock = FakeClock()
    primary, backup = FlakyPrimary(), StubBackup()
    feed = ResilientFeed(primary, backup, threshold=2, cooldown=100, clock=clock)

    # Failure 1: primary tried, backup served, breaker still closed
    assert feed.get_chain_snapshot("SPY").source == "cboe"
    assert primary.calls == 1

    # Failure 2: trips the breaker
    assert feed.get_chain_snapshot("SPX").source == "cboe"
    assert primary.calls == 2

    # Breaker open: primary NOT touched anymore
    clock.t += 10
    for t in ("QQQ", "AAPL", "NVDA"):
        assert feed.get_chain_snapshot(t).source == "cboe"
    assert primary.calls == 2
    assert backup.calls == 5


def test_breaker_half_open_probe_and_reopen():
    clock = FakeClock()
    primary, backup = FlakyPrimary(), StubBackup()
    feed = ResilientFeed(primary, backup, threshold=2, cooldown=100, clock=clock)

    feed.get_chain_snapshot("SPY")
    feed.get_chain_snapshot("SPX")          # breaker opens here
    assert primary.calls == 2

    # Cooldown expires → exactly ONE probe; still failing → re-opens
    clock.t += 101
    assert feed.get_chain_snapshot("SPY").source == "cboe"
    assert primary.calls == 3
    assert feed.get_chain_snapshot("SPX").source == "cboe"
    assert primary.calls == 3               # open again, no second probe


def test_breaker_recovery_resets():
    clock = FakeClock()
    primary, backup = FlakyPrimary(), StubBackup()
    feed = ResilientFeed(primary, backup, threshold=2, cooldown=100, clock=clock)

    feed.get_chain_snapshot("SPY")
    feed.get_chain_snapshot("SPX")          # breaker opens
    primary.healthy = True

    # Probe after cooldown succeeds → back on LSEG for everything
    clock.t += 101
    assert feed.get_chain_snapshot("SPY").source == "lseg"
    assert feed.get_chain_snapshot("SPX").source == "lseg"
    assert feed.get_chain_snapshot("QQQ").source == "lseg"

    # And a later single blip doesn't flap the breaker (threshold=2)
    primary.healthy = False
    assert feed.get_chain_snapshot("SPY").source == "cboe"
    primary.healthy = True
    assert feed.get_chain_snapshot("SPX").source == "lseg"


def test_no_backup_reraises():
    clock = FakeClock()
    feed = ResilientFeed(FlakyPrimary(), None, threshold=1, cooldown=100, clock=clock)
    with pytest.raises(RuntimeError, match="no backup feed"):
        feed.get_chain_snapshot("SPY")
