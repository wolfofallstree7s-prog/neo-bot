"""
Neo — crypto weekly structure alert bot (v3: risk-to-invalidation, pullback/invalidation alerts, entry-stage stats)
"""
import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

BOT_NAME = "Neo"
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
DATA_DIR = "/data"
RESULTS_FILE = os.path.join(DATA_DIR, "results.json")
HEARTBEAT_FILE = os.path.join(DATA_DIR, "heartbeat.json")

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
NEAR_OPEN_PCT = 0.15   # pullback alert zone around the open
BANDS = [0.25, 0.50, 0.75, 1.00]
EXTENDED_PCT = 5.0
SKIP_EXTENDED_PCT = 8.0
HEARTBEAT_HOURS = 6
STALE_HOURS = 2
POLL_SLEEP = 15
SEND_HEARTBEAT_MSG = os.environ.get("HEARTBEAT_MSG", "0") == "1"

# Schedule (UTC)
MON_AT = (0, 30)
MID_HOUR = 18
SUN_HOUR = 21

HIST_SEED = {
    "BTC":  {"long_w": 21, "long_n": 21, "short_w": 22, "short_n": 23},
    "ETH":  {"long_w": 13, "long_n": 14, "short_w": 21, "short_n": 23},
    "BNB":  {"long_w": 16, "long_n": 16, "short_w": 17, "short_n": 19},
    "LTC":  {"long_w": 9,  "long_n": 9,  "short_w": 19, "short_n": 22},
    "DOGE": {"long_w": 8,  "long_n": 8,  "short_w": 19, "short_n": 20},
    "SOL":  {"long_w": 7,  "long_n": 8,  "short_w": 8,  "short_n": 9},
    "XRP":  {"long_w": 8,  "long_n": 9,  "short_w": 16, "short_n": 18},
}
LIVE_SEED = {
    "BTC":  {"long_w": 0, "long_n": 0, "short_w": 1, "short_n": 1},
    "ETH":  {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0},
    "BNB":  {"long_w": 1, "long_n": 1, "short_w": 0, "short_n": 0},
    "LTC":  {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0},
    "DOGE": {"long_w": 1, "long_n": 1, "short_w": 0, "short_n": 0},
    "SOL":  {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0},
    "XRP":  {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0},
}
EMPTY_BOOK = {"long_w": 0, "long_n": 0, "short_w": 0, "short_n": 0}


# ─── storage ───────────────────────────────────────────────────────────────

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
    for name, seed in HIST_SEED.items():
        r["hist"].setdefault(name, dict(seed))

    if "live" not in r:
        r["live"] = {name: dict(v) for name, v in LIVE_SEED.items()}

    meta = r.setdefault("meta", {})
    if not meta.get("live_restored_v1"):
        r["live"] = {name: dict(v) for name, v in LIVE_SEED.items()}
        meta["live_restored_v1"] = True

    for name in SYMBOLS.values():
        r["live"].setdefault(name, dict(EMPTY_BOOK))
        r["hist"].setdefault(name, dict(EMPTY_BOOK))

    r.setdefault("weeks", {})
    r.setdefault("journal", [])
    r.setdefault("stage", {})
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
            f"⚠️ <b>{BOT_NAME}</b> heartbeat was stale ({age_h:.1f}h) — "
            f"bot was down or stuck. Check Railway logs."
        )
        flag["stale_alerted"] = True
        save_results(results)
    elif age_h < STALE_HOURS and flag.get("stale_alerted"):
        flag["stale_alerted"] = False
        save_results(results)


def week_key(dt: datetime) -> str:
    d = dt.date()
    return (d - timedelta(days=d.weekday())).isoformat()


def scrub(e):
    return str(e).replace(TOKEN, "***")


def retry(fn, tries=3, delay=2, label=""):
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            last = e
            print(f"retry {label} {i+1}/{tries}: {scrub(e)}")
            time.sleep(delay * (i + 1))
    raise last if last else RuntimeError("retry failed")


# ─── telegram ──────────────────────────────────────────────────────────────

def send_telegram(text: str):
    def _send():
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        body = text if len(text) <= 4000 else text[:3900] + "\n…"
        r = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": body,
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
        print("telegram hard fail", scrub(e))


def delete_webhook():
    try:
        requests.get(
            f"https://api.telegram.org/bot{TOKEN}/deleteWebhook",
            params={"drop_pending_updates": False},
            timeout=15,
        )
    except Exception as e:
        print("deleteWebhook", scrub(e))


def set_bot_commands():
    commands = [
        {"command": "status", "description": "Official 0.25% structure now"},
        {"command": "scores", "description": "Hist + live win rates"},
        {"command": "bands", "description": "All bands 0.25–1.00%"},
        {"command": "journal", "description": "Last 5 scored setups"},
        {"command": "capture", "description": "Wed vs Thu entry stats"},
        {"command": "help", "description": "Commands + schedule"},
    ]
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/setMyCommands",
            json={"commands": commands},
            timeout=15,
        )
        r.raise_for_status()
    except Exception as e:
        print("setMyCommands", scrub(e))


# ─── price data ────────────────────────────────────────────────────────────

def kline_row(k):
    return {
        "open_time": int(k[0]),
        "open": float(k[1]),
        "high": float(k[2]),
        "low": float(k[3]),
        "close": float(k[4]),
    }


def fetch_binance_klines(symbol, interval="1d", limit=21):
    last_err = None
    for base in BINANCE_BASES:
        try:
            r = requests.get(
                f"{base}/api/v3/klines",
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
    raise RuntimeError(f"binance fail {symbol}: {last_err}")


def fetch_yahoo_daily(yahoo_sym, limit=21):
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

    days = [monday_date + timedelta(days=i) for i in range(7)]
    present = [d for d in days if d in by_day]
    if not present:
        return None

    mon_d = present[0]
    last = by_day[present[-1]]
    return {
        "open": by_day[mon_d]["open"],
        "high": max(by_day[d]["high"] for d in present),
        "low": min(by_day[d]["low"] for d in present),
        "close": last["close"],
        "bar_ts": last["open_time"],
        "monday_date": mon_d.isoformat(),
    }


def snapshot_week():
    now = datetime.now(timezone.utc)
    monday_date = datetime.fromisoformat(week_key(now)).date()
    out, errors, venue_used = {}, [], {}

    for sym, name in SYMBOLS.items():
        got, venue = None, None
        try:
            raw, base = fetch_binance_klines(sym, "1d", 21)
            got = rows_to_week([kline_row(k) for k in raw], monday_date)
            if got:
                venue = "binance:" + base.replace("https://", "")
        except Exception as e:
            print("binance", sym, e)

        if not got:
            try:
                ysym = YAHOO_MAP.get(sym)
                if ysym:
                    got = rows_to_week(fetch_yahoo_daily(ysym, 21), monday_date)
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


# ─── structure math ────────────────────────────────────────────────────────

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


def _open_for(sym, data, opens):
    return (opens or {}).get(sym, data[sym]["open"])


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


def risk_to_inv(side, px, o):
    inv_l, inv_s = inv_prices(o)
    if side == "LONG":
        return (px - inv_l) / px * 100
    if side == "SHORT":
        return (inv_s - px) / px * 100
    return None


def simulate(side, entry, o, lo, hi, close):
    """Entry -> week end; exits at the invalidation line if it broke.
    Ignores slippage and gaps through the line."""
    inv_l, inv_s = inv_prices(o)
    if side == "LONG":
        stopped = lo < inv_l
        exit_px = inv_l if stopped else close
        return (exit_px - entry) / entry * 100, stopped
    stopped = hi > inv_s
    exit_px = inv_s if stopped else close
    return (entry - exit_px) / entry * 100, stopped


def record_stages(results, week, sym, name, o, lo, hi, close):
    stage = results.setdefault("stage", {})
    out = []
    for st in ("wed", "thu"):
        w = week.get(sym, {})
        side, px = w.get(f"{st}_confirm"), w.get(f"{st}_px")
        if side not in ("LONG", "SHORT") or not px or w.get(f"{st}_scored"):
            continue
        ret, stopped = simulate(side, px, o, lo, hi, close)
        b = stage.setdefault(st, {}).setdefault(
            name, {"n": 0, "w": 0, "stopped": 0, "sum_ret": 0.0})
        b["n"] += 1
        b["w"] += 1 if ret > 0 else 0
        b["stopped"] += 1 if stopped else 0
        b["sum_ret"] += ret
        w[f"{st}_scored"] = True
        out.append(f"{st.upper()}→end {ret:+.2f}%" + (" (stopped)" if stopped else ""))
    return out


def capture_message(results):
    lines = [f"<b>{BOT_NAME} entry-stage stats</b>",
             "<i>entry price → week end, stopped at the 0.25% line</i>"]
    for st, label in (("wed", "Wed early"), ("thu", "Thu final")):
        book = results.get("stage", {}).get(st, {})
        n = sum(b["n"] for b in book.values())
        if not n:
            lines.append(f"{label}: no scored trades yet")
            continue
        w = sum(b["w"] for b in book.values())
        s = sum(b["stopped"] for b in book.values())
        r = sum(b["sum_ret"] for b in book.values())
        lines.append(f"<b>{label}</b>: {w}/{n} win · {s} stopped · avg {r/n:+.2f}%")
        for name, b in book.items():
            if b["n"]:
                lines.append(f"  {name} {b['w']}/{b['n']} avg {b['sum_ret']/b['n']:+.2f}%")
    return "\n".join(lines)


def check_live_alerts(results, week, data, opens, now):
    changed = False
    today = now.date().isoformat()
    for sym, name in SYMBOLS.items():
        if sym not in data:
            continue
        w = week.setdefault(sym, {})
        o = _open_for(sym, data, opens)
        lo, hi, px = data[sym]["low"], data[sym]["high"], data[sym]["close"]
        inv_l, inv_s = inv_prices(o)

        # 1) invalidation of an issued signal
        sig = w.get("thu_confirm") if w.get("thu_confirm") in ("LONG", "SHORT") \
            else w.get("wed_confirm")
        if sig in ("LONG", "SHORT") and not w.get("inv_alerted"):
            broke = lo < inv_l if sig == "LONG" else hi > inv_s
            if broke:
                line = inv_l if sig == "LONG" else inv_s
                send_telegram(
                    f"🛑 <b>{BOT_NAME}</b> {name} {sig} INVALIDATED\n"
                    f"Crossed {fmt_p(line)} (open {fmt_p(o)}). Setup is dead."
                )
                w["inv_alerted"] = True
                changed = True

        # 2) pullback to the open while structure is still alive
        if now.weekday() >= 1 and not w.get("inv_alerted") \
                and w.get("pullback_day") != today:
            side = band_side(o, lo, hi, OFFICIAL)
            near = NEAR_OPEN_PCT / 100
            hit = (side == "LONG" and inv_l < px <= o * (1 + near)) or \
                  (side == "SHORT" and o * (1 - near) <= px < inv_s)
            if hit:
                risk = risk_to_inv(side, px, o)
                send_telegram(
                    f"🎯 <b>{BOT_NAME}</b> {name} {side} pullback to open\n"
                    f"Price {fmt_p(px)} · open {fmt_p(o)}\n"
                    f"Risk to invalidation {risk:.2f}%"
                )
                w["pullback_day"] = today
                changed = True
    if changed:
        save_results(results)


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
        "<i>Manual risk: ≤1% account to invalidation · "
        "1–2 theme positions · flat by Sunday · "
        "if already extended → half size or skip</i>"
    )


def schedule_text():
    return (
        f"Schedule (UTC): Mon ≥{MON_AT[0]:02d}:{MON_AT[1]:02d} · "
        f"Wed early ≥{MID_HOUR}:00 · Thu final ≥{MID_HOUR}:00 · "
        f"Sun report ≥{SUN_HOUR}:00"
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
    lines = [
        f"<b>{BOT_NAME} {title}</b>  official {OFFICIAL}%",
        asof_line(),
    ]
    for sym, name in SYMBOLS.items():
        if sym not in data:
            lines.append(f"⚪ {name} no data")
            continue
        o = _open_for(sym, data, opens)
        lo, hi = data[sym]["low"], data[sym]["high"]
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
            risk = risk_to_inv(side, data[sym]["close"], o)
            lines.append(
                f"{icon} <b>{name}</b> {side}\n"
                f"dip {dip:.2f}% | rally {rally:.2f}% | ext {ext:.2f}%\n"
                f"STRUCTURE: {side} OK · TRADE: {trade}\n"
                f"Inv L {fmt_p(inv_l)} · S {fmt_p(inv_s)}"
                + (f"\nRisk to inv now {risk:.2f}%" if risk is not None else "")
            )
    lines.append(risk_footer())
    return "\n\n".join(lines)


def bands_block(data, opens=None):
    lines = [f"<b>{BOT_NAME} all bands</b>", asof_line()]
    for thr in BANDS:
        chunk = [f"<b>{thr:.2f}%</b>"]
        for sym, name in SYMBOLS.items():
            if sym not in data:
                continue
            o = _open_for(sym, data, opens)
            side = band_side(o, data[sym]["low"], data[sym]["high"], thr)
            if side in ("LONG", "SHORT", "BOTH"):
                icon = {"LONG": "🟢", "SHORT": "🔴", "BOTH": "🟡"}[side]
                chunk.append(f"{icon} {name} {side}")
        if len(chunk) == 1:
            chunk.append("none")
        lines.append("\n".join(chunk))
    return "\n\n".join(lines)


def _book_line(name, hist, live):
    return (
        f"{name} long {hist.get('long_w', 0)}/{hist.get('long_n', 0)} "
        f"live {live.get('long_w', 0)}/{live.get('long_n', 0)} || "
        f"short {hist.get('short_w', 0)}/{hist.get('short_n', 0)} "
        f"live {live.get('short_w', 0)}/{live.get('short_n', 0)}"
    )


def sunday_message(data, results, opens):
    lines = [f"<b>{BOT_NAME} Sunday report</b>  {OFFICIAL}% live score", asof_line()]
    wk = results.get("current_week", "")
    week = results.setdefault("weeks", {}).setdefault(wk, {})

    for sym, name in SYMBOLS.items():
        if sym not in data:
            lines.append(f"{name}: no data")
            continue
        o = _open_for(sym, data, opens)
        c = data[sym]["close"]
        ret = (c - o) / o * 100
        conf = week.get(sym, {}).get("thu_confirm")
        live = results["live"].setdefault(name, dict(EMPTY_BOOK))
        hist = results["hist"].setdefault(name, dict(EMPTY_BOOK))

        scored = week.get(sym, {}).get("scored", False)
        if scored:
            note = "already scored"
        elif conf in ("LONG", "SHORT"):
            win = (c > o) if conf == "LONG" else (c < o)
            key = "long" if conf == "LONG" else "short"
            live[f"{key}_n"] += 1
            if win:
                live[f"{key}_w"] += 1
            note = f"{key} live updated" + ("" if win else " (loss)")
            week.setdefault(sym, {})["scored"] = True
            results.setdefault("journal", []).append({
                "week": wk,
                "pair": name,
                "side": conf,
                "open": o,
                "close": c,
                "ret_pct": ret,
                "win": win,
                "venue": data[sym].get("venue", "unknown"),
                "ts": datetime.now(timezone.utc).isoformat(),
            })
        elif conf:
            note = f"thu was {conf}"
        else:
            note = "no Thursday 0.25% setup"

        extra = record_stages(results, week, sym, name, o,
                              data[sym]["low"], data[sym]["high"], c)
        tail = f"\n{' · '.join(extra)}" if extra else ""
        lines.append(f"{name} week {ret:+.2f}%\n{note}{tail}\n" + _book_line(name, hist, live))
    return "\n\n".join(lines)


def scores_message(results):
    lines = [f"<b>{BOT_NAME} scores</b> {OFFICIAL}% only"]
    for name in SYMBOLS.values():
        h = results.get("hist", {}).get(name, {})
        lv = results.get("live", {}).get(name, {})
        lines.append(_book_line(name, h, lv))
    return "\n".join(lines)


def journal_message(results):
    j = results.get("journal", [])[-5:]
    if not j:
        return "journal empty"
    lines = [f"<b>{BOT_NAME} journal (last 5)</b>"]
    for row in j:
        lines.append(
            f"{row.get('week')} {row.get('pair')} {row.get('side')} "
            f"{row.get('ret_pct', 0):+.2f}% "
            f"{'WIN' if row.get('win') else 'LOSS'}"
        )
    return "\n".join(lines)


def help_message():
    return (
        f"<b>{BOT_NAME}</b>\n"
        "/status · /scores · /bands · /journal · /capture\n\n" + schedule_text()
    )


# ─── command polling ───────────────────────────────────────────────────────

def poll_commands(results):
    meta = results.setdefault("meta", {})
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    params = {"timeout": 0}
    if meta.get("tg_offset"):
        params["offset"] = meta["tg_offset"]

    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        updates = r.json().get("result", [])
    except Exception as e:
        print("poll", scrub(e))
        return
    if not updates:
        return

    cache = {}

    def get_snap():
        if "snap" not in cache:
            snap, errs = snapshot_week()
            wk = results.get("weeks", {}).get(results.get("current_week", ""), {})
            opens = {s: snap[s]["open"] for s in snap}
            opens.update(wk.get("opens", {}))
            cache["snap"] = (snap, errs, opens)
        return cache["snap"]

    try:
        for upd in updates:
            meta["tg_offset"] = upd["update_id"] + 1
            try:
                msg = upd.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id", ""))
                if chat != str(CHAT_ID):
                    continue
                text = (msg.get("text") or "").strip().lower()
                if not text.startswith("/"):
                    continue
                cmd = text.split()[0].split("@")[0]

                if cmd == "/status":
                    snap, errs, opens = get_snap()
                    if errs:
                        send_telegram(f"⚠️ data fail: {', '.join(errs)}")
                    send_telegram(structure_block(snap, "status", opens))
                elif cmd in ("/start", "/help"):
                    send_telegram(help_message())
                elif cmd in ("/scores", "/score"):
                    send_telegram(scores_message(results))
                elif cmd == "/bands":
                    snap, _errs, opens = get_snap()
                    send_telegram(bands_block(snap, opens))
                elif cmd == "/journal":
                    send_telegram(journal_message(results))
                elif cmd == "/capture":
                    send_telegram(capture_message(results))
            except Exception as e:
                print("command error", scrub(e))
    finally:
        save_results(results)


# ─── schedule ──────────────────────────────────────────────────────────────

def run_once(results):
    now = datetime.now(timezone.utc)
    wk = week_key(now)
    results["current_week"] = wk
    week = results.setdefault("weeks", {}).setdefault(wk, {})
    for k in ("monday_sent", "wed_sent", "thu_sent", "sun_sent"):
        week.setdefault(k, False)

    data, errors = snapshot_week()
    if errors:
        meta = results.setdefault("meta", {})
        if time.time() - meta.get("last_err_alert", 0) > 3600:
            send_telegram(
                f"⚠️ <b>{BOT_NAME}</b> data fail this check: {', '.join(errors)}"
            )
            meta["last_err_alert"] = time.time()
            save_results(results)

    has_data = bool(data)

    if has_data:
        opens = week.setdefault("opens", {})
        opens_meta = week.setdefault("opens_meta", {})
        changed = False
        for sym in data:
            if sym not in opens:
                opens[sym] = data[sym]["open"]
                opens_meta[sym] = {
                    "monday_date": data[sym]["monday_date"],
                    "venue": data[sym].get("venue"),
                }
                changed = True
        if changed:
            save_results(results)
    opens = week.get("opens", {})

    if (now.weekday() == 0 and (now.hour, now.minute) >= MON_AT
            and not week["monday_sent"] and has_data):
        send_telegram(monday_message(data))
        week["monday_sent"] = True
        save_results(results)

    # Wednesday early: also records the entry price for stage stats
    if (now.weekday() == 2 and now.hour >= MID_HOUR
            and not week["wed_sent"] and has_data):
        send_telegram(structure_block(data, "Wednesday early", opens))
        for sym in SYMBOLS:
            if sym in data:
                o = _open_for(sym, data, opens)
                week.setdefault(sym, {})["wed_confirm"] = band_side(
                    o, data[sym]["low"], data[sym]["high"], OFFICIAL)
                week[sym]["wed_px"] = data[sym]["close"]
        week["wed_sent"] = True
        save_results(results)

    # Thursday final
    if (now.weekday() == 3 and now.hour >= MID_HOUR
            and not week["thu_sent"] and has_data):
        send_telegram(structure_block(data, "Thursday final", opens))
        for sym in SYMBOLS:
            if sym not in data:
                continue
            o = _open_for(sym, data, opens)
            side = band_side(o, data[sym]["low"], data[sym]["high"], OFFICIAL)
            week.setdefault(sym, {})["thu_confirm"] = side
            week[sym]["snapshot"] = {
                "open": o,
                "low": data[sym]["low"],
                "high": data[sym]["high"],
                "ts": now.isoformat(),
            }
            week[sym]["thu_px"] = data[sym]["close"]
        week["thu_sent"] = True
        save_results(results)

    # Live alerts: invalidation + pullback to open
    if has_data:
        check_live_alerts(results, week, data, opens, now)

    if (now.weekday() == 6 and now.hour >= SUN_HOUR
            and not week["sun_sent"] and has_data):
        send_telegram(sunday_message(data, results, opens))
        week["sun_sent"] = True
        save_results(results)

    meta = results.setdefault("meta", {})
    if time.time() - meta.get("last_hb_msg", 0) > HEARTBEAT_HOURS * 3600:
        meta["last_hb_msg"] = time.time()
        save_results(results)
        if SEND_HEARTBEAT_MSG:
            send_telegram(
                f"💓 <b>{BOT_NAME}</b> alive · symbols ok {len(data)}/{len(SYMBOLS)}"
            )
        else:
            print(f"heartbeat ok {now.isoformat()} symbols={len(data)}")

    touch_heartbeat(ok=True, note=f"ok symbols={len(data)}")


def main():
    ensure_data_dir()
    delete_webhook()
    set_bot_commands()
    results = load_results()
    save_results(results)
    send_telegram(
        f"🤖 <b>{BOT_NAME} is online 24/7</b>\n"
        "Binance (Yahoo fallback): BTC ETH BNB LTC DOGE SOL XRP\n"
        + schedule_text() + "\n"
        "Official 0.25% · /status /scores /bands /journal /capture"
    )
    while True:
        try:
            results = load_results()
            check_stale_heartbeat(results)
            poll_commands(results)
            run_once(results)
        except Exception as e:
            print("loop error", e)
            try:
                touch_heartbeat(ok=False, note=str(e))
            except Exception:
                pass
        time.sleep(POLL_SLEEP)


if __name__ == "__main__":
    main()
