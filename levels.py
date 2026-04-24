"""
Level computation logic.

Computes PDH/PDL, PMH/PML, and ORH/ORL from Alpaca bar data.
Time boundaries match the PineScript indicator exactly:
  - Full Day (PDH/PDL): 01:00 – 16:58 PDT, previous completed trading day
  - Premarket (PMH/PML): 01:00 – 06:29 PDT, current day
  - Opening Range: 06:30 – 06:34 PDT, current day
"""

from datetime import datetime, timedelta

import pytz
from alpaca_trade_api.rest import REST, TimeFrame

import config

TZ = pytz.timezone(config.TIMEZONE)


def compute_pdh_pdl(bars):
    """Previous day high/low from bars within 01:00-16:58 PDT."""
    if bars.empty:
        return None, None
    return float(bars["high"].max()), float(bars["low"].min())


def compute_pmh_pml(bars):
    """Premarket high/low from bars within 01:00-06:29 PDT."""
    if bars.empty:
        return None, None
    return float(bars["high"].max()), float(bars["low"].min())


def compute_opening_range(bars):
    """5-min opening range high/low from bars within 06:30-06:34 PDT."""
    if bars.empty:
        return None, None
    return float(bars["high"].max()), float(bars["low"].min())


def _hhmm(dt):
    """Convert a datetime to HHMM integer for boundary comparison."""
    return dt.hour * 100 + dt.minute


def _find_previous_trading_day(api):
    """Find the most recent completed trading day using Alpaca calendar.

    A 'completed' day means its full-day window (01:00-16:58) has fully passed.
    If we're currently before 16:58 PDT, the previous trading day is the one
    before today. If after 16:58, today's data could count — but since our
    PineScript rolls over on newPMSession (first premarket bar), we use the
    same logic: the previous day's full range is locked once premarket starts.
    """
    now = datetime.now(TZ)
    # Fetch the last 5 calendar days to handle weekends/holidays
    start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")
    calendar = api.get_calendar(start, end)

    # Calendar entries have .date as a date object and .open/.close times
    # We want the most recent trading day that is fully completed
    today_date = now.date()
    prev_day = None
    for entry in calendar:
        entry_date = entry.date
        if hasattr(entry_date, "date"):
            entry_date = entry_date.date()
        if entry_date < today_date:
            prev_day = entry_date
    return prev_day


def _filter_bars_by_time(bars, start_hhmm, end_hhmm):
    """Filter a DataFrame of bars to only include those within a PDT time window.

    start_hhmm/end_hhmm are inclusive boundaries in HHMM format.
    Bars index is UTC timestamps; we convert to PDT for comparison.
    """
    if bars.empty:
        return bars

    # Convert index to PDT and filter
    idx_pdt = bars.index.tz_convert(TZ)
    mask = []
    for ts in idx_pdt:
        t = ts.hour * 100 + ts.minute
        mask.append(start_hhmm <= t <= end_hhmm)
    return bars[mask]


def get_levels_for_ticker(api, ticker):
    """Compute all key levels for a ticker.

    Returns dict: {"PDH": float, "PDL": float, "PMH": float, "PML": float,
                   "ORH": None, "ORL": None}
    ORH/ORL are None at startup — filled later by the monitor at 06:35.
    """
    now = datetime.now(TZ)
    levels = {"PDH": None, "PDL": None, "PMH": None, "PML": None,
              "ORH": None, "ORL": None}

    # --- PDH/PDL: previous completed trading day, 01:00-16:58 PDT ---
    prev_day = _find_previous_trading_day(api)
    if prev_day:
        # Build UTC time range for the previous day's full-day window
        pd_start = TZ.localize(datetime(prev_day.year, prev_day.month, prev_day.day, 1, 0))
        pd_end = TZ.localize(datetime(prev_day.year, prev_day.month, prev_day.day, 16, 58))

        bars = api.get_bars(
            ticker, TimeFrame.Minute,
            start=pd_start.isoformat(),
            end=pd_end.isoformat(),
        ).df

        filtered = _filter_bars_by_time(bars, config.FULL_DAY_START, config.FULL_DAY_END)
        pdh, pdl = compute_pdh_pdl(filtered)
        levels["PDH"] = pdh
        levels["PDL"] = pdl

    # --- PMH/PML: today's premarket, 01:00-06:29 PDT ---
    today = now.date()
    pm_start = TZ.localize(datetime(today.year, today.month, today.day, 1, 0))
    pm_end = TZ.localize(datetime(today.year, today.month, today.day, 6, 29))

    bars = api.get_bars(
        ticker, TimeFrame.Minute,
        start=pm_start.isoformat(),
        end=pm_end.isoformat(),
    ).df

    filtered = _filter_bars_by_time(bars, config.PREMARKET_START, config.PREMARKET_END)
    pmh, pml = compute_pmh_pml(filtered)
    levels["PMH"] = pmh
    levels["PML"] = pml

    return levels


if __name__ == "__main__":
    # Standalone test: fetch and print levels for a single ticker
    if not config.ALPACA_API_KEY:
        print("Set ALPACA_API_KEY and ALPACA_API_SECRET environment variables.")
        raise SystemExit(1)

    api = REST(config.ALPACA_API_KEY, config.ALPACA_API_SECRET, config.BASE_URL)
    ticker = "SPY"

    print(f"Computing levels for {ticker}...")
    levels = get_levels_for_ticker(api, ticker)

    print(f"\n{'Level':<6} {'Price':>10}")
    print("-" * 18)
    for name, price in levels.items():
        if price is not None:
            print(f"{name:<6} {price:>10.2f}")
        else:
            print(f"{name:<6} {'--':>10}")
