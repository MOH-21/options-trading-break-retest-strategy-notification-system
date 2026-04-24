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
RESET = "\033[0m"
BOLD = "\033[1m"


@dataclass
class AlertState:
    """Tracks alert state for one ticker/level pair."""
    cross_count: int = 0
    side: Optional[str] = None        # "above" or "below"
    has_broken: bool = False
    break_direction: Optional[str] = None  # "up" or "down"


def evaluate_bar(ticker, level_name, level_price, candle_high, candle_low,
                 candle_close, alert_state):
    """Evaluate a 1-min bar against a level and return an alert string or None.

    Logic:
    1. Determine which side of the level price closed on
    2. Compare to previous side to detect breaks and retests
    3. Cap alerts at MAX_ALERTS_PER_LEVEL
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

    return format_alert(ticker, level_name, level_price, event_type,
                        candle_close, datetime.now())


def format_alert(ticker, level_name, level_price, event_type, candle_close,
                 timestamp):
    """Format a color-coded terminal alert string."""
    time_str = timestamp.strftime("%H:%M:%S")

    if "ABOVE" in event_type or "RECLAIM" in event_type:
        color = GREEN
    elif "BELOW" in event_type or "FADE" in event_type:
        color = RED
    else:
        color = YELLOW

    return (
        f"{BOLD}[{time_str}]{RESET} "
        f"{CYAN}{ticker:<5}{RESET} | "
        f"{level_name} ({level_price:.2f}) | "
        f"{color}{event_type}{RESET} | "
        f"Close: {candle_close:.2f}"
    )
