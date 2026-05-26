"""
Level computation logic.

Computes PDH/PDL, PMH/PML, and ORH/ORL from FMP bar data.
Time boundaries match the PineScript indicator exactly:
  - Full Day (PDH/PDL): 01:00 - 16:58 PDT, previous completed trading day
  - Premarket (PMH/PML): 01:00 - 06:29 PDT, current day
  - Opening Range: 06:30 - 06:34 PDT, current day
"""

from datetime import datetime

import pytz

import config
import fmp_client

TZ = pytz.timezone(config.TIMEZONE)


def compute_pdh_pdl(bars):
    """Previous day high/low from bars within 01:00-16:58 PDT."""
    if not bars:
        return None, None
    return max(b["high"] for b in bars), min(b["low"] for b in bars)


def compute_pmh_pml(bars):
    """Premarket high/low from bars within 01:00-06:29 PDT."""
    if not bars:
        return None, None
    return max(b["high"] for b in bars), min(b["low"] for b in bars)


def compute_opening_range(bars):
    """5-min opening range high/low from bars within 06:30-06:34 PDT."""
    if not bars:
        return None, None
    return max(b["high"] for b in bars), min(b["low"] for b in bars)


def _filter_bars_by_time(bars, start_hhmm, end_hhmm):
    """Filter bars to only include those within a time window (in user's timezone).

    start_hhmm/end_hhmm are inclusive boundaries in HHMM format.
    Bar timestamps are ET; we convert to the user's timezone for comparison.
    """
    result = []
    for bar in bars:
        local_dt = bar["datetime_et"].astimezone(TZ)
        t = local_dt.hour * 100 + local_dt.minute
        if start_hhmm <= t <= end_hhmm:
            result.append(bar)
    return result


def get_levels_for_ticker(ticker):
    """Compute all key levels for a ticker.

    Returns dict: {"PDH": float, "PDL": float, "PMH": float, "PML": float,
                   "ORH": None, "ORL": None}
    ORH/ORL are None at startup -- filled later by the monitor at 06:35.
    """
    now = datetime.now(TZ)
    levels = {"PDH": None, "PDL": None, "PMH": None, "PML": None,
              "ORH": None, "ORL": None}

    # --- PDH/PDL: previous completed trading day, 01:00-16:58 PDT ---
    prev_day = fmp_client.find_previous_trading_day(now.date())
    if prev_day:
        bars = fmp_client.fetch_bars(ticker, "1min", start=prev_day, end=prev_day)
        filtered = _filter_bars_by_time(bars, config.FULL_DAY_START, config.FULL_DAY_END)
        pdh, pdl = compute_pdh_pdl(filtered)
        levels["PDH"] = pdh
        levels["PDL"] = pdl

    # --- PMH/PML: today's premarket, 01:00-06:29 PDT ---
    today = now.date()
    bars = fmp_client.fetch_bars(ticker, "1min", start=today, end=today)
    filtered = _filter_bars_by_time(bars, config.PREMARKET_START, config.PREMARKET_END)
    pmh, pml = compute_pmh_pml(filtered)
    levels["PMH"] = pmh
    levels["PML"] = pml

    return levels


if __name__ == "__main__":
    # Standalone test: fetch and print levels for a single ticker
    if not config.FMP_API_KEY:
        print("Set FMP_API_KEY environment variable.")
        raise SystemExit(1)

    ticker = "SPY"

    print(f"Computing levels for {ticker}...")
    levels = get_levels_for_ticker(ticker)

    print(f"\n{'Level':<6} {'Price':>10}")
    print("-" * 18)
    for name, price in levels.items():
        if price is not None:
            print(f"{name:<6} {price:>10.2f}")
        else:
            print(f"{name:<6} {'--':>10}")
