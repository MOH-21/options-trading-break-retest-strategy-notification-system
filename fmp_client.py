"""
FMP (Financial Modeling Prep) API client.

Provides historical bars and trading calendar for the key levels monitor.
"""

from datetime import datetime, timedelta

import pytz
import requests

import config

FMP_BASE = "https://financialmodelingprep.com/stable"
ET = pytz.timezone("America/New_York")


def _get(endpoint, params=None):
    """Make an authenticated GET request to FMP."""
    if params is None:
        params = {}
    params["apikey"] = config.FMP_API_KEY
    resp = requests.get(f"{FMP_BASE}/{endpoint}", params=params)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict) and "Error Message" in data:
        raise ValueError(data["Error Message"])
    return data


def fetch_bars(symbol, timeframe="1min", start=None, end=None, extended=True):
    """Fetch historical bars from FMP.

    Returns list of dicts sorted oldest-first. Each dict has:
        date, open, high, low, close, volume, datetime_et (tz-aware)
    """
    params = {"symbol": symbol}
    if start:
        params["from"] = start.strftime("%Y-%m-%d") if hasattr(start, "strftime") else str(start)
    if end:
        params["to"] = end.strftime("%Y-%m-%d") if hasattr(end, "strftime") else str(end)
    if extended:
        params["extended"] = "true"

    try:
        data = _get(f"historical-chart/{timeframe}", params)
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 402:
            print(f"  Warning: {symbol} requires paid FMP plan, skipping")
            return []
        raise
    if not data or not isinstance(data, list):
        return []

    # FMP returns newest-first; reverse to oldest-first
    data.reverse()

    for bar in data:
        try:
            bar["datetime_et"] = ET.localize(
                datetime.strptime(bar["date"], "%Y-%m-%d %H:%M:%S")
            )
        except ValueError:
            # Daily bars: date-only format
            bar["datetime_et"] = ET.localize(
                datetime.strptime(bar["date"][:10], "%Y-%m-%d")
            )

    return data


def find_previous_trading_day(ref_date=None):
    """Find the most recent completed trading day before ref_date.

    Uses SPY daily bars to determine trading days.
    """
    if ref_date is None:
        ref_date = datetime.now(ET).date()

    start = ref_date - timedelta(days=10)
    params = {
        "symbol": "SPY",
        "from": start.strftime("%Y-%m-%d"),
        "to": ref_date.strftime("%Y-%m-%d"),
    }
    data = _get("historical-price-eod/full", params)
    if not data or not isinstance(data, list):
        return None

    # Data is newest-first; first match < ref_date is the answer
    for bar in data:
        d = datetime.strptime(bar["date"][:10], "%Y-%m-%d").date()
        if d < ref_date:
            return d
    return None


def get_quote(symbol):
    """Get current quote for a symbol. Returns dict or None."""
    data = _get("quote", {"symbol": symbol})
    if data and isinstance(data, list) and len(data) > 0:
        return data[0]
    return None
