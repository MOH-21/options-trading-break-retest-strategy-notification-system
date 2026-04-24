import os
from dotenv import load_dotenv

load_dotenv()

# Alpaca API credentials (set these as environment variables)
ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.environ.get("ALPACA_API_SECRET", "")
BASE_URL = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

# Data feed: "iex" (free) or "sip" (paid)
DATA_FEED = "iex"
WS_URL = f"wss://stream.data.alpaca.markets/v2/{DATA_FEED}"

# Watchlist
WATCHLIST = [
    "SPY", "QQQ", "SMH", "IWM", "DJI",
    "AAPL", "TSLA", "AMD", "NVDA", "PLTR",
    "MU", "NFLX", "MSFT", "AMZN", "META",
    "GOOG", "INTC",
]

# Timezone — all session boundaries are in America/Los_Angeles (PDT/PST)
TIMEZONE = "America/Los_Angeles"

# Session boundaries (HHMM format, matching PineScript exactly)
# Full day range for PDH/PDL: 01:00 – 16:58
FULL_DAY_START = 100   # 01:00
FULL_DAY_END = 1658    # 16:58

# Premarket for PMH/PML: 01:00 – 06:29
PREMARKET_START = 100  # 01:00
PREMARKET_END = 629    # 06:29 (< 630)

# RTH: 06:30 – 13:00
RTH_START = 630        # 06:30
RTH_END = 1300         # 13:00

# 5-min Opening Range: 06:30 – 06:34
OR_START = 630         # 06:30
OR_END = 634           # 06:34 (< 635)

# Monitor window (alerts active during this window)
MONITOR_START = 630    # 06:30
MONITOR_END = 800      # 08:00

# Alert settings
MAX_ALERTS_PER_LEVEL = 3
