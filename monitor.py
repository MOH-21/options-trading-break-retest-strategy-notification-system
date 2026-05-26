"""
Polling monitor for real-time 1-minute bar data from FMP.

Polls FMP REST API every 60 seconds for all watchlist tickers,
computes opening range at 06:35, and runs each bar through the alert engine.
"""

import time
import threading
from collections import defaultdict
from datetime import datetime

import pytz

import config
import fmp_client
from alerts import evaluate_bar, check_proximity, find_clusters, AlertState

TZ = pytz.timezone(config.TIMEZONE)


class KeyLevelMonitor:
    """Polls FMP for 1-min bars and monitors key levels."""

    def __init__(self, levels, on_alert=None):
        """
        Args:
            levels: dict of {ticker: {"PDH": float, ...}} from levels.py
            on_alert: optional callback(alert_string), defaults to print
        """
        self.levels = levels
        self.on_alert = on_alert or print
        self._running = False

        # Alert state: {(ticker, level_name): AlertState}
        self.alert_states = {}
        for ticker, ticker_levels in levels.items():
            for level_name in ticker_levels:
                self.alert_states[(ticker, level_name)] = AlertState()

        # Opening range accumulators: {ticker: {"high": float, "low": float}}
        self._or_bars = {t: {"high": None, "low": None} for t in levels}
        self._or_locked = {t: False for t in levels}

        # Volume tracking: {ticker: [volumes]}
        self._volume_history = defaultdict(list)

        # Level clusters: {ticker: {level_name: [peer_names]}}
        self._clusters = {}
        for ticker in levels:
            self._clusters[ticker] = find_clusters(levels[ticker])

        # Track last processed bar per ticker
        self._last_seen = {}

    def _hhmm(self, dt):
        return dt.hour * 100 + dt.minute

    def _avg_volume(self, ticker):
        """Get rolling average volume for a ticker."""
        hist = self._volume_history[ticker]
        if not hist:
            return 0
        lookback = min(len(hist), config.VOLUME_LOOKBACK)
        return sum(hist[-lookback:]) / lookback

    def _poll_loop(self):
        """Main polling loop — fetches 1-min bars every 60s for all tickers."""
        while self._running:
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            for ticker in self.levels:
                if not self._running:
                    break
                try:
                    bars = fmp_client.fetch_bars(
                        ticker, "1min", start=today, end=today
                    )
                    last_seen = self._last_seen.get(ticker)
                    for bar in bars:
                        if last_seen and bar["date"] <= last_seen:
                            continue
                        self._process_bar(ticker, bar)
                    if bars:
                        self._last_seen[ticker] = bars[-1]["date"]
                except Exception as e:
                    print(f"  Poll error for {ticker}: {e}")

            if self._running:
                time.sleep(60)

    def _process_bar(self, ticker, bar):
        """Process a single 1-min bar through the alert engine."""
        bar_local = bar["datetime_et"].astimezone(TZ)
        current_hhmm = self._hhmm(bar_local)

        # Only process during monitor window
        if current_hhmm < config.MONITOR_START or current_hhmm >= config.MONITOR_END:
            return

        candle_open = bar["open"]
        candle_high = bar["high"]
        candle_low = bar["low"]
        candle_close = bar["close"]
        volume = bar.get("volume", 0)

        # Track volume
        self._volume_history[ticker].append(volume)
        avg_vol = self._avg_volume(ticker)

        # --- Opening Range accumulation (06:30 - 06:34) ---
        if not self._or_locked[ticker]:
            if config.OR_START <= current_hhmm <= config.OR_END:
                or_data = self._or_bars[ticker]
                if or_data["high"] is None:
                    or_data["high"] = candle_high
                    or_data["low"] = candle_low
                else:
                    or_data["high"] = max(or_data["high"], candle_high)
                    or_data["low"] = min(or_data["low"], candle_low)

            # Lock at 06:35
            if current_hhmm >= config.OR_END + 1:
                self._or_locked[ticker] = True
                or_data = self._or_bars[ticker]
                if or_data["high"] is not None:
                    self.levels[ticker]["ORH"] = or_data["high"]
                    self.levels[ticker]["ORL"] = or_data["low"]
                    # Initialize alert states for OR levels
                    self.alert_states[(ticker, "ORH")] = AlertState()
                    self.alert_states[(ticker, "ORL")] = AlertState()
                    # Recompute clusters with OR levels
                    self._clusters[ticker] = find_clusters(self.levels[ticker])
                    print(
                        f"  OR locked for {ticker}: "
                        f"ORH={or_data['high']:.2f}, ORL={or_data['low']:.2f}"
                    )

        # --- Evaluate against all levels ---
        for level_name, level_price in self.levels[ticker].items():
            if level_price is None:
                continue

            state = self.alert_states[(ticker, level_name)]
            cluster_peers = self._clusters[ticker].get(level_name, [])

            # Check proximity first (fires before a break)
            prox_alert = check_proximity(
                ticker, level_name, level_price, candle_close, state
            )
            if prox_alert:
                self.on_alert(prox_alert)

            alert = evaluate_bar(
                ticker, level_name, level_price,
                candle_open, candle_high, candle_low, candle_close,
                state,
                volume=volume,
                avg_volume=avg_vol,
                cluster_peers=cluster_peers or None,
            )
            if alert:
                self.on_alert(alert)

    def start(self):
        """Start the polling monitor. Blocks until stop() is called."""
        self._running = True
        print("Starting FMP polling monitor (60s interval)...")
        self._poll_loop()

    def stop(self):
        """Stop the monitor."""
        self._running = False

    def start_background(self):
        """Start in a background thread. Returns the thread."""
        self._running = True
        t = threading.Thread(target=self._poll_loop, daemon=True)
        t.start()
        return t
