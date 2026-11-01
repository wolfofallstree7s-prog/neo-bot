"""
Neo — crypto weekly structure alert bot
All upgrades: Binance klines, retries, heartbeat, journal, EXTENDED/invalidation,
STRUCTURE vs TRADE, de-spammed bands, fail alerts, webhook clear, live-from-stored-opens
"""
import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

BOT_NAME = "Neo"
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DATA_DIR = "/data"
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat.json")

# Primary: Binance spot. Fallback: Yahoo (Railway often blocks Binance IPs)
SYMBOLS = {
    "BTCUSDT": "BTC",
    "ETHUSDT": "ETH",
    "BNBUSDT": "BNB",
    "LTCUSDT": "LTC",
    "DOGEUSDT": "DOGE",
    "SOLUSDT": "SOL",
    "XRPUSDT": "XRP",
}
YAHOO_MAP = {
    "BTCUSDT": "BTC-USD",
    "ETHUSDT": "ETH-USD",
    "BNBUSDT": "BNB-USD",
    "LTCUSDT": "LTC-USD",
    "DOGEUSDT": "DOGE-USD",
    "SOLUSDT": "SOL-USD",
    "XRPUSDT": "XRP-USD",
}
BINANCE_BASES = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
    "https://data-api.binance.vision",
]

OFFICIAL = 0.25
BANDS = [0.25, 0.50, 0.75, 1.00]
EXTENDED_PCT = 5.0          # structure OK but already ran this much → EXTENDED
SKIP_EXTENDED_PCT = 8.0     # no new risk language above this
HEARTBEAT_HOURS = 6
STALE_HOURS = 2
POLL_SLEEP = 60

# Historical seeds (your research sample) — live book is separate
HIST_SEED = {
    "BTC":  {"long_w": 21, "long_n": 21, "short_w": 22, "short_n": 23},
    "ETH":  {"long_w": 13, "long_n": 14, "short_w": 21, "short_n": 23},
    "BNB":  {"long_w": 16, "long_n": 16, "short_w": 17, "short_n": 19},
    "LTC":  {"long_w": 9,  "long_n": 9,  "short_w": 19, "short_n": 22},
    "DOGE": {"long_w": 8,  "long_n": 8,  "short_w": 19, "short_n": 20},
    "SOL":  {"long_w": 7,  "long_n": 8,  "short_w": 8,  "short_n": 9},
    "XRP":  {"long_w": 8,  "long_n": 9,  "short_w": 16, "short_n": 18},
}

# Live book before upgrade reset — restored once on next deploy
LIVE_SEED = {
    "BTC":  {"long_w": 0, "long_n": 0, "short_w": 1, "short_n": 1},
    "ETH":  {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0},
    "BNB":  {"long_w": 1, "long_n": 1, "short_w": 0, "short_n": 0},
    "LTC":  {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0},
    "DOGE": {"long_w": 1, "long_n": 1, "short_w": 0, "short_n": 0},
    "SOL":  {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0},
    "XRP":  {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0},
}


# ─── helpers ───────────────────────────────────────────────────────────────

def ensure_data_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def load_json(path, default=None):
    if default is None:
        default = {}
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception as e:
        print("load_json", path, e)
    return default


def save_json(path, obj):
    ensure_data_dir()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
    os.replace(tmp, path)


def load_results():
    r = load_json(RESULTS_FILE, {})
    if "hist" not in r:
        r["hist"] = {k: dict(v) for k, v in HIST_SEED.items()}
    # ensure every coin exists in hist
    for name, seed in HIST_SEED.items():
        r.setdefault("hist", {}).setdefault(name, dict(seed))

    if "live" not in r:
        r["live"] = {name: dict(v) for name, v in LIVE_SEED.items()}

    # One-time restore after upgrade wiped live to 0/0
    meta = r.setdefault("meta", {})
    if not meta.get("live_restored_v1"):
        r["live"] = {name: dict(v) for name, v in LIVE_SEED.items()}
        meta["live_restored_v1"] = True

    # ensure every coin exists in live
    for name in SYMBOLS.values():
        r.setdefault("live", {}).setdefault(
            name, {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0}
        )

    if "weeks" not in r:
        r["weeks"] = {}
    if "journal" not in r:
        r["journal"] = []
    return r


def save_results(r):
    save_json(RESULTS_FILE, r)


def touch_heartbeat(ok=True, note=""):
    save_json(HEARTBEAT_FILE, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "note": note,
    })


def check_stale_heartbeat(results):
    """If last successful loop is too old, alert once per incident."""
    hb = load_json(HEARTBEAT_FILE, {})
    ts = hb.get("ts")
    if not ts:
        return
    try:
        last = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except Exception:
        return
    age_h = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    flag = results.setdefault("meta", {})
    if age_h >= STALE_HOURS and not flag.get("stale_alerted"):
        send_telegram(
            f"⚠️ <b>{BOT_NAME}</b> heartbeat stale ({age_h:.1f}h). "
            f"Check Railway logs / process."
        )
        flag["stale_alerted"] = True
        save_results(results)
    elif age_h < STALE_HOURS and flag.get("stale_alerted"):
        flag["stale_alerted"] = False
        save_results(results)


def week_key(dt: datetime) -> str:
    d = dt.date()
    monday = d - timedelta(days=d.weekday())
    return monday.isoformat()


def retry(fn, tries=3, delay=2, label=""):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"retry {label} {i+1}/{tries}: {e}")
            time.sleep(delay * (i + 1))
    raise last if last else RuntimeError("retry failed")


def send_telegram(text: str):
    def _send():
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text[:4000],
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=25,
        )
        r.raise_for_status()
        return r

    try:
        retry(_send, tries=3, delay=2, label="telegram")
    except Exception as e:
        print("telegram hard fail", e)


def delete_webhook():
    try:
        requests.get(
            f"https://api.telegram.org/bot{TOKEN}/deleteWebhook",
            params={"drop_pending_updates": False},
            timeout=15,
        )
    except Exception as e:
        print("deleteWebhook", e)


# ─── Price data (Binance first, Yahoo fallback) ─────────────────────────────

def kline_row(k):
    return {
        "open_time": int(k[0]),
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
    }


def fetch_binance_klines(symbol: str, interval="1d", limit=21):
    last_err = None
    for base in BINANCE_BASES:
        try:
            url = f"{base}/api/v3/klines"
            r = requests.get(
                url,
                params={"symbol": symbol, "interval": interval, "limit": limit},
                timeout=20,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code != 200:
                last_err = f"{base} status {r.status_code}"
                continue
            data = r.json()
            if not isinstance(data, list) or not data:
                last_err = f"{base} empty"
                continue
            return data, base
        except Exception as e:
            last_err = str(e)
            continue
    raise RuntimeError(f"binance fail {symbol}: {last_err}")


def fetch_yahoo_daily(yahoo_sym: str, limit=21):
    from urllib.parse import quote

    def _fetch():
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(yahoo_sym)}"
        r = requests.get(
            url,
            params={"interval": "1d", "range": "1mo"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=25,
        )
        r.raise_for_status()
        res = r.json()["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        ts = res["timestamp"]
        rows = []
        for i in range(len(ts)):
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c):
                continue
            rows.append({
                "open_time": int(ts[i]) * 1000,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
            })
        if not rows:
            raise ValueError(f"yahoo empty {yahoo_sym}")
        return rows[-limit:]

    return retry(_fetch, tries=3, delay=2, label=f"yahoo {yahoo_sym}")


def rows_to_week(rows, monday_date):
    by_day = {}
    for row in rows:
        d = datetime.fromtimestamp(row["open_time"] / 1000, tz=timezone.utc).date()
        by_day[d] = row

    if monday_date not in by_day:
        candidates = [
            d for d in by_day
            if monday_date <= d <= monday_date + timedelta(days=6)
        ]
        if not candidates:
            return None
        mon_d = min(candidates)
    else:
        mon_d = monday_date

    mon_open = by_day[mon_d]["open"]
    highs, lows, last_close, last_ts = [], [], None, None
    for i in range(7):
        d = monday_date + timedelta(days=i)
        if d in by_day:
            highs.append(by_day[d]["high"])
            lows.append(by_day[d]["low"])
            last_close = by_day[d]["close"]
            last_ts = by_day[d]["open_time"]
    if not highs:
        return None
    return {
        "open": mon_open,
        "high": max(highs),
        "low": min(lows),
        "close": last_close,
        "bar_ts": last_ts,
        "monday_date": mon_d.isoformat(),
    }


def snapshot_week():
    """
    Weekly structure from daily candles.
    Tries Binance first (several hosts), then Yahoo USD pairs.
    """
    now = datetime.now(timezone.utc)
    wk = week_key(now)
    monday_date = datetime.fromisoformat(wk).date()
    out = {}
    errors = []
    venue_used = {}

    for sym, name in SYMBOLS.items():
        got = None
        venue = None
        # 1) Binance
        try:
            raw, base = fetch_binance_klines(sym, "1d", 21)
            rows = [kline_row(k) for k in raw]
            got = rows_to_week(rows, monday_date)
            if got:
                venue = f"binance:{base.replace('https://', '')}"
        except Exception as e:
            print("binance", sym, e)

        # 2) Yahoo fallback
        if not got:
            try:
                ysym = YAHOO_MAP.get(sym)
                if ysym:
                    rows = fetch_yahoo_daily(ysym, 21)
                    got = rows_to_week(rows, monday_date)
                    if got:
                        venue = "yahoo"
            except Exception as e:
                print("yahoo", sym, e)

        if not got:
            errors.append(name)
            continue

        got["name"] = name
        got["venue"] = venue
        out[sym] = got
        venue_used[name] = venue

    if venue_used:
        print("venues", venue_used)
    return out, errors


# ─── structure math ─────────────────────────────────────────────────────────

def band_side(o, lo, hi, thr):
    long_ok = lo >= o * (1 - thr / 100)
    short_ok = hi <= o * (1 + thr / 100)
    if long_ok and not short_ok:
        return "LONG"
    if short_ok and not long_ok:
        return "SHORT"
    if long_ok and short_ok:
        return "BOTH"
    return "NONE"


def extension_note(o, lo, hi, side):
    dip = (o - lo) / o * 100
    rally = (hi - o) / o * 100
    if side == "LONG":
        ext = rally
    elif side == "SHORT":
        ext = dip
    else:
        ext = max(dip, rally)
    if ext >= SKIP_EXTENDED_PCT:
        return ext, "SKIP_EXTENDED"
    if ext >= EXTENDED_PCT:
        return ext, "EXTENDED"
    return ext, "OK"


def inv_prices(o):
    return o * (1 - OFFICIAL / 100), o * (1 + OFFICIAL / 100)


def fmt_p(x):
    if x >= 100:
        return f"${x:,.2f}"
    if x >= 1:
        return f"${x:,.4f}"
    return f"${x:,.6f}"


def asof_line():
    now = datetime.now(timezone.utc)
    return f"<i>as-of {now.strftime('%Y-%m-%d %H:%M')} UTC (day may be open)</i>"


def risk_footer():
    return (
        "\n\n<i>Manual risk: ≤1% account to invalidation · "
        "1–2 theme positions · flat by Sunday · "
        "if already extended → half size or skip</i>"
    )


# ─── messages ──────────────────────────────────────────────────────────────

def monday_message(data):
    lines = [f"<b>{BOT_NAME} new week</b>", asof_line()]
    for sym, name in SYMBOLS.items():
        if sym not in data:
            lines.append(f"{name}: no data")
            continue
        o = data[sym]["open"]
        lo_need, hi_need = inv_prices(o)
        lines.append(
            f"<b>{name}</b> open {fmt_p(o)}\n"
            f"Long needs low ≥ {fmt_p(lo_need)} (−{OFFICIAL}%)\n"
            f"Short needs high ≤ {fmt_p(hi_need)} (+{OFFICIAL}%)\n"
            f"Invalidation L {fmt_p(lo_need)} · S {fmt_p(hi_need)}"
        )
    lines.append(risk_footer())
    return "\n\n".join(lines)


def structure_block(data, title, opens=None):
    """Official 0.25% STRUCTURE + TRADE tag."""
    lines = [
        f"<b>{BOT_NAME} {title}</b>  official {OFFICIAL}%",
        asof_line(),
    ]
    for sym, name in SYMBOLS.items():
        if sym not in data:
            lines.append(f"⚪ {name} no data")
            continue
        o = opens.get(sym, data[sym]["open"]) if opens else data[sym]["open"]
        lo = data[sym]["low"]
        hi = data[sym]["high"]
        dip = (o - lo) / o * 100
        rally = (hi - o) / o * 100
        side = band_side(o, lo, hi, OFFICIAL)
        ext, tag = extension_note(o, lo, hi, side)
        inv_l, inv_s = inv_prices(o)
        icon = {"LONG": "🟢", "SHORT": "🔴", "BOTH": "🟡", "NONE": "⚪"}.get(side, "⚪")

        if side == "NONE":
            lines.append(
                f"{icon} <b>{name}</b> NO SETUP\n"
                f"dip {dip:.2f}% | rally {rally:.2f}%\n"
                f"STRUCTURE: none · TRADE: none"
            )
        else:
            trade = "consider"
            if tag == "EXTENDED":
                trade = "EXTENDED — half size / pullback only"
            elif tag == "SKIP_EXTENDED":
                trade = "SKIP_EXTENDED — structure only, no new risk"
            lines.append(
                f"{icon} <b>{name}</b> {side}\n"
                f"dip {dip:.2f}% | rally {rally:.2f}% | ext {ext:.2f}%\n"
                f"STRUCTURE: {side} OK · TRADE: {trade}\n"
                f"Inv L {fmt_p(inv_l)} · S {fmt_p(inv_s)}"
            )
    lines.append(risk_footer())
    return "\n\n".join(lines)


def bands_block(data, opens=None):
    """Multi-band on demand only (/bands)."""
    lines = [f"<b>{BOT_NAME} all bands</b>", asof_line()]
    for thr in BANDS:
        chunk = [f"<b>{thr:.2f}%</b>"]
        for sym, name in SYMBOLS.items():
            if sym not in data:
                continue
            o = opens.get(sym, data[sym]["open"]) if opens else data[sym]["open"]
            side = band_side(o, data[sym]["low"], data[sym]["high"], thr)
            if side in ("LONG", "SHORT", "BOTH"):
                icon = {"LONG": "🟢", "SHORT": "🔴", "BOTH": "🟡"}[side]
                chunk.append(f"{icon} {name} {side}")
        if len(chunk) == 1:
            chunk.append("none")
        lines.append("\n".join(chunk))
    return "\n\n".join(lines)


def sunday_message(data, results, opens):
    lines = [f"<b>{BOT_NAME} Sunday report</b>  {OFFICIAL}% live score", asof_line()]
    wk = results.get("current_week", "")
    week = results.setdefault("weeks", {}).setdefault(wk, {})

    for sym, name in SYMBOLS.items():
        if sym not in data:
            lines.append(f"{name}: no data")
            continue
        o = opens.get(sym, data[sym]["open"])
        c = data[sym]["close"]
        ret = (c - o) / o * 100
        conf = week.get(sym, {}).get("thu_confirm")
        live = results["live"].setdefault(
            name, {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0}
        )
        hist = results["hist"].setdefault(
            name, {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0}
        )

        # score only once
        scored = week.get(sym, {}).get("scored", False)
        note = "no Thursday 0.25% setup"
        if not scored and conf in ("LONG", "SHORT"):
            if conf == "LONG":
                live["long_n"] += 1
                if c > o:
                    live["long_w"] += 1
                    note = "long live updated"
                else:
                    note = "long live updated (loss)"
            else:
                live["short_n"] += 1
                if c < o:
                    live["short_w"] += 1
                    note = "short live updated"
                else:
                    note = "short live updated (loss)"
            week.setdefault(sym, {})["scored"] = True
            results.setdefault("journal", []).append({
                "week": wk,
                "pair": name,
                "side": conf,
                "open": o,
                "close": c,
                "ret_pct": ret,
                "win": (c > o) if conf == "LONG" else (c < o),
                "venue": "binance",
                "ts": datetime.now(timezone.utc).isoformat(),
            })
        elif scored:
            note = "already scored"
        elif conf:
            note = f"thu was {conf}"

        lines.append(
            f"{name} week {ret:+.2f}%\n{note}\n"
            f"{name} long {hist.get('long_w', 0)}/{hist.get('long_n', 0)} "
            f"live {live['long_w']}/{live['long_n']} || "
            f"short {hist.get('short_w', 0)}/{hist.get('short_n', 0)} "
            f"live {live['short_w']}/{live['short_n']}"
        )
    return "\n\n".join(lines)


def scores_message(results):
    lines = [f"<b>{BOT_NAME} scores</b> {OFFICIAL}% only"]
    for name in SYMBOLS.values():
        h = results.get("hist", {}).get(name, {})
        lv = results.get("live", {}).get(name, {})
        lines.append(
            f"{name} long {h.get('long_w', 0)}/{h.get('long_n', 0)} | "
            f"live {lv.get('long_w', 0)}/{lv.get('long_n', 0)} || "
            f"short {h.get('short_w', 0)}/{h.get('short_n', 0)} | "
            f"live {lv.get('short_w', 0)}/{lv.get('short_n', 0)}"
        )
    return "\n".join(lines)


# ─── telegram poll ─────────────────────────────────────────────────────────

def poll_commands(results):
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    try:
        r = requests.get(url, params={"timeout": 0}, timeout=20)
        r.raise_for_status()
        data = r.json()
        for upd in data.get("result", []):
            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip().lower()
            chat = str((msg.get("chat") or {}).get("id", ""))
            if chat and chat != str(CHAT_ID):
                # ignore other chats
                rid = upd["update_id"]
                requests.get(url, params={"offset": rid + 1}, timeout=10)
                continue

            snap, errs = snapshot_week()
            wk = results.get("weeks", {}).get(results.get("current_week", {}), {})
            opens = wk.get("opens") or {s: snap[s]["open"] for s in snap}

            if text in ("/status", "/start", "/help"):
                if errs:
                    send_telegram(f"⚠️ data fail: {', '.join(errs)}")
                send_telegram(structure_block(snap, "status", opens))
                send_telegram(
                    "/status structure · /scores live+hist · /bands all % · /journal last 5"
                )
            elif text in ("/scores", "/score"):
                send_telegram(scores_message(results))
            elif text == "/bands":
                send_telegram(bands_block(snap, opens))
            elif text == "/journal":
                j = results.get("journal", [])[-5:]
                if not j:
                    send_telegram("journal empty")
                else:
                    lines = [f"<b>{BOT_NAME} journal (last 5)</b>"]
                    for row in j:
                        lines.append(
                            f"{row.get('week')} {row.get('pair')} {row.get('side')} "
                            f"{row.get('ret_pct', 0):+.2f}% "
                            f"{'WIN' if row.get('win') else 'LOSS'}"
                        )
                    send_telegram("\n".join(lines))

            rid = upd["update_id"]
            requests.get(url, params={"offset": rid + 1}, timeout=10)
    except Exception as e:
        print("poll", e)


# ─── schedule ──────────────────────────────────────────────────────────────

def run_once(results):
    now = datetime.now(timezone.utc)
    wk = week_key(now)
    results["current_week"] = wk
    week = results.setdefault("weeks", {}).setdefault(wk, {})
    week.setdefault("monday_sent", False)
    week.setdefault("wed_sent", False)
    week.setdefault("thu_sent", False)
    week.setdefault("sun_sent", False)

    data, errors = snapshot_week()
    if errors:
        # only spam once per hour max
        meta = results.setdefault("meta", {})
        last = meta.get("last_err_alert", 0)
        if time.time() - last > 3600:
            send_telegram(
                f"⚠️ <b>{BOT_NAME}</b> data fail this check: {', '.join(errors)}"
            )
            meta["last_err_alert"] = time.time()
            save_results(results)

    # Monday new week ≥ 00:30 UTC (crypto 24/7 — Monday candle exists)
    if now.weekday() == 0 and now.hour >= 0 and now.minute >= 30 and not week["monday_sent"]:
        if data:
            send_telegram(monday_message(data))
            week["monday_sent"] = True
            week["opens"] = {sym: data[sym]["open"] for sym in data}
            week["opens_meta"] = {
                sym: {"monday_date": data[sym]["monday_date"], "venue": "binance"}
                for sym in data
            }
            save_results(results)

    opens = week.get("opens", {})
    # rebuild opens keys if stored under name by old version — expect binance sym keys
    if opens and not any(k.endswith("USDT") for k in opens):
        # map name→sym
        inv = {v: k for k, v in SYMBOLS.items()}
        opens = {inv[n]: p for n, p in opens.items() if n in inv}
        week["opens"] = opens

    # Wednesday early 18:00–19:00 UTC
    if now.weekday() == 2 and 18 <= now.hour < 19 and not week["wed_sent"]:
        send_telegram(structure_block(data, "Wednesday early", opens))
        week["wed_sent"] = True
        save_results(results)

    # Thursday final 18:00–19:00 UTC (confirm)
    if now.weekday() == 3 and 18 <= now.hour < 19 and not week["thu_sent"]:
        send_telegram(structure_block(data, "Thursday final", opens))
        for sym in SYMBOLS:
            if sym not in data:
                continue
            o = opens.get(sym, data[sym]["open"])
            side = band_side(o, data[sym]["low"], data[sym]["high"], OFFICIAL)
            week.setdefault(sym, {})["thu_confirm"] = side
            week[sym]["snapshot"] = {
                "open": o,
                "low": data[sym]["low"],
                "high": data[sym]["high"],
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        week["thu_sent"] = True
        save_results(results)

    # Sunday report ≥ 21:00 UTC
    if now.weekday() == 6 and now.hour >= 21 and not week["sun_sent"]:
        send_telegram(sunday_message(data, results, opens))
        week["sun_sent"] = True
        save_results(results)

    # heartbeat telegram every HEARTBEAT_HOURS (silent file always)
    meta = results.setdefault("meta", {})
    last_hb = meta.get("last_hb_msg", 0)
    if time.time() - last_hb > HEARTBEAT_HOURS * 3600:
        # file heartbeat only — no spam unless you want it
        meta["last_hb_msg"] = time.time()
        save_results(results)

    touch_heartbeat(ok=True, note=f"ok symbols={len(data)}")


def main():
    ensure_data_dir()
    delete_webhook()
    results = load_results()
    save_results(results)
    send_telegram(
        f"🤖 <b>{BOT_NAME} is online 24/7</b>\n"
        "Binance: BTC ETH BNB LTC DOGE SOL XRP\n"
        "Mon open · Wed early 18:00 UTC · Thu final 18:00 UTC · Sun ≥21:00 UTC\n"
        "Official 0.25% · /status /scores /bands /journal"
    )
    while True:
        try:
            results = load_results()
            check_stale_heartbeat(results)
            poll_commands(results)
            run_once(results)
            touch_heartbeat(ok=True)
        except Exception as e:
            print("loop error", e)
            try:
                touch_heartbeat(ok=False, note=str(e))
            except Exception:
                pass
        time.sleep(POLL_SLEEP)


if __name__ == "__main__":
    main()
