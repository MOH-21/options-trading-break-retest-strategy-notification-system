# Key Levels Monitor

Real-time stock price level monitor that detects breaks and retests of key levels during the morning trading session. Uses Alpaca Markets API for both historical data and live WebSocket streaming.

## What It Does

Computes key price levels from historical data, then streams real-time 1-minute bars to alert you when price breaks through or retests a level.

**Levels tracked:**
- **PDH / PDL** — Previous Day High / Low (01:00–16:58 PDT)
- **PMH / PML** — Premarket High / Low (01:00–06:29 PDT)
- **ORH / ORL** — 5-Minute Opening Range High / Low (06:30–06:34 PDT)

**Alert types:**
- **BREAK ABOVE / BELOW** — First time price crosses through a level
- **RECLAIM** — Price retakes a level from below after breaking it
- **FADE** — Price drops back below a level from above after breaking it

Monitor window: **06:30 – 08:00 PDT** (configurable in `config.py`)

## Prerequisites

- Python 3.10+
- A free [Alpaca Markets](https://alpaca.markets/) account (paper trading works fine)
- Linux with `libnotify` for desktop notifications (optional)

## Setup

1. **Clone the repo:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/key-levels-monitor.git
   cd key-levels-monitor
   ```

2. **Create a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create a `.env` file** with your Alpaca API credentials:
   ```
   ALPACA_API_KEY=your_api_key_here
   ALPACA_API_SECRET=your_api_secret_here
   ALPACA_BASE_URL=https://paper-api.alpaca.markets
   ```
   Get your keys from the [Alpaca Dashboard](https://app.alpaca.markets/paper/dashboard/overview).

5. **(Optional) Install `libnotify` for desktop popup notifications:**
   ```bash
   # Ubuntu/Debian
   sudo apt install libnotify-bin

   # Fedora
   sudo dnf install libnotify

   # Arch
   sudo pacman -S libnotify
   ```

## Usage

### Live Monitoring

Run during market hours (06:30–08:00 PDT) to get real-time alerts:

```bash
python main.py
```

This will:
1. Connect to Alpaca and compute levels for all tickers in the watchlist
2. Stream 1-minute bars via WebSocket
3. Print color-coded alerts in the terminal
4. Send desktop notifications (Linux) so you can see alerts while charts are fullscreen
5. Auto-stop at 08:00 PDT

Press `Ctrl+C` for a session summary at any time.

### Historical Simulation

Replay a past trading day through the alert engine to backtest:

```bash
# Most recent trading day, default tickers (QQQ + top tech)
python simulate.py

# Specific date
python simulate.py 2026-04-23

# Specific date + custom tickers (QQQ always included as benchmark)
python simulate.py 2026-04-23 AMD TSLA
```

## Configuration

Edit `config.py` to customize:

| Setting | Default | Description |
|---------|---------|-------------|
| `WATCHLIST` | 17 tickers | Tickers to monitor |
| `DATA_FEED` | `"iex"` | `"iex"` (free) or `"sip"` (paid, real-time) |
| `MONITOR_START` | `630` | Alert window start (HHMM, PDT) |
| `MONITOR_END` | `800` | Alert window end (HHMM, PDT) |
| `MAX_ALERTS_PER_LEVEL` | `3` | Max alerts per ticker/level pair |
| `OR_START` / `OR_END` | `630` / `634` | Opening range window |

## Project Structure

```
key-levels-monitor/
├── main.py          # Entry point for live monitoring
├── simulate.py      # Historical simulation / backtesting
├── config.py        # All configuration (watchlist, time windows, etc.)
├── levels.py        # Level computation (PDH/PDL, PMH/PML, ORH/ORL)
├── alerts.py        # Alert engine (break/retest detection + formatting)
├── monitor.py       # WebSocket client for real-time bar streaming
├── requirements.txt
└── .env             # Your API keys (not tracked by git)
```

## Notes

- The free `iex` data feed has a 15-minute delay. For real-time data, switch `DATA_FEED` to `"sip"` in `config.py` (requires a paid Alpaca plan).
- All times are in Pacific time (PDT/PST). The session boundaries match common US equity trading conventions.
- Desktop notifications require a notification daemon running (standard on most Linux desktop environments).
