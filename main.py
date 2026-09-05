import os
import json
import requests
import time
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])

LONG_OK = 0.9975
SHORT_OK = 1.0025
RESULTS_FILE = "/data/results.json" if os.path.isdir("/data") else "results.json"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "LTCUSDT",
    "DOGEUSDT", "SOLUSDT", "XRPUSDT",
]
NAMES = {
    "BTCUSDT": "BTC", "ETHUSDT": "ETH", "BNBUSDT": "BNB",
    "LTCUSDT": "LTC", "DOGEUSDT": "DOGE", "SOLUSDT": "SOL",
    "XRPUSDT": "XRP",
}
HIST_LONG = {
    "BTCUSDT": (21, 21), "ETHUSDT": (13, 14), "BNBUSDT": (16, 16),
    "LTCUSDT": (9, 9), "DOGEUSDT": (8, 8), "SOLUSDT": (7, 8),
    "XRPUSDT": (8, 9),
}
HIST_SHORT = {
    "BTCUSDT": (22, 23), "ETHUSDT": (21, 23), "BNBUSDT": (17, 19),
    "LTCUSDT": (19, 22), "DOGEUSDT": (19, 20), "SOLUSDT": (8, 9),
    "XRPUSDT": (16, 18),
}

update_offset = 0

def send_telegram(message):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
    except Exception as e:
        print("Telegram error:", e)

def empty_coin():
    return {
        "pending_long": False,
        "pending_short": False,
        "entry": None,
        "long_wins": 0, "long_total": 0,
        "short_wins": 0, "short_total": 0,
    }

def load_results():
    default = {
        "week": None,
        "monday_sent": False,
        "wednesday_sent": False,
        "thursday_sent": False,
        "sunday_sent": False,
        "coins": {s: empty_coin() for s in SYMBOLS},
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
        data["coins"].setdefault(s, empty_coin())
        for kk, vv in empty_coin().items():
            data["coins"][s].setdefault(kk, vv)
    return data

def save_results(data):
    folder = os.path.dirname(RESULTS_FILE)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(data, f)

results = load_results()

def get_daily_klines(symbol, limit=14):
    sources = [
        ("https://api.binance.com/api/v3/klines",
         {"symbol": symbol, "interval": "1d", "limit": limit}, "binance"),
        ("https://api.binance.us/api/v3/klines",
         {"symbol": symbol, "interval": "1d", "limit": limit}, "binanceus"),
    ]
    for url, params, name in sources:
        try:
            r = requests.get(url, params=params, timeout=15).json()
            if isinstance(r, list) and r:
                return [{
                    "open_time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                } for k in r]
        except Exception as e:
            print(name, symbol, e)
    try:
        r = requests.get(
            "https://api.bybit.com/v5/market/kline",
            params={"category": "spot", "symbol": symbol, "interval": "D", "limit": limit},
            timeout=15,
        ).json()
        rows = (r.get("result") or {}).get("list") or []
        candles = [{
            "open_time": datetime.fromtimestamp(int(k[0]) / 1000, tz=timezone.utc),
            "open": float(k[1]),
            "high": float(k[2]),
            "low": float(k[3]),
            "close": float(k[4]),
        } for k in rows]
        candles.sort(key=lambda x: x["open_time"])
        if candles:
            return candles
    except Exception as e:
        print("bybit", symbol, e)
    return []

def snapshot():
    now = datetime.now(timezone.utc)
    this_monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    out = {}
    for symbol in SYMBOLS:
        candles = get_daily_klines(symbol)
        monday = None
        lows, highs = [], []
        close = None
        for c in candles:
            if c["open_time"].date() == this_monday.date():
                monday = c
            if c["open_time"] >= this_monday:
                lows.append(c["low"])
                highs.append(c["high"])
                close = c["close"]
        if not monday or not lows:
            continue
        o = monday["open"]
        low, high = min(lows), max(highs)
        out[symbol] = {
            "open": o,
            "low": low,
            "high": high,
            "close": close,
            "dip": (o - low) / o * 100,
            "rally": (high - o) / o * 100,
            "long_ok": low >= o * LONG_OK,
            "short_ok": high <= o * SHORT_OK,
        }
        time.sleep(0.25)
    return this_monday, out

def score_line(symbol):
    lw, lt = HIST_LONG[symbol]
    sw, st = HIST_SHORT[symbol]
    c = results["coins"][symbol]
    return (
        f"{NAMES[symbol]} long {lw}/{lt} | live {c['long_wins']}/{c['long_total']} || "
        f"short {sw}/{st} | live {c['short_wins']}/{c['short_total']}"
    )

def handle_command(text):
    cmd = text.strip().lower()
    if cmd.startswith("/status"):
        _, data = snapshot()
        lines = ["<b>Neo status</b>"]
        for s in SYMBOLS:
            if s not in data:
                lines.append(f"⚪ {NAMES[s]} no data")
                continue
            d = data[s]
            tag = "🟢 LONG" if d["long_ok"] else ("🔴 SHORT" if d["short_ok"] else "⚪ NONE")
            lines.append(
                f"{tag} <b>{NAMES[s]}</b>\n"
                f"Open ${d['open']:,.4f} | Low ${d['low']:,.4f} | High ${d['high']:,.4f}\n"
                f"Dip {d['dip']:.2f}% | Rally {d['rally']:.2f}%"
            )
        send_telegram("\n\n".join(lines))
    elif cmd.startswith("/score"):
        send_telegram("<b>Scores</b>\n" + "\n".join(score_line(s) for s in SYMBOLS))
    elif cmd.startswith("/help") or cmd.startswith("/start"):
        send_telegram(
            "<b>Neo</b>\n"
            "LONG: week low never -0.25% under Monday open\n"
            "SHORT: week high never +0.25% over Monday open\n"
            "Wednesday early / Thursday final / Sunday report\n"
            "/status /score"
        )

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
            results["coins"][s]["pending_long"] = False
            results["coins"][s]["pending_short"] = False
            results["coins"][s]["entry"] = None
        save_results(results)

    if not results["monday_sent"]:
        lines = ["<b>New week</b>"]
        for s in SYMBOLS:
            if s in data:
                lines.append(f"{NAMES[s]} open ${data[s]['open']:,.4f}")
        send_telegram("\n".join(lines))
        results["monday_sent"] = True
        save_results(results)

    if now.weekday() == 2 and now.hour >= 18 and not results["wednesday_sent"]:
        lines = ["<b>Wednesday early report</b>"]
        for s in SYMBOLS:
            if s not in data:
                continue
            d = data[s]
            if d["long_ok"]:
                results["coins"][s]["entry"] = d["close"]
                lines.append(f"🟢 {NAMES[s]} EARLY LONG\nDip {d['dip']:.2f}%")
            elif d["short_ok"]:
                results["coins"][s]["entry"] = d["close"]
                lines.append(f"🔴 {NAMES[s]} EARLY SHORT\nRally {d['rally']:.2f}%")
            else:
                lines.append(f"⚪ {NAMES[s]} no early setup")
        send_telegram("\n\n".join(lines))
        results["wednesday_sent"] = True
        save_results(results)

    if now.weekday() == 3 and now.hour >= 18 and not results["thursday_sent"]:
        lines = ["<b>Thursday final report</b>"]
        for s in SYMBOLS:
            if s not in data:
                continue
            d = data[s]
            c = results["coins"][s]
            if d["long_ok"]:
                c["pending_long"] = True
                c["pending_short"] = False
                if c["entry"] is None:
                    c["entry"] = d["close"]
                lines.append(f"🟢 {NAMES[s]} LONG CONFIRMED\nDip {d['dip']:.2f}%")
            elif d["short_ok"]:
                c["pending_short"] = True
                c["pending_long"] = False
                if c["entry"] is None:
                    c["entry"] = d["close"]
                lines.append(f"🔴 {NAMES[s]} SHORT CONFIRMED\nRally {d['rally']:.2f}%")
            else:
                c["pending_long"] = False
                c["pending_short"] = False
                lines.append(f"⚪ {NAMES[s]} NO SETUP")
        send_telegram("\n\n".join(lines))
        results["thursday_sent"] = True
        save_results(results)

    if now.weekday() == 6 and now.hour >= 20 and not results["sunday_sent"]:
        lines = ["<b>Sunday report</b>"]
        for s in SYMBOLS:
            if s not in data:
                continue
            d = data[s]
            c = results["coins"][s]
            week_ret = (d["close"] - d["open"]) / d["open"] * 100
            if c["pending_long"]:
                c["long_total"] += 1
                if d["close"] > d["open"]:
                    c["long_wins"] += 1
                note = "long live score updated"
            elif c["pending_short"]:
                c["short_total"] += 1
                if d["close"] < d["open"]:
                    c["short_wins"] += 1
                note = "short live score updated"
            else:
                note = "no Thursday setup"
            c["pending_long"] = False
            c["pending_short"] = False
            lines.append(
                f"{NAMES[s]} week {week_ret:+.2f}%\n{note}\n{score_line(s)}"
            )
        send_telegram("\n\n".join(lines))
        results["sunday_sent"] = True
        save_results(results)

print("Neo long+short starting")
send_telegram("🤖 <b>Neo updated</b>\nLong + short filters. /status /score")

last_check = 0
while True:
    poll_telegram()
    if time.time() - last_check >= 20 * 60:
        try:
            run_schedule()
        except Exception as e:
            print("schedule error", e)
        last_check = time.time()
    time.sleep(3)
