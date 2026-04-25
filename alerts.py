"""
Alert state tracking and formatting.

Tracks price position relative to each key level, detects breaks and retests,
and formats color-coded terminal alerts.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import config


# ANSI color codes
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
MAGENTA = "\033[95m"
DIM = "\033[2m"
RESET = "\033[0m"
BOLD = "\033[1m"


WICK_THRESHOLD = 0.25  # 25% of candle range to count as a notable wick


@dataclass
class AlertState:
    """Tracks alert state for one ticker/level pair."""
    cross_count: int = 0
    side: Optional[str] = None        # "above" or "below"
    has_broken: bool = False
    break_direction: Optional[str] = None  # "up" or "down"
    proximity_fired: bool = False     # only fire proximity alert once


def analyze_price_action(candle_open, candle_high, candle_low, candle_close):
    """Classify a candle's price action as STRONG, WEAK, or INDECISION.

    Returns (label, detail) where detail adds wick context if notable.
    """
    body = abs(candle_close - candle_open)
    total_range = candle_high - candle_low

    # Doji / no movement
    if total_range == 0 or body < total_range * 0.05:
        return "INDECISION", None

    upper_wick = candle_high - max(candle_open, candle_close)
    lower_wick = min(candle_open, candle_close) - candle_low

    is_green = candle_close > candle_open
    notable_lower = lower_wick >= total_range * WICK_THRESHOLD
    notable_upper = upper_wick >= total_range * WICK_THRESHOLD

    if is_green:
        detail = "buyer wick" if notable_lower else None
        return "STRONG", detail
    else:
        detail = "seller wick" if notable_upper else None
        return "WEAK", detail


# --- Volume confirmation ---

def classify_volume(bar_volume, avg_volume):
    """Classify bar volume relative to session average.

    Returns a tag string or None if volume confirmation is disabled.
    """
    if not config.VOLUME_CONFIRMATION or avg_volume <= 0:
        return None
    ratio = bar_volume / avg_volume
    if ratio >= config.VOLUME_HIGH_MULT:
        return "HIGH VOL"
    else:
        return "LOW VOL"


# --- Level clustering ---

def find_clusters(levels_dict):
    """Group levels that are within CLUSTER_PCT of each other.

    Args:
        levels_dict: {level_name: price, ...} for a single ticker

    Returns:
        dict: {level_name: [other_level_names_in_same_cluster]}
        Empty lists mean the level is standalone.
    """
    if not config.LEVEL_CLUSTERING:
        return {name: [] for name in levels_dict}

    names = []
    prices = []
    for name, price in levels_dict.items():
        if price is not None:
            names.append(name)
            prices.append(price)

    clusters = {name: [] for name in levels_dict}
    threshold = config.CLUSTER_PCT / 100.0

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            mid = (prices[i] + prices[j]) / 2
            if mid > 0 and abs(prices[i] - prices[j]) / mid <= threshold:
                clusters[names[i]].append(names[j])
                clusters[names[j]].append(names[i])

    return clusters


# --- Proximity alerts ---

def check_proximity(ticker, level_name, level_price, candle_close, alert_state,
                    timestamp=None):
    """Check if price is approaching a level without crossing it.

    Returns a formatted proximity alert string or None.
    """
    if not config.PROXIMITY_ALERTS or level_price is None:
        return None

    # Don't fire if already broken or proximity already alerted
    if alert_state.has_broken or alert_state.proximity_fired:
        return None

    # Don't fire if we haven't established a side yet
    if alert_state.side is None:
        return None

    distance_pct = abs(candle_close - level_price) / level_price * 100

    if distance_pct <= config.PROXIMITY_PCT and distance_pct > 0:
        alert_state.proximity_fired = True
        direction = "from below" if candle_close < level_price else "from above"
        return format_proximity_alert(
            ticker, level_name, level_price, candle_close,
            distance_pct, direction, timestamp or datetime.now()
        )

    return None


# --- Core alert evaluation ---

def evaluate_bar(ticker, level_name, level_price, candle_open, candle_high,
                 candle_low, candle_close, alert_state, volume=None,
                 avg_volume=0, cluster_peers=None):
    """Evaluate a 1-min bar against a level and return an alert string or None.

    Logic:
    1. Determine which side of the level price closed on
    2. Compare to previous side to detect breaks and retests
    3. Analyze price action (strong/weak/indecision)
    4. Add volume and cluster context
    5. Cap alerts at MAX_ALERTS_PER_LEVEL
    """
    if level_price is None:
        return None

    # Already capped
    if alert_state.cross_count >= config.MAX_ALERTS_PER_LEVEL:
        return None

    # Determine current side based on close
    current_side = "above" if candle_close > level_price else "below"

    # First bar — establish baseline, no alert
    if alert_state.side is None:
        alert_state.side = current_side
        return None

    prev_side = alert_state.side

    # No side change — no event
    if current_side == prev_side:
        return None

    # Side changed — classify the event
    alert_state.side = current_side
    alert_state.cross_count += 1

    if not alert_state.has_broken:
        # First break through this level
        alert_state.has_broken = True
        alert_state.break_direction = "up" if current_side == "above" else "down"
        event_type = "BREAK ABOVE" if current_side == "above" else "BREAK BELOW"
    else:
        # Already broken once — this is a retest / reclaim
        if current_side == "above":
            event_type = "RECLAIM (retest from below)"
        else:
            event_type = "FADE (retest from above)"

    pa_label, pa_detail = analyze_price_action(
        candle_open, candle_high, candle_low, candle_close
    )

    vol_tag = classify_volume(volume, avg_volume) if volume is not None else None
    cluster_info = cluster_peers if cluster_peers else None

    return format_alert(ticker, level_name, level_price, event_type,
                        candle_close, datetime.now(), pa_label, pa_detail,
                        vol_tag, cluster_info)


# --- Formatting ---

def format_alert(ticker, level_name, level_price, event_type, candle_close,
                 timestamp, pa_label=None, pa_detail=None, vol_tag=None,
                 cluster_info=None):
    """Format a color-coded terminal alert string."""
    time_str = timestamp.strftime("%H:%M:%S")

    if "ABOVE" in event_type or "RECLAIM" in event_type:
        color = GREEN
    elif "BELOW" in event_type or "FADE" in event_type:
        color = RED
    else:
        color = YELLOW

    # Level label — include cluster if applicable
    if cluster_info:
        level_label = f"{level_name}+{'+'.join(cluster_info)} ({level_price:.2f})"
    else:
        level_label = f"{level_name} ({level_price:.2f})"

    # Price action tag
    pa_tag = ""
    if pa_label:
        if pa_label == "STRONG":
            pa_color = GREEN
        elif pa_label == "WEAK":
            pa_color = RED
        else:
            pa_color = YELLOW
        detail_str = f" ({pa_detail})" if pa_detail else ""
        pa_tag = f" | {pa_color}{pa_label}{detail_str}{RESET}"

    # Volume tag
    vol_str = ""
    if vol_tag:
        vol_color = GREEN if vol_tag == "HIGH VOL" else DIM
        vol_str = f" | {vol_color}{vol_tag}{RESET}"

    return (
        f"{BOLD}[{time_str}]{RESET} "
        f"{CYAN}{ticker:<5}{RESET} | "
        f"{level_label} | "
        f"{color}{event_type}{RESET} | "
        f"Close: {candle_close:.2f}"
        f"{pa_tag}{vol_str}"
    )


def format_proximity_alert(ticker, level_name, level_price, candle_close,
                           distance_pct, direction, timestamp):
    """Format a proximity warning alert."""
    time_str = timestamp.strftime("%H:%M:%S")
    return (
        f"{BOLD}[{time_str}]{RESET} "
        f"{CYAN}{ticker:<5}{RESET} | "
        f"{level_name} ({level_price:.2f}) | "
        f"{MAGENTA}APPROACHING {direction} ({distance_pct:.2f}%){RESET} | "
        f"Close: {candle_close:.2f}"
    )
