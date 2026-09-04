def get_daily_klines(symbol, limit=14):
    # 1) Binance
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": symbol, "interval": "1d", "limit": limit},
            timeout=15,
        ).json()
        if isinstance(r, list):
            candles = []
            for k in r:
                candles.append({
                    "open_time": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc),
                    "open": float(k[1]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                })
            if candles:
                return candles
    except Exception as e:
        print("binance error", symbol, e)

    # 2) Bybit backup
    r = requests.get(
        "https://api.bybit.com/v5/market/kline",
        params={"category": "spot", "symbol": symbol, "interval": "D", "limit": limit},
        timeout=15,
    ).json()
    rows = r.get("result", {}).get("list", [])
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
