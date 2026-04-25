"""
Websocket monitor for real-time 1-minute bar data from Alpaca.

Subscribes to bars for all watchlist tickers, computes opening range at 06:35,
and runs each bar through the alert engine.
"""

import json
import time
import threading
from collections import defaultdict
from datetime import datetime

import pytz
import websocket

import config
from alerts import evaluate_bar, check_proximity, find_clusters, AlertState

TZ = pytz.timezone(config.TIMEZONE)


class KeyLevelMonitor:
    """Connects to Alpaca websocket and monitors key levels."""

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

    def _hhmm(self, dt):
        return dt.hour * 100 + dt.minute

    def _avg_volume(self, ticker):
        """Get rolling average volume for a ticker."""
        hist = self._volume_history[ticker]
        if not hist:
            return 0
        lookback = min(len(hist), config.VOLUME_LOOKBACK)
        return sum(hist[-lookback:]) / lookback

    def _on_open(self, ws):
        auth_msg = {
            "action": "auth",
            "key": config.ALPACA_API_KEY,
            "secret": config.ALPACA_API_SECRET,
        }
        ws.send(json.dumps(auth_msg))

    def _on_message(self, ws, message):
        data = json.loads(message)

        for msg in data:
            msg_type = msg.get("T")

            if msg_type == "success":
                if msg.get("msg") == "authenticated":
                    # Subscribe to bars for all tickers
                    sub_msg = {
                        "action": "subscribe",
                        "bars": list(self.levels.keys()),
                    }
                    ws.send(json.dumps(sub_msg))
                    print(f"Subscribed to {len(self.levels)} tickers")
                continue

            if msg_type == "subscription":
                continue

            if msg_type == "b":
                # 1-minute bar
                self._process_bar(msg)

    def _process_bar(self, bar):
        ticker = bar["S"]
        if ticker not in self.levels:
            return

        # Parse bar timestamp and convert to PDT
        bar_time = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
        bar_pdt = bar_time.astimezone(TZ)
        current_hhmm = self._hhmm(bar_pdt)

        # Only process during monitor window
        if current_hhmm < config.MONITOR_START or current_hhmm >= config.MONITOR_END:
            return

        candle_open = bar["o"]
        candle_high = bar["h"]
        candle_low = bar["l"]
        candle_close = bar["c"]
        volume = bar.get("v", 0)

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

    def _on_error(self, ws, error):
        print(f"WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg):
        print(f"WebSocket closed: {close_status_code} {close_msg}")
        if self._running:
            self._reconnect()

    def _reconnect(self):
        delay = 1
        max_delay = 60
        while self._running:
            print(f"Reconnecting in {delay}s...")
            time.sleep(delay)
            try:
                self._connect()
                return
            except Exception as e:
                print(f"Reconnect failed: {e}")
                delay = min(delay * 2, max_delay)

    def _connect(self):
        self.ws = websocket.WebSocketApp(
            config.WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws.run_forever()

    def start(self):
        """Start the websocket monitor. Blocks until stop() is called."""
        self._running = True
        print(f"Connecting to {config.WS_URL}...")
        self._connect()

    def stop(self):
        """Stop the monitor."""
        self._running = False
        if hasattr(self, "ws"):
            self.ws.close()

    def start_background(self):
        """Start in a background thread. Returns the thread."""
        self._running = True
        t = threading.Thread(target=self._connect, daemon=True)
        t.start()
        return t
