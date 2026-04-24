"""
Key Levels Monitor — Entry Point

Computes PDH/PDL, PMH/PML from Alpaca historical data, then streams
real-time 1-min bars to detect breaks and retests during the morning session.
"""

import os
import platform
import re
import subprocess
import sys
import time
import signal
from datetime import datetime

import pytz
from alpaca_trade_api.rest import REST

import config
from levels import get_levels_for_ticker
from monitor import KeyLevelMonitor

TZ = pytz.timezone(config.TIMEZONE)

# Session log file — written to logs/ directory
LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = None

# ANSI formatting
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"


def open_log():
    """Create the log file for this session. Returns the file handle."""
    global LOG_FILE
    os.makedirs(LOG_DIR, exist_ok=True)
    date_str = datetime.now(TZ).strftime("%Y-%m-%d_%H%M")
    path = os.path.join(LOG_DIR, f"session_{date_str}.txt")
    LOG_FILE = open(path, "w")
    return path


def log(text):
    """Write plain text to the session log file."""
    if LOG_FILE:
        LOG_FILE.write(strip_ansi(text) + "\n")
        LOG_FILE.flush()


def print_and_log(text):
    """Print to terminal and write to log."""
    print(text)
    log(text)


def _format_hhmm(hhmm):
    """Format HHMM integer as HH:MM string."""
    return f"{hhmm // 100:02d}:{hhmm % 100:02d}"


def print_banner():
    tz_name = datetime.now(TZ).strftime("%Z")
    mon_start = _format_hhmm(config.MONITOR_START)
    mon_end = _format_hhmm(config.MONITOR_END)
    print(f"\n{BOLD}{'=' * 60}{RESET}")
    print(f"{BOLD}  Key Levels Monitor{RESET}")
    print(f"{DIM}  Timezone: {config.TIMEZONE} ({tz_name}){RESET}")
    print(f"{DIM}  Monitor window: {mon_start} – {mon_end} {tz_name}{RESET}")
    print(f"{DIM}  Max alerts per level: {config.MAX_ALERTS_PER_LEVEL}{RESET}")
    print(f"{BOLD}{'=' * 60}{RESET}\n")


def print_levels_table(all_levels):
    """Print a formatted table of computed levels for all tickers."""
    header = f"{'Ticker':<6} {'PDH':>10} {'PDL':>10} {'PMH':>10} {'PML':>10} {'ORH':>10} {'ORL':>10}"
    print_and_log(f"{BOLD}{header}{RESET}")
    print_and_log("-" * len(header))

    for ticker in sorted(all_levels.keys()):
        levels = all_levels[ticker]
        row = f"{CYAN}{ticker:<6}{RESET}"
        for name in ["PDH", "PDL", "PMH", "PML", "ORH", "ORL"]:
            val = levels.get(name)
            if val is not None:
                row += f" {val:>10.2f}"
            else:
                row += f" {'--':>10}"
        print_and_log(row)
    print_and_log("")


def print_session_summary(monitor):
    """Print end-of-session summary."""
    print_and_log(f"\n{BOLD}{'=' * 60}{RESET}")
    print_and_log(f"{BOLD}  Session Summary{RESET}")
    print_and_log(f"{BOLD}{'=' * 60}{RESET}\n")

    header = f"{'Ticker':<6} {'Level':<6} {'Alerts':>7} {'Broken?':>8} {'Direction':>10}"
    print_and_log(f"{BOLD}{header}{RESET}")
    print_and_log("-" * len(header))

    for (ticker, level_name), state in sorted(monitor.alert_states.items()):
        if state.cross_count > 0 or state.has_broken:
            broken = "Yes" if state.has_broken else "No"
            direction = state.break_direction or "--"
            print_and_log(f"{ticker:<6} {level_name:<6} {state.cross_count:>7} {broken:>8} {direction:>10}")

    print_and_log("")


ANSI_RE = re.compile(r'\033\[[0-9;]*m')


def strip_ansi(text):
    return ANSI_RE.sub('', text)


def send_notification(title, body):
    """Send a desktop notification. Works on Linux, macOS, and Windows."""
    system = platform.system()
    try:
        if system == "Linux":
            subprocess.Popen(
                ["notify-send", "-u", "critical", "-t", "10000", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif system == "Darwin":  # macOS
            script = f'display notification "{body}" with title "{title}" sound name "Glass"'
            subprocess.Popen(
                ["osascript", "-e", script],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        elif system == "Windows":
            # PowerShell toast notification
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
        pass  # Notification tool not installed — skip silently


def alert_with_notification(alert_string):
    """Print alert to terminal, log to file, and send a desktop notification."""
    print_and_log(alert_string)
    plain = strip_ansi(alert_string)
    # Split into title (ticker + level) and body (event details)
    parts = plain.split(" | ", 1)
    title = parts[0].strip() if parts else "Key Level Alert"
    body = parts[1] if len(parts) > 1 else plain
    send_notification(title, body)


def main():
    # Validate credentials
    if not config.ALPACA_API_KEY or not config.ALPACA_API_SECRET:
        print("Error: Set ALPACA_API_KEY and ALPACA_API_SECRET environment variables.")
        sys.exit(1)

    log_path = open_log()

    print_banner()
    log(f"Session started: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M:%S %Z')}")
    log(f"Monitor window: {config.MONITOR_START} – {config.MONITOR_END}")
    log(f"Watchlist: {', '.join(config.WATCHLIST)}")
    log("")

    api = REST(config.ALPACA_API_KEY, config.ALPACA_API_SECRET, config.BASE_URL)

    # Validate connection
    try:
        account = api.get_account()
        print(f"{GREEN}Connected to Alpaca ({account.status}){RESET}\n")
    except Exception as e:
        print(f"Failed to connect to Alpaca: {e}")
        sys.exit(1)

    # Compute levels for all tickers
    print(f"Computing levels for {len(config.WATCHLIST)} tickers...")
    all_levels = {}
    for ticker in config.WATCHLIST:
        try:
            levels = get_levels_for_ticker(api, ticker)
            all_levels[ticker] = levels
            sys.stdout.write(".")
            sys.stdout.flush()
        except Exception as e:
            print(f"\n  Warning: Failed to compute levels for {ticker}: {e}")
            all_levels[ticker] = {
                "PDH": None, "PDL": None, "PMH": None, "PML": None,
                "ORH": None, "ORL": None,
            }
    print("\n")

    print_levels_table(all_levels)

    # Start monitor
    monitor = KeyLevelMonitor(all_levels, on_alert=alert_with_notification)

    # Graceful shutdown on Ctrl+C
    def handle_signal(sig, frame):
        print(f"\n{YELLOW}Shutting down...{RESET}")
        monitor.stop()
        print_session_summary(monitor)
        if LOG_FILE:
            LOG_FILE.close()
        print(f"{DIM}Session log saved to: {log_path}{RESET}")
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Check if we should auto-stop at MONITOR_END
    ws_thread = monitor.start_background()

    try:
        while ws_thread.is_alive():
            now = datetime.now(TZ)
            current_hhmm = now.hour * 100 + now.minute
            if current_hhmm >= config.MONITOR_END:
                print(f"\n{YELLOW}Monitor window closed ({_format_hhmm(config.MONITOR_END)}).{RESET}")
                monitor.stop()
                break
            time.sleep(1)
    except KeyboardInterrupt:
        monitor.stop()

    print_session_summary(monitor)

    if LOG_FILE:
        LOG_FILE.close()
    print(f"{DIM}Session log saved to: {log_path}{RESET}")


if __name__ == "__main__":
    main()
