import os
import json
import requests
import time
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["8609134609:AAExGA_cBZw7-HKLUfSEgXuO_6ymKrsPvXY"]
TELEGRAM_CHAT_ID = str(os.environ["5760480316"])

THRESHOLD = 0.9975
RESULTS_FILE = "/data/results.json" if os.path.isdir("/data") else "results.json"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "LTCUSDT",
    "DOGEUSDT", "SOLUSDT", "XRPUSDT",
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

HIST = {
    "BTCUSDT": (21, 21),
    "ETHUSDT": (13, 14),
    "BNBUSDT": (16, 16),
    "LTCUSDT": (9, 9),
    "DOGEUSDT": (8, 8),
    "SOLUSDT": (7, 8),
    "XRPUSDT": (8, 9),
}

update_offset = 0

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
            },
            timeout=15,
        )
    except Exception as e:
        print("Telegram error:", e)

def load_results():
    default_coin = {
        "open": None,
        "wed_holding": None,
        "pending": False,
        "wins": 0,
        "total": 0,
        "entry": None,
    }
    default = {
        "week": None,
        "monday_sent": False,
        "wednesday_sent": False,
        "thursday_sent": False,
        "sunday_sent": False,
        "coins": {s: default_coin.copy() for s in SYMBOLS},
    }
    if not os.path.exists(RESULTS_FILE):
        return default
    try:
        with open(RESULTS_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        return default
    for k, v in default.items():
        data.setdefault(k, v)
    data.setdefault("coins", {})
    for s in SYMBOLS:
        data["coins"].setdefault(s, default_coin.copy())
        for kk, vv in default_coin.items():
            data["coins"][s].setdefault(kk, vv)
    return data

def save_results(data):
    folder = os.path.dirname(RESULTS_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(data, f)

results = load_results()

def register_commands():
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands",
            json={
                "commands": [
                    {"command": "status", "description": "HOLDING / BROKEN now"},
                    {"command": "score", "description": "Historical + live scores"},
                    {"command": "help", "description": "How Neo works"},
                ]
            },
            timeout=15,
        )
    except Exception as e:
        print("commands error", e)

def get_daily_klines(symbol, limit=14):
    # Binance
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": limit},
            timeout=15,
        ).json()
        if isinstance(r, list) and r:
            candles = []
            for k in r:
                candles.append({
                    "open_time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                    "open": float(k[1]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                })
            return candles
        print("binance bad reply", symbol, r)
    except Exception as e:
        print("binance error", symbol, e)

    # Bybit backup
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={
                "category": "spot",
                "symbol": symbol,
                "interval": "D",
                "limit": limit,
            },
            timeout=15,
        ).json()
        rows = r.get("result", {}).get("list", []) or []
        candles = []
        for k in rows:
            candles.append({
                "open_time": datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc),
                "open": float(k[1]),
                "low": float(k[3]),
                "close": float(k[4]),
            })
        candles.sort(key=lambda x: x["open_time"])
        return candles
    except Exception as e:
        print("bybit error", symbol, e)
        return []

def snapshot():
    now = datetime.now(timezone.utc)
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    out = {}
    for symbol in SYMBOLS:
        candles = get_daily_klines(symbol)
        monday_candle = None
        week_lows = []
        latest_close = None
        for c in candles:
            if c["open_time"].date() == this_monday.date():
                monday_candle = c
            if c["open_time"] >= this_monday:
                week_lows.append(c["low"])
                latest_close = c["close"]
        if not monday_candle or not week_lows:
            print("no week data", symbol)
            continue
        mon_open = monday_candle["open"]
        low = min(week_lows)
        out[symbol] = {
            "open": mon_open,
            "low": low,
            "close": latest_close,
            "dip": (mon_open - low) / mon_open * 100,
            "holding": low >= mon_open * THRESHOLD,
        }
        time.sleep(0.3)
    return this_monday, out

def score_line(symbol):
    hw, ht = HIST[symbol]
    lw = results["coins"][symbol]["wins"]
    lt = results["coins"][symbol]["total"]
    return f"{NAMES[symbol]}: before {hw}/{ht} | since Neo {lw}/{lt} | total {hw + lw}/{ht + lt}"

def format_board(data, title):
    lines = [f"<b>{title}</b>"]
    for symbol in SYMBOLS:
        if symbol not in data:
            lines.append(f"⚪ <b>{NAMES[symbol]}</b>\nNo data")
            continue
        d = data[symbol]
        flag = "🟢 HOLDING" if d["holding"] else "🔴 BROKEN"
        lines.append(
            f"{flag} <b>{NAMES[symbol]}</b>\n"
            f"Open ${d['open']:,.4f} | Low ${d['low']:,.4f} | Dip {d['dip']:.2f}%"
        )
    return "\n\n".join(lines)

def handle_command(text):
    cmd = text.strip().lower()
    if cmd.startswith("/status"):
        _, data = snapshot()
        send_telegram(format_board(data, "Neo status"))
    elif cmd.startswith("/score"):
        send_telegram("<b>Scores</b>\n" + "\n".join(score_line(s) for s in SYMBOLS))
    elif cmd.startswith("/help") or cmd.startswith("/start"):
        send_telegram(
            "<b>Neo</b>\n"
            "Monday: week opens\n"
            "Wednesday 21:00 BG: early HOLDING / BROKEN\n"
            "Thursday 21:00 BG: CONFIRMED / DENIED\n"
            "Sunday 23:00 BG: result + score\n\n"
            "/status and /score anytime"
        )
    else:
        send_telegram("Commands: /status /score /help")

def poll_telegram():
    global update_offset
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"timeout": 0, "offset": update_offset},
            timeout=15,
        ).json()
        for upd in r.get("result", []):
            update_offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != TELEGRAM_CHAT_ID:
                continue
            text = msg.get("text") or ""
            if text.startswith("/"):
                handle_command(text)
    except Exception as e:
        print("poll error", e)

def run_schedule():
    now = datetime.now(timezone.utc)
    this_monday, data = snapshot()
    week_key = this_monday.strftime("%Y-%m-%d")

    if results.get("week") != week_key:
        results["week"] = week_key
        results["monday_sent"] = False
        results["wednesday_sent"] = False
        results["thursday_sent"] = False
        results["sunday_sent"] = False
        for s in SYMBOLS:
            results["coins"][s]["pending"] = False
            results["coins"][s]["wed_holding"] = None
            results["coins"][s]["entry"] = None
            if s in data:
                results["coins"][s]["open"] = data[s]["open"]
        save_results(results)

    if not results["monday_sent"]:
        send_telegram(format_board(data, "🆕 New week"))
        results["monday_sent"] = True
        save_results(results)

    if now.weekday() == 2 and now.hour >= 18 and not results["wednesday_sent"]:
        lines = ["<b>Wednesday early report</b>"]
        for symbol in SYMBOLS:
            if symbol not in data:
                continue
            d = data[symbol]
            results["coins"][symbol]["wed_holding"] = d["holding"]
            if d["holding"]:
                results["coins"][symbol]["entry"] = d["close"]
                lines.append(
                    f"🟢 <b>{NAMES[symbol]} EARLY HOLDING</b>\n"
                    f"Open ${d['open']:,.4f} | Low ${d['low']:,.4f} | Dip {d['dip']:.2f}%\n"
                    f"Early entry ref: ${d['close']:,.4f}"
                )
            else:
                lines.append(
                    f"🔴 <b>{NAMES[symbol]} ALREADY BROKEN</b>\n"
                    f"Open ${d['open']:,.4f} | Low ${d['low']:,.4f} | Dip {d['dip']:.2f}%"
                )
        send_telegram("\n\n".join(lines))
        results["wednesday_sent"] = True
        save_results(results)

    if now.weekday() == 3 and now.hour >= 18 and not results["thursday_sent"]:
        lines = ["<b>Thursday final report</b>"]
        for symbol in SYMBOLS:
            if symbol not in data:
                continue
            d = data[symbol]
            wed = results["coins"][symbol]["wed_holding"]
            if d["holding"]:
                results["coins"][symbol]["pending"] = True
                if results["coins"][symbol]["entry"] is None:
                    results["coins"][symbol]["entry"] = d["close"]
                lines.append(
                    f"🟢 <b>{NAMES[symbol]} CONFIRMED</b>\n"
                    f"Open ${d['open']:,.4f} | Low ${d['low']:,.4f} | Dip {d['dip']:.2f}%"
                )
            else:
                results["coins"][symbol]["pending"] = False
                extra = ""
                if wed is True:
                    extra = "\n⚠️ Held Wednesday, broke Thursday. Possible weak week / short idea, small sample."
                lines.append(
                    f"🔴 <b>{NAMES[symbol]} DENIED</b>\n"
                    f"Open ${d['open']:,.4f} | Low ${d['low']:,.4f} | Dip {d['dip']:.2f}%"
                    f"{extra}"
                )
        send_telegram("\n\n".join(lines))
        results["thursday_sent"] = True
        save_results(results)

    if now.weekday() == 6 and now.hour >= 20 and not results["sunday_sent"]:
        lines = ["<b>Sunday report</b>"]
        for symbol in SYMBOLS:
            if symbol not in data:
                continue
            d = data[symbol]
            green = d["close"] > d["open"]
            ret = (d["close"] - d["open"]) / d["open"] * 100
            entry = results["coins"][symbol]["entry"]
            trade = ((d["close"] - entry) / entry * 100) if entry else None
            if results["coins"][symbol]["pending"]:
                results["coins"][symbol]["total"] += 1
                if green:
                    results["coins"][symbol]["wins"] += 1
                results["coins"][symbol]["pending"] = False
                note = "live score updated"
            else:
                note = "no Thursday setup"
            flag = "✅ GREEN" if green else "❌ RED"
            trade_txt = f"\nEntry → Sun: {trade:+.2f}%" if trade is not None else ""
            lines.append(
                f"{flag} <b>{NAMES[symbol]}</b> week {ret:+.2f}%"