import os
from datetime import datetime

import pytz
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

# Timezone — set via .env or defaults to America/New_York (Eastern)
# Common US values: America/New_York, America/Chicago, America/Denver, America/Los_Angeles
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")


# --- Auto-compute session boundaries from timezone ---
# All boundaries are defined in US Eastern time (the market's native timezone)
# and converted to the user's local timezone automatically.
#
# Eastern reference times:
#   Extended hours start:    04:00 ET
#   Premarket end:           09:29 ET
#   Market open (RTH):       09:30 ET
#   Opening range end:       09:34 ET
#   RTH close:               16:00 ET
#   Extended hours end:      19:58 ET
#   Monitor window:          09:30 – 11:00 ET

def _et_to_local_hhmm(et_hour, et_minute):
    """Convert an Eastern time (HH:MM) to local HHMM in the configured timezone."""
    et = pytz.timezone("America/New_York")
    local = pytz.timezone(TIMEZONE)
    # Use a recent weekday to get correct DST offset
    ref = datetime(2026, 4, 27, et_hour, et_minute)
    et_time = et.localize(ref)
    local_time = et_time.astimezone(local)
    return local_time.hour * 100 + local_time.minute


# Full day range for PDH/PDL
FULL_DAY_START = _et_to_local_hhmm(4, 0)
FULL_DAY_END = _et_to_local_hhmm(19, 58)

# Premarket for PMH/PML
PREMARKET_START = _et_to_local_hhmm(4, 0)
PREMARKET_END = _et_to_local_hhmm(9, 29)

# RTH
RTH_START = _et_to_local_hhmm(9, 30)
RTH_END = _et_to_local_hhmm(16, 0)

# 5-min Opening Range
OR_START = _et_to_local_hhmm(9, 30)
OR_END = _et_to_local_hhmm(9, 34)

# Monitor window (alerts active during this window)
MONITOR_START = _et_to_local_hhmm(9, 30)
MONITOR_END = _et_to_local_hhmm(11, 0)

# Alert settings
MAX_ALERTS_PER_LEVEL = 3
