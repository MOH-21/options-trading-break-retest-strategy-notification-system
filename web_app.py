"""
Key Levels Monitor — Web App

Local Flask web app that runs the key levels monitor and streams alerts
to the browser in real time via Server-Sent Events.

    python web_app.py
    → opens http://localhost:5000 in your browser
"""

import importlib
import json
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime

import pytz
from dotenv import dotenv_values, set_key
from flask import Flask, Response, jsonify, redirect, render_template_string, request

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_ENV_PATH = os.path.join(_BASE_DIR, '.env')
_LOG_DIR = os.path.join(_BASE_DIR, 'logs')

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def _strip_ansi(text):
    return _ANSI_RE.sub('', text)


def _send_notification(title, body):
    """Send a desktop notification (Linux/macOS/Windows)."""
    system = platform.system()
    try:
        if system == "Linux":
            subprocess.Popen(
                ["notify-send", "-u", "critical", "-t", "10000", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif system == "Darwin":
            script = f'display notification "{body}" with title "{title}" sound name "Glass"'
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif system == "Windows":
            ps_script = (
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
                "ContentType = WindowsRuntime] > $null; "
                "$template = [Windows.UI.Notifications.ToastNotificationManager]::"
                "GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
                "$textNodes = $template.GetElementsByTagName('text'); "
                f"$textNodes.Item(0).AppendChild($template.CreateTextNode('{title}')) > $null; "
                f"$textNodes.Item(1).AppendChild($template.CreateTextNode('{body}')) > $null; "
                "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
                "[Windows.UI.Notifications.ToastNotificationManager]::"
                "CreateToastNotifier('Key Levels Monitor').Show($toast)"
            )
            subprocess.Popen(
                ["powershell", "-Command", ps_script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Session logging
# ---------------------------------------------------------------------------

class SessionLog:
    def __init__(self):
        self._file = None
        self.path = None

    def open(self):
        os.makedirs(_LOG_DIR, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d_%H%M")
        self.path = os.path.join(_LOG_DIR, f"session_{date_str}.txt")
        self._file = open(self.path, "w")

    def write(self, text):
        if self._file:
            self._file.write(_strip_ansi(text) + "\n")
            self._file.flush()

    def close(self):
        if self._file:
            self._file.close()
            self._file = None


# ---------------------------------------------------------------------------
# App State — monitor lifecycle + alert fan-out
# ---------------------------------------------------------------------------

class AppState:
    MAX_HISTORY = 200

    def __init__(self):
        self.running = False
        self.starting = False
        self.monitor = None
        self.ws_thread = None
        self.alert_history = []
        self.levels = {}
        self.session_log = SessionLog()
        self._lock = threading.Lock()
        self._subscribers = []
        self._error = None

    def subscribe(self):
        q = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def _broadcast(self, data):
        with self._lock:
            for q in self._subscribers:
                q.put(data)

    def on_alert(self, alert_string):
        """Callback from monitor thread."""
        plain = _strip_ansi(alert_string)
        with self._lock:
            self.alert_history.append(plain)
            if len(self.alert_history) > self.MAX_HISTORY:
                self.alert_history = self.alert_history[-self.MAX_HISTORY:]

        self._broadcast(json.dumps({"type": "alert", "text": plain}))
        self.session_log.write(plain)

        # Desktop notification
        parts = plain.split(" | ", 1)
        title = parts[0].strip() if parts else "Key Level Alert"
        body = parts[1] if len(parts) > 1 else plain
        _send_notification(title, body)

    def start_monitor(self):
        if self.running or self.starting:
            return "Already running"
        self.starting = True
        self._error = None

        try:
            import config as cfg
            importlib.reload(cfg)

            if not cfg.FMP_API_KEY:
                self._error = "Missing FMP API key. Go to Settings to configure."
                return self._error

            self._broadcast(json.dumps({"type": "status", "text": "Computing levels..."}))

            self.session_log.open()
            self.session_log.write(f"Session started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            self.session_log.write(f"Watchlist: {', '.join(cfg.WATCHLIST)}")

            import fmp_client
            from levels import get_levels_for_ticker
            from monitor import KeyLevelMonitor

            # Validate FMP connection
            fmp_client.get_quote("SPY")

            all_levels = {}
            for ticker in cfg.WATCHLIST:
                try:
                    levels = get_levels_for_ticker(ticker)
                    all_levels[ticker] = levels
                    self._broadcast(json.dumps({
                        "type": "status",
                        "text": f"Computed levels for {ticker}"
                    }))
                except Exception as e:
                    self.session_log.write(f"Warning: Failed {ticker}: {e}")
                    all_levels[ticker] = {
                        "PDH": None, "PDL": None, "PMH": None, "PML": None,
                        "ORH": None, "ORL": None,
                    }

            self.levels = all_levels

            # Log levels
            for ticker in sorted(all_levels.keys()):
                lvls = all_levels[ticker]
                parts = [f"{k}={v:.2f}" if v else f"{k}=--" for k, v in lvls.items()]
                self.session_log.write(f"  {ticker}: {', '.join(parts)}")

            self.monitor = KeyLevelMonitor(all_levels, on_alert=self.on_alert)
            self.running = True
            self.ws_thread = self.monitor.start_background()

            self._broadcast(json.dumps({"type": "started", "text": "Monitor running"}))
            _send_notification("Key Levels Monitor", f"Monitoring {len(cfg.WATCHLIST)} tickers.")

            threading.Thread(target=self._auto_stop_loop, daemon=True).start()
            return None

        except Exception as e:
            self._error = str(e)
            self.session_log.close()
            return self._error
        finally:
            self.starting = False

    def stop_monitor(self):
        if not self.running:
            return
        self.running = False
        if self.monitor:
            self.monitor.stop()
        self.session_log.write(f"Session ended: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.session_log.close()
        self._broadcast(json.dumps({"type": "stopped", "text": "Monitor stopped"}))
        self.monitor = None
        self.ws_thread = None

    def _auto_stop_loop(self):
        import config as cfg
        tz = pytz.timezone(cfg.TIMEZONE)
        while self.running:
            now = datetime.now(tz)
            current_hhmm = now.hour * 100 + now.minute
            if current_hhmm >= cfg.MONITOR_END:
                self.stop_monitor()
                _send_notification("Key Levels Monitor", "Monitor window closed.")
                break
            time.sleep(5)

    def get_status(self):
        levels_data = {}
        for ticker, lvls in self.levels.items():
            levels_data[ticker] = {
                k: round(v, 2) if v is not None else None
                for k, v in lvls.items()
            }
        return {
            "running": self.running,
            "starting": self.starting,
            "alert_count": len(self.alert_history),
            "levels": levels_data,
            "error": self._error,
        }


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)
state = AppState()

_TIMEZONES = [
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
]


@app.route("/")
def dashboard():
    return render_template_string(DASHBOARD_HTML)


@app.route("/settings")
def settings_page():
    env = dotenv_values(_ENV_PATH) if os.path.exists(_ENV_PATH) else {}
    return render_template_string(SETTINGS_HTML, env=env, timezones=_TIMEZONES)


@app.route("/settings", methods=["POST"])
def save_settings():
    if not os.path.exists(_ENV_PATH):
        with open(_ENV_PATH, "w") as f:
            f.write("")

    set_key(_ENV_PATH, "FMP_API_KEY", request.form.get("fmp_api_key", "").strip())
    set_key(_ENV_PATH, "TIMEZONE", request.form.get("timezone", "America/New_York"))

    wl = request.form.get("watchlist", "").strip()
    tickers = ",".join(s.strip() for s in wl.replace("\n", ",").split(",") if s.strip())
    if tickers:
        set_key(_ENV_PATH, "WATCHLIST", tickers)

    for key in ("VOLUME_CONFIRMATION", "LEVEL_CLUSTERING", "PROXIMITY_ALERTS"):
        val = "true" if request.form.get(key) else "false"
        set_key(_ENV_PATH, key, val)

    return redirect("/settings?saved=1")


@app.route("/start", methods=["POST"])
def start():
    def _run():
        state.start_monitor()
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "starting"})


@app.route("/stop", methods=["POST"])
def stop():
    state.stop_monitor()
    return jsonify({"status": "stopped"})


@app.route("/status")
def status():
    return jsonify(state.get_status())


@app.route("/stream")
def stream():
    def event_stream():
        q = state.subscribe()
        try:
            # Send existing history on connect
            with state._lock:
                history = list(state.alert_history)
            for alert in history:
                yield f"data: {json.dumps({'type': 'alert', 'text': alert})}\n\n"
            # Stream new events
            while True:
                try:
                    data = q.get(timeout=30)
                    yield f"data: {data}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except GeneratorExit:
            state.unsubscribe(q)

    return Response(event_stream(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------------------
# HTML Templates
# ---------------------------------------------------------------------------

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Key Levels Monitor</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0f1117; color: #e4e4e7; font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace; font-size: 14px; }
  .container { max-width: 960px; margin: 0 auto; padding: 20px; }
  header { display: flex; align-items: center; justify-content: space-between; padding: 16px 0; border-bottom: 1px solid #27272a; margin-bottom: 20px; }
  h1 { font-size: 20px; font-weight: 600; }
  .status { display: flex; align-items: center; gap: 8px; font-size: 13px; }
  .dot { width: 10px; height: 10px; border-radius: 50%; }
  .dot.stopped { background: #71717a; }
  .dot.running { background: #22c55e; box-shadow: 0 0 8px #22c55e80; }
  .dot.starting { background: #eab308; box-shadow: 0 0 8px #eab30880; }
  .dot.error { background: #ef4444; }
  .controls { display: flex; gap: 8px; margin-bottom: 20px; }
  button { background: #27272a; color: #e4e4e7; border: 1px solid #3f3f46; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-family: inherit; font-size: 13px; }
  button:hover { background: #3f3f46; }
  button:disabled { opacity: 0.4; cursor: not-allowed; }
  button.primary { background: #22c55e; color: #0f1117; border-color: #22c55e; }
  button.primary:hover { background: #16a34a; }
  button.danger { background: #dc2626; color: #fff; border-color: #dc2626; }
  button.danger:hover { background: #b91c1c; }
  a.btn { text-decoration: none; display: inline-block; background: #27272a; color: #e4e4e7; border: 1px solid #3f3f46; padding: 8px 16px; border-radius: 6px; font-family: inherit; font-size: 13px; }
  a.btn:hover { background: #3f3f46; }
  .section { margin-bottom: 20px; }
  .section h2 { font-size: 14px; color: #a1a1aa; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th { text-align: left; padding: 6px 10px; color: #a1a1aa; border-bottom: 1px solid #27272a; }
  td { padding: 6px 10px; border-bottom: 1px solid #1a1a2e; }
  td.ticker { color: #67e8f9; font-weight: 600; }
  #levels-table { display: none; }
  #alert-feed { background: #18181b; border: 1px solid #27272a; border-radius: 8px; height: 400px; overflow-y: auto; padding: 12px; }
  .alert-line { padding: 4px 0; border-bottom: 1px solid #1f1f23; line-height: 1.5; word-break: break-word; }
  .alert-line:last-child { border-bottom: none; }
  .status-msg { color: #a1a1aa; font-style: italic; }
  .empty-state { color: #52525b; text-align: center; padding: 60px 0; }
</style>
</head>
<body>
<div class="container">
  <header>
    <h1>Key Levels Monitor</h1>
    <div class="status">
      <span class="dot stopped" id="status-dot"></span>
      <span id="status-text">Stopped</span>
    </div>
  </header>

  <div class="controls">
    <button class="primary" id="btn-start" onclick="startMonitor()">Start</button>
    <button class="danger" id="btn-stop" onclick="stopMonitor()" disabled>Stop</button>
    <a class="btn" href="/settings">Settings</a>
  </div>

  <div class="section" id="levels-table">
    <h2>Levels</h2>
    <table>
      <thead><tr><th>Ticker</th><th>PDH</th><th>PDL</th><th>PMH</th><th>PML</th><th>ORH</th><th>ORL</th></tr></thead>
      <tbody id="levels-body"></tbody>
    </table>
  </div>

  <div class="section">
    <h2>Alert Feed</h2>
    <div id="alert-feed">
      <div class="empty-state" id="empty-msg">No alerts yet. Click Start to begin monitoring.</div>
    </div>
  </div>
</div>

<script>
const feed = document.getElementById('alert-feed');
const emptyMsg = document.getElementById('empty-msg');
const dot = document.getElementById('status-dot');
const statusText = document.getElementById('status-text');
const btnStart = document.getElementById('btn-start');
const btnStop = document.getElementById('btn-stop');
const levelsTable = document.getElementById('levels-table');
const levelsBody = document.getElementById('levels-body');
let evtSource = null;

function setStatus(s) {
  dot.className = 'dot ' + s;
  statusText.textContent = s.charAt(0).toUpperCase() + s.slice(1);
  btnStart.disabled = (s === 'running' || s === 'starting');
  btnStop.disabled = (s !== 'running');
}

function addAlert(text) {
  if (emptyMsg) emptyMsg.remove();
  const div = document.createElement('div');
  div.className = 'alert-line';
  div.textContent = text;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

function addStatus(text) {
  if (emptyMsg) emptyMsg.remove();
  const div = document.createElement('div');
  div.className = 'alert-line status-msg';
  div.textContent = text;
  feed.appendChild(div);
  feed.scrollTop = feed.scrollHeight;
}

function renderLevels(levels) {
  if (!levels || Object.keys(levels).length === 0) {
    levelsTable.style.display = 'none';
    return;
  }
  levelsBody.innerHTML = '';
  const tickers = Object.keys(levels).sort();
  for (const t of tickers) {
    const l = levels[t];
    const hasData = Object.values(l).some(v => v !== null);
    if (!hasData) continue;
    const row = document.createElement('tr');
    row.innerHTML = '<td class="ticker">' + t + '</td>' +
      ['PDH','PDL','PMH','PML','ORH','ORL'].map(k =>
        '<td>' + (l[k] !== null ? l[k].toFixed(2) : '--') + '</td>'
      ).join('');
    levelsBody.appendChild(row);
  }
  levelsTable.style.display = levelsBody.children.length ? 'block' : 'none';
}

function connectSSE() {
  if (evtSource) evtSource.close();
  evtSource = new EventSource('/stream');
  evtSource.onmessage = function(e) {
    const msg = JSON.parse(e.data);
    if (msg.type === 'alert') {
      addAlert(msg.text);
    } else if (msg.type === 'status') {
      addStatus(msg.text);
    } else if (msg.type === 'started') {
      setStatus('running');
      fetchStatus();
    } else if (msg.type === 'stopped') {
      setStatus('stopped');
    }
  };
}

function startMonitor() {
  setStatus('starting');
  fetch('/start', {method: 'POST'});
}

function stopMonitor() {
  fetch('/stop', {method: 'POST'});
  setStatus('stopped');
}

function fetchStatus() {
  fetch('/status').then(r => r.json()).then(data => {
    if (data.running) setStatus('running');
    else if (data.starting) setStatus('starting');
    else if (data.error) setStatus('error');
    else setStatus('stopped');
    renderLevels(data.levels);
  });
}

fetchStatus();
connectSSE();
</script>
</body>
</html>
"""

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Settings — Key Levels Monitor</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0f1117; color: #e4e4e7; font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace; font-size: 14px; }
  .container { max-width: 560px; margin: 0 auto; padding: 20px; }
  header { display: flex; align-items: center; gap: 16px; padding: 16px 0; border-bottom: 1px solid #27272a; margin-bottom: 24px; }
  h1 { font-size: 20px; font-weight: 600; }
  a { color: #67e8f9; }
  .group { margin-bottom: 20px; }
  .group h2 { font-size: 13px; color: #a1a1aa; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 10px; }
  label { display: block; color: #a1a1aa; font-size: 12px; margin-bottom: 4px; }
  input[type=text], input[type=password], select, textarea {
    width: 100%; background: #18181b; color: #e4e4e7; border: 1px solid #3f3f46;
    padding: 8px 10px; border-radius: 6px; font-family: inherit; font-size: 13px; margin-bottom: 12px;
  }
  input:focus, select:focus, textarea:focus { outline: none; border-color: #67e8f9; }
  textarea { resize: vertical; min-height: 60px; }
  .checkbox { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; cursor: pointer; }
  .checkbox input { width: 16px; height: 16px; accent-color: #22c55e; }
  .actions { display: flex; gap: 8px; margin-top: 20px; }
  button { background: #22c55e; color: #0f1117; border: none; padding: 10px 24px; border-radius: 6px; cursor: pointer; font-family: inherit; font-size: 14px; font-weight: 600; }
  button:hover { background: #16a34a; }
  .note { color: #71717a; font-size: 12px; margin-top: 12px; }
  .saved { background: #22c55e20; border: 1px solid #22c55e40; color: #22c55e; padding: 8px 12px; border-radius: 6px; margin-bottom: 16px; font-size: 13px; }
  hr { border: none; border-top: 1px solid #27272a; margin: 16px 0; }
</style>
</head>
<body>
<div class="container">
  <header>
    <a href="/">&larr; Back</a>
    <h1>Settings</h1>
  </header>

  {% if request.args.get('saved') %}
  <div class="saved">Settings saved. Restart the monitor for changes to take effect.</div>
  {% endif %}

  <form method="POST" action="/settings">
    <div class="group">
      <h2>API Credentials</h2>
      <label>FMP API Key</label>
      <input type="password" name="fmp_api_key" value="{{ env.get('FMP_API_KEY', '') }}">
    </div>

    <hr>

    <div class="group">
      <h2>Timezone</h2>
      <select name="timezone">
        {% for tz in timezones %}
        <option value="{{ tz }}" {{ 'selected' if env.get('TIMEZONE', 'America/New_York') == tz else '' }}>{{ tz }}</option>
        {% endfor %}
      </select>
    </div>

    <hr>

    <div class="group">
      <h2>Watchlist</h2>
      <label>Tickers (comma-separated)</label>
      <textarea name="watchlist">{{ env.get('WATCHLIST', '') }}</textarea>
    </div>

    <hr>

    <div class="group">
      <h2>Features</h2>
      <label class="checkbox">
        <input type="checkbox" name="VOLUME_CONFIRMATION" {{ 'checked' if env.get('VOLUME_CONFIRMATION', 'true').lower() == 'true' else '' }}>
        Volume Confirmation
      </label>
      <label class="checkbox">
        <input type="checkbox" name="LEVEL_CLUSTERING" {{ 'checked' if env.get('LEVEL_CLUSTERING', 'true').lower() == 'true' else '' }}>
        Level Clustering
      </label>
      <label class="checkbox">
        <input type="checkbox" name="PROXIMITY_ALERTS" {{ 'checked' if env.get('PROXIMITY_ALERTS', 'true').lower() == 'true' else '' }}>
        Proximity Alerts
      </label>
    </div>

    <div class="actions">
      <button type="submit">Save Settings</button>
    </div>
    <p class="note">Changes take effect on next Start.</p>
  </form>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    print(f"Key Levels Monitor running at http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=False)
