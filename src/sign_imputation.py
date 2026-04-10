"""
Dealer sign imputation for GEX computation.

Industry standard (SqueezeMetrics, SpotGamma, Skylit, Barchart):
    Calls → dealer_sign = +1   (customers buy calls → dealers short → long gamma → stabilizing)
    Puts  → dealer_sign = -1   (customers buy puts  → dealers short → short gamma → destabilizing)

This is the universal assumption used by every public GEX platform. It's
imperfect (not all OI is customer-initiated — covered call writing, put
selling for income, and structured products all flip the direction), but
without actual CBOE/OCC customer-vs-firm OI data, it's the best available.

The previous implementation attempted to correct for covered-call
overwriting (AAPL/NVDA/MSFT/TSLA), structured product positioning (JPM
collar), and 0DTE retail flow. These corrections were well-intentioned but:
    1. Started from the wrong baseline (dealer_sign=-1 for ALL, not just puts)
    2. Required assumptions about institutional positioning that change quarterly
    3. Made the Sirius node land in a different place than every other platform

The standard +1/-1 split is now the sole logic. If CBOE/OCC customer-vs-firm
data becomes available, Layer 2 and 3 from the original design can be
reactivated against the correct baseline.

References:
    - SqueezeMetrics White Paper (2017)
    - SpotGamma GEX Methodology (spotgamma.com)
    - Barbon & Buraschi "Gamma Fragility" (2021) — uses actual broker-dealer
      inventory data from regulatory filings for the gold-standard approach
"""
from __future__ import annotations


def blended_dealer_sign(
    ticker: str,
    strike: float,
    option_type: str,
    oi_reconciled_net: float | None = None,
    days_to_expiry: int | None = None,
    spot: float | None = None,
) -> int:
    """
    Return the dealer sign for a contract.

    Standard convention:
        Calls → +1   (dealer short call → long gamma → positive GEX)
        Puts  → -1   (dealer short put  → short gamma → negative GEX)
    """
    if option_type.upper() == "C":
        return +1
    return -1
