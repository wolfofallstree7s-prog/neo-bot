import os
import requests
import time
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

THRESHOLD = 0.9975  # 0.25%

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "LTCUSDT",
    "DOGEUSDT",
    "SOLUSDT",
    "XRPUSDT",
]

NAMES = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "BNBUSDT": "BNB",
    "LTCUSDT": "LTC",
    "DOGEUSDT": "DOGE",
    "SOLUSDT": "SOL",
    "XRPUSDT": "XRP",
}

STATS = {
    "BTCUSDT": "21/21",
    "ETHUSDT": "13/14",
    "BNBUSDT": "16/16",
    "LTCUSDT": "9/9",
    "DOGEUSDT": "8/8",
    "SOLUSDT": "7/8",
    "XRPUSDT": "8/9",
}

state = {}

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        print("Telegram error:", e)

def get_daily_klines(symbol, limit=14):
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": "1d", "limit": limit}
    data = requests.get(url, params=params, timeout=15).json()
    candles = []
    for k in data:
        candles.append({
            "open_time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
            "open": float(k[1]),
            "low": float(k[3]),
        })
    return candles

def check_symbol(symbol):
    name = NAMES[symbol]
    candles = get_daily_klines(symbol)
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
        return

    if symbol not in state or state[symbol]["week"] != this_monday:
        state[symbol] = {
            "week": this_monday,
            "open": monday_candle["open"],
            "alerted": False,
            "reported": False,
        }
        send_telegram(
            f"🆕 <b>{name} new week</b>\n"
            f"Monday open: ${monday_candle['open']:,.4f}"
        )

    mon_open = state[symbol]["open"]
    current_low = min(week_lows)
    threshold = mon_open * THRESHOLD
    holding = current_low >= threshold
    status = "✅ HOLDING" if holding else "❌ BROKEN"

    print(f"{name} {now:%Y-%m-%d %H:%M} | open {mon_open} | low {current_low} | {status}")

    if holding and now.weekday() == 3 and now.hour >= 18 and not state[symbol]["alerted"]:
        send_telegram(
            f"<b>🟢 Neo Alert – {name} pattern triggered</b>\n\n"
            f"Monday open: ${mon_open:,.4f}\n"
            f"Lowest so far: ${current_low:,.4f}\n"
            f"Threshold: ${threshold:,.4f}\n\n"
            f"No dip greater than 0.25% below Monday open.\n"
            f"Sample: {STATS[symbol]}"
        )
        state[symbol]["alerted"] = True

    if now.weekday() == 6 and now.hour >= 20 and not state[symbol]["reported"]:
        send_telegram(
            f"<b>📊 {name} weekly report</b>\n"
            f"Monday open: ${mon_open:,.4f}\n"
            f"Week low: ${current_low:,.4f}\n"
            f"Status: {status}"
        )
        state[symbol]["reported"] = True

print("Neo starting... multi-asset")
send_telegram(
    "🤖 <b>Neo is online 24/7</b>\n"
    "Watching: BTC ETH BNB LTC DOGE SOL XRP"
)

while True:
    try:
        for symbol in SYMBOLS:
            check_symbol(symbol)
            time.sleep(1)
    except Exception as e:
        print("Error:", e)
    time.sleep(20 * 60)
