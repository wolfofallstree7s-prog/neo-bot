import os
import requests
import time
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SYMBOL = "BTCUSDT"
THRESHOLD = 0.9975

alerted_this_week = False
weekly_report_sent = False
current_week_monday = None
monday_open = None

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print("Telegram error:", e)

def get_daily_klines(limit=14):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": SYMBOL, "interval": "1d", "limit": limit}
    data = requests.get(url, params=params, timeout=15).json()
    candles = []
    for k in data:
        candles.append({
            "open_time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            "open": float(k[1]),
            "low": float(k[3]),
        })
    return candles

def check_pattern():
    global alerted_this_week, weekly_report_sent, current_week_monday, monday_open

    candles = get_daily_klines()
    now = datetime.now(timezone.utc)
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    monday_candle = None
    week_lows = []

    for c in candles:
        if c["open_time"].date() == this_monday.date():
            monday_candle = c
        if c["open_time"] >= this_monday:
            week_lows.append(c["low"])

    if not monday_candle or not week_lows:
        print("Waiting for Monday data...")
        return

    if current_week_monday != this_monday:
        current_week_monday = this_monday
        monday_open = monday_candle["open"]
        alerted_this_week = False
        weekly_report_sent = False
        send_telegram(
            f"🆕 <b>New week started</b>\n"
            f"Monday Open: ${monday_open:,.2f}\n"
            f"Neo is watching."
        )

    current_low = min(week_lows)
    threshold = monday_open * THRESHOLD
    holding = current_low >= threshold
    status = "✅ HOLDING" if holding else "❌ BROKEN"

    print(
        f"{now.strftime('%Y-%m-%d %H:%M')} UTC | "
        f"Open {monday_open:.2f} | Low {current_low:.2f} | {status}"
    )

    # Thursday alert if the pattern is still valid
    if holding and now.weekday() == 3 and now.hour >= 18 and not alerted_this_week:
        send_telegram(
            f"<b>🟢 Neo Alert – Pattern Triggered</b>\n\n"
            f"Week of: {this_monday.strftime('%d %b %Y')}\n"
            f"Monday Open: ${monday_open:,.2f}\n"
            f"Lowest so far: ${current_low:,.2f}\n"
            f"Threshold: ${threshold:,.2f}\n\n"
            f"Price has not dipped more than 0.25% below Monday open.\n"
            f"Historically this closed green."
        )
        alerted_this_week = True

    # Sunday weekly report
    if now.weekday() == 6 and now.hour >= 20 and not weekly_report_sent:
        send_telegram(
            f"<b>📊 Weekly Report</b>\n\n"
            f"Monday Open: ${monday_open:,.2f}\n"
            f"Week Low: ${current_low:,.2f}\n"
            f"Status: {status}"
        )
        weekly_report_sent = True

print("Neo Bot starting...")
send_telegram("🤖 <b>Neo is online 24/7</b>\nRunning on Railway")

while True:
    try:
        check_pattern()
    except Exception as e:
        print("Error:", e)
    time.sleep(20 * 60)
