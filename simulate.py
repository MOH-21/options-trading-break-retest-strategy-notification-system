"""
Key Levels Monitor — Simulation Mode

Replays a historical trading day's 1-min bars through the alert engine
for multiple tickers simultaneously on a unified timeline.

Usage:
    python simulate.py                          # most recent trading day, default tickers
    python simulate.py 2026-04-23               # specific date
    python simulate.py 2026-04-23 AAPL NVDA     # specific date + custom tickers (QQQ always included)
"""

import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

import pytz
from alpaca_trade_api.rest import REST, TimeFrame

import config
from levels import (compute_pdh_pdl, compute_pmh_pml, compute_opening_range,
                    _filter_bars_by_time, _find_previous_trading_day)
from alerts import evaluate_bar, format_alert, AlertState

TZ = pytz.timezone(config.TIMEZONE)

BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
WHITE = "\033[97m"

# Default tech stocks to simulate alongside QQQ
DEFAULT_TECH = ["NVDA", "AAPL", "MSFT", "META", "AMZN"]
BENCHMARK = "QQQ"


def get_levels_for_sim(api, ticker, sim_date):
    """Compute PDH/PDL, PMH/PML, ORH/ORL for a simulation date."""
    levels = {"PDH": None, "PDL": None, "PMH": None, "PML": None,
              "ORH": None, "ORL": None}

    # Find previous trading day relative to sim_date
    start = (sim_date - timedelta(days=7)).strftime("%Y-%m-%d")
    end = sim_date.strftime("%Y-%m-%d")
    calendar = api.get_calendar(start, end)

    prev_day = None
    for entry in calendar:
        entry_date = entry.date
        if hasattr(entry_date, "date"):
            entry_date = entry_date.date()
        if entry_date < sim_date:
            prev_day = entry_date

    # PDH/PDL
    if prev_day:
        pd_start = TZ.localize(datetime(prev_day.year, prev_day.month, prev_day.day, 1, 0))
        pd_end = TZ.localize(datetime(prev_day.year, prev_day.month, prev_day.day, 16, 58))
        bars = api.get_bars(ticker, TimeFrame.Minute,
                            start=pd_start.isoformat(), end=pd_end.isoformat()).df
        filtered = _filter_bars_by_time(bars, config.FULL_DAY_START, config.FULL_DAY_END)
        pdh, pdl = compute_pdh_pdl(filtered)
        levels["PDH"] = pdh
        levels["PDL"] = pdl

    # PMH/PML
    pm_start = TZ.localize(datetime(sim_date.year, sim_date.month, sim_date.day, 1, 0))
    pm_end = TZ.localize(datetime(sim_date.year, sim_date.month, sim_date.day, 6, 29))
    bars = api.get_bars(ticker, TimeFrame.Minute,
                        start=pm_start.isoformat(), end=pm_end.isoformat()).df
    filtered = _filter_bars_by_time(bars, config.PREMARKET_START, config.PREMARKET_END)
    pmh, pml = compute_pmh_pml(filtered)
    levels["PMH"] = pmh
    levels["PML"] = pml

    # ORH/ORL
    or_start = TZ.localize(datetime(sim_date.year, sim_date.month, sim_date.day, 6, 30))
    or_end = TZ.localize(datetime(sim_date.year, sim_date.month, sim_date.day, 6, 35))
    bars = api.get_bars(ticker, TimeFrame.Minute,
                        start=or_start.isoformat(), end=or_end.isoformat()).df
    filtered = _filter_bars_by_time(bars, config.OR_START, config.OR_END)
    orh, orl = compute_opening_range(filtered)
    levels["ORH"] = orh
    levels["ORL"] = orl

    return levels


def fetch_monitor_bars(api, ticker, sim_date):
    """Fetch 1-min bars for the monitor window (06:30–08:00)."""
    mon_start = TZ.localize(datetime(sim_date.year, sim_date.month, sim_date.day, 6, 30))
    mon_end = TZ.localize(datetime(sim_date.year, sim_date.month, sim_date.day, 8, 0))
    bars = api.get_bars(ticker, TimeFrame.Minute,
                        start=mon_start.isoformat(), end=mon_end.isoformat()).df
    if bars.empty:
        return bars
    return _filter_bars_by_time(bars, config.MONITOR_START, config.MONITOR_END)


def compute_bar_change_pct(open_price, close_price):
    """Compute percentage change from open to close."""
    if open_price == 0:
        return 0.0
    return ((close_price - open_price) / open_price) * 100


def simulate_multi(api, tickers, sim_date, speed=0.03):
    """Replay bars for multiple tickers on a unified timeline."""

    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  Multi-Ticker Simulation{RESET}")
    print(f"{DIM}  Date: {sim_date}  |  Benchmark: {BENCHMARK}{RESET}")
    print(f"{DIM}  Tech: {', '.join(t for t in tickers if t != BENCHMARK)}{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")

    # Compute levels for all tickers
    all_levels = {}
    print("Computing levels...", end="", flush=True)
    for ticker in tickers:
        try:
            all_levels[ticker] = get_levels_for_sim(api, ticker, sim_date)
            sys.stdout.write(f" {ticker}")
            sys.stdout.flush()
        except Exception as e:
            print(f"\n  Warning: {ticker} failed: {e}")
            all_levels[ticker] = {"PDH": None, "PDL": None, "PMH": None,
                                  "PML": None, "ORH": None, "ORL": None}
    print("\n")

    # Print levels table
    header = f"{'Ticker':<6} {'PDH':>10} {'PDL':>10} {'PMH':>10} {'PML':>10} {'ORH':>10} {'ORL':>10}"
    print(f"{BOLD}{header}{RESET}")
    print("-" * len(header))
    for ticker in tickers:
        levels = all_levels[ticker]
        color = MAGENTA if ticker == BENCHMARK else CYAN
        row = f"{color}{ticker:<6}{RESET}"
        for name in ["PDH", "PDL", "PMH", "PML", "ORH", "ORL"]:
            val = levels.get(name)
            row += f" {val:>10.2f}" if val is not None else f" {'--':>10}"
        print(row)
    print()

    # Fetch bars for all tickers
    all_bars = {}
    for ticker in tickers:
        all_bars[ticker] = fetch_monitor_bars(api, ticker, sim_date)

    # Build unified timeline (sorted unique timestamps across all tickers)
    all_timestamps = set()
    for ticker, bars in all_bars.items():
        if not bars.empty:
            for ts in bars.index:
                all_timestamps.add(ts)
    timeline = sorted(all_timestamps)

    if not timeline:
        print("No bars found for any ticker on this date.")
        return

    # Init alert states
    alert_states = {}
    for ticker in tickers:
        for level_name in all_levels[ticker]:
            alert_states[(ticker, level_name)] = AlertState()

    # Track session open price for % change
    session_open = {}

    print(f"{BOLD}Replaying {len(timeline)} time steps across {len(tickers)} tickers...{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")

    total_alerts = 0
    alerts_by_ticker = defaultdict(int)

    for ts in timeline:
        ts_pdt = ts.tz_convert(TZ) if hasattr(ts, 'tz_convert') else ts
        time_str = ts_pdt.strftime("%H:%M")

        # Collect bars at this timestamp for each ticker
        bars_at_ts = {}
        for ticker in tickers:
            if not all_bars[ticker].empty and ts in all_bars[ticker].index:
                bars_at_ts[ticker] = all_bars[ticker].loc[ts]

        # Track session opens
        for ticker, row in bars_at_ts.items():
            if ticker not in session_open:
                session_open[ticker] = row["open"]

        # Evaluate alerts for each ticker at this timestamp
        alerts_this_bar = []
        for ticker, row in bars_at_ts.items():
            for level_name, level_price in all_levels[ticker].items():
                if level_price is None:
                    continue
                state = alert_states[(ticker, level_name)]
                alert = evaluate_bar(
                    ticker, level_name, level_price,
                    row["high"], row["low"], row["close"],
                    state,
                )
                if alert:
                    # Reformat with historical timestamp
                    alert = format_alert(
                        ticker, level_name, level_price,
                        _get_event_type(state),
                        row["close"],
                        ts_pdt.to_pydatetime(),
                    )
                    alerts_this_bar.append((ticker, alert))

        # Print alerts with QQQ context
        if alerts_this_bar:
            # Get QQQ status at this moment
            qqq_ctx = ""
            if BENCHMARK in bars_at_ts and BENCHMARK in session_open:
                qqq_row = bars_at_ts[BENCHMARK]
                qqq_chg = compute_bar_change_pct(session_open[BENCHMARK], qqq_row["close"])
                chg_color = GREEN if qqq_chg >= 0 else RED
                qqq_ctx = f"  {DIM}[{BENCHMARK} {chg_color}{qqq_chg:+.2f}%{RESET}{DIM}]{RESET}"

            for ticker, alert in alerts_this_bar:
                # Add the stock's own % change
                stock_ctx = ""
                if ticker != BENCHMARK and ticker in bars_at_ts and ticker in session_open:
                    stock_row = bars_at_ts[ticker]
                    stock_chg = compute_bar_change_pct(session_open[ticker], stock_row["close"])
                    chg_color = GREEN if stock_chg >= 0 else RED
                    stock_ctx = f"  {DIM}[{ticker} {chg_color}{stock_chg:+.2f}%{RESET}{DIM}]{RESET}"

                print(f"{alert}{stock_ctx}{qqq_ctx}")
                total_alerts += 1
                alerts_by_ticker[ticker] += 1

        time.sleep(speed)

    # ===== SESSION SUMMARY =====
    print(f"\n{BOLD}{'=' * 70}{RESET}")
    print(f"{BOLD}  Simulation Summary — {sim_date}{RESET}")
    print(f"{BOLD}{'=' * 70}{RESET}\n")

    # Final % changes
    print(f"{BOLD}Session Performance (06:30 open → last bar):{RESET}")
    print(f"{'Ticker':<6} {'Change':>8}")
    print("-" * 16)
    for ticker in tickers:
        if ticker in session_open and not all_bars[ticker].empty:
            last_close = all_bars[ticker].iloc[-1]["close"]
            chg = compute_bar_change_pct(session_open[ticker], last_close)
            chg_color = GREEN if chg >= 0 else RED
            label_color = MAGENTA if ticker == BENCHMARK else CYAN
            print(f"{label_color}{ticker:<6}{RESET} {chg_color}{chg:+.2f}%{RESET}")
    print()

    # Alert breakdown
    print(f"{BOLD}Alerts by Ticker:{RESET}")
    print(f"{'Ticker':<6} {'Alerts':>7}")
    print("-" * 15)
    for ticker in tickers:
        if alerts_by_ticker[ticker] > 0:
            print(f"{ticker:<6} {alerts_by_ticker[ticker]:>7}")
    print(f"{'TOTAL':<6} {total_alerts:>7}\n")

    # Level detail
    print(f"{BOLD}Level Breakdown:{RESET}")
    print(f"{'Ticker':<6} {'Level':<6} {'Alerts':>7} {'Broken?':>8} {'Direction':>10}")
    print("-" * 42)
    for ticker in tickers:
        for level_name in ["PDH", "PDL", "PMH", "PML", "ORH", "ORL"]:
            if all_levels[ticker].get(level_name) is None:
                continue
            state = alert_states[(ticker, level_name)]
            if state.cross_count > 0:
                broken = "Yes" if state.has_broken else "No"
                direction = state.break_direction or "--"
                print(f"{ticker:<6} {level_name:<6} {state.cross_count:>7} {broken:>8} {direction:>10}")
    print()


def _get_event_type(state):
    """Reconstruct event type from state for reformatting alerts."""
    if state.cross_count == 1 and state.has_broken:
        if state.break_direction == "up":
            return "BREAK ABOVE"
        else:
            return "BREAK BELOW"
    else:
        if state.side == "above":
            return "RECLAIM (retest from below)"
        else:
            return "FADE (retest from above)"


def main():
    if not config.ALPACA_API_KEY or not config.ALPACA_API_SECRET:
        print("Error: Set ALPACA_API_KEY and ALPACA_API_SECRET environment variables.")
        sys.exit(1)

    api = REST(config.ALPACA_API_KEY, config.ALPACA_API_SECRET, config.BASE_URL)

    # Parse args
    sim_date = None
    custom_tickers = []

    for arg in sys.argv[1:]:
        # If it looks like a date, parse it
        try:
            sim_date = datetime.strptime(arg, "%Y-%m-%d").date()
        except ValueError:
            custom_tickers.append(arg.upper())

    # Build ticker list: QQQ benchmark + tech stocks
    if custom_tickers:
        tickers = [BENCHMARK] + [t for t in custom_tickers if t != BENCHMARK]
    else:
        tickers = [BENCHMARK] + DEFAULT_TECH

    # Default to most recent trading day
    if sim_date is None:
        sim_date = _find_previous_trading_day(api)
        if sim_date is None:
            print("Could not determine previous trading day.")
            sys.exit(1)
        print(f"Using most recent trading day: {sim_date}")

    simulate_multi(api, tickers, sim_date)


if __name__ == "__main__":
    main()
