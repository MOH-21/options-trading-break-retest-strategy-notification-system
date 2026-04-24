# CLAUDE.md

## Project overview

Real-time stock key level monitor. Computes PDH/PDL, PMH/PML, ORH/ORL from Alpaca historical data, then streams 1-min bars via WebSocket to detect breaks and retests during the morning session. Sends desktop notifications and logs sessions to file.

## Architecture

- `config.py` — All configuration. Loads `.env`, defines watchlist, auto-computes session boundaries from user's timezone (defined in Eastern, converted at import time).
- `levels.py` — Computes key levels from Alpaca REST API. PDH/PDL from previous trading day, PMH/PML from current day premarket, ORH/ORL computed live.
- `alerts.py` — Stateful alert engine. `AlertState` tracks each ticker/level pair. `evaluate_bar()` detects side changes (break/reclaim/fade). Pure logic, no I/O.
- `monitor.py` — WebSocket client (`KeyLevelMonitor`). Connects to Alpaca stream, accumulates opening range bars, feeds each bar to `evaluate_bar()`. Accepts `on_alert` callback.
- `main.py` — Entry point for live monitoring. Wires everything together, handles notifications (Linux/macOS/Windows), session logging, graceful shutdown.
- `simulate.py` — Replays historical bars through the alert engine for backtesting. Standalone, does not use `monitor.py`.

## Key conventions

- All session boundaries use HHMM integer format (e.g., `930` = 9:30 AM). Defined in Eastern time in `config.py`, auto-converted to user's timezone.
- Alert flow: Alpaca bar → `monitor._process_bar()` → `alerts.evaluate_bar()` → `on_alert` callback → terminal + notification + log file.
- `AlertState` is per (ticker, level_name) pair. Tracks cross count, side, break direction. Caps at `MAX_ALERTS_PER_LEVEL`.
- Opening range (ORH/ORL) is accumulated from live bars during 9:30–9:34 ET, locked at 9:35.

## Running

```bash
source venv/bin/activate
python main.py           # live monitoring
python simulate.py       # simulate most recent trading day
python simulate.py 2026-04-23 AMD TSLA   # specific date + tickers
```

Requires `.env` with `ALPACA_API_KEY`, `ALPACA_API_SECRET`, `ALPACA_BASE_URL`, and `TIMEZONE`.

## Common tasks

- **Add a ticker**: Add to `WATCHLIST` in `config.py`.
- **Change monitor window**: Modify `MONITOR_START`/`MONITOR_END` Eastern times in `config.py` (the `_et_to_local_hhmm` calls).
- **Change alert cap**: Modify `MAX_ALERTS_PER_LEVEL` in `config.py`.
- **Add a new level type**: Add computation in `levels.py`, include in the levels dict, alert engine picks it up automatically.
- **Change data feed**: Set `DATA_FEED` to `"sip"` in `config.py` for real-time (paid).

## Things to know

- The free `iex` feed has ~15-minute delay. Only `sip` is real-time.
- `simulate.py` has its own level computation (`get_levels_for_sim`) separate from `levels.py`'s `get_levels_for_ticker` because simulation needs levels for arbitrary historical dates, not "today."
- Desktop notifications silently skip if the OS notification tool isn't installed.
- Session logs go to `logs/` (gitignored), plain text with ANSI stripped.
- `.env` is gitignored — never commit credentials.
