import json, os, time, datetime, threading, requests, uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

cloud_state = {
    "bal": 10000.0,
    "pos": list(),
    "hist": list(),
    "logs": list(["[AURA LONG-LIFE V8] Auto-ATR + Live Config + Real Brokerage Active!"]),
    "algo": True,
    "cfg": {"mode": "AUTO", "lot": 0.3, "sl": 0.18, "tp": 0.35, "mx": 3, "brk": True},
    "last_t": 0
}
prices = {"XBTUSD": 0.0, "ETHUSD": 0.0, "NIFTY50": 0.0, "BANKNIFTY": 0.0, "EURUSD": 0.0}
candles_cache = dict()

if os.path.exists("aura_db.json"):
    try:
        with open("aura_db.json", "r") as f:
            cloud_state.update(json.load(f))
    except Exception:
        pass

def save_db():
    try:
        with open("aura_db.json", "w") as f:
            json.dump(cloud_state, f)
    except Exception:
        pass

def add_log(m):
    ts = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).strftime("%H:%M:%S")
    cloud_state["logs"].insert(0, f"[{ts}] {m}")
    if len(cloud_state["logs"]) > 40:
        cloud_state["logs"].pop()
    save_db()

NSE_HOLIDAYS = ("2026-11-24", "2026-12-25", "2027-01-26", "2027-03-26", "2027-08-15", "2027-10-02")

def is_open(s):
    if s in ("XBTUSD", "ETHUSD"):
        return True
    ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    if ist.weekday() >= 5:
        return False
    if s == "EURUSD":
        return True
    if ist.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
        return False
    mins = ist.hour * 60 + ist.minute
    return 555 <= mins <= 930

def get_c(row, idx):
    return float(tuple(row)[idx])

def calc_brokerage(s, entry, exit_p, lot):
    if not cloud_state["cfg"].get("brk", True):
        return 0.0
    if s in ("XBTUSD", "ETHUSD"):
        notional = (entry + exit_p) * lot
        return round(notional * 0.0005, 2)
    elif s in ("NIFTY50", "BANKNIFTY"):
        return round(0.65 + (entry + exit_p) * lot * 0.00004, 2)
    else:
        return round(7.0 * lot, 2)

def fetch_feed(pair, iv=5):
    hdr = {"User-Agent": "Mozilla/5.0"}
    try:
        if pair in ("NIFTY50", "BANKNIFTY", "EURUSD"):
            ymap = {"NIFTY50": "%5ENSEI", "BANKNIFTY": "%5ENSEBANK", "EURUSD": "EURUSD%3DX"}
            iv_s = "1m" if iv == 1 else ("5m" if iv == 5 else ("15m" if iv == 15 else ("1h" if iv == 60 else "1d")))
            rng = "1d" if iv == 1 else ("5d" if iv <= 15 else ("1mo" if iv == 60 else "3mo"))
            yhost = "https://query1.finance." + "yahoo.com"
            r = requests.get(f"{yhost}/v8/finance/chart/{ymap[pair]}?interval={iv_s}&range={rng}", headers=hdr, timeout=5).json()
            res = list(r["chart"]["result"]).pop(0)
            ts = list(res.get("timestamp", list()))
            q = list(res["indicators"]["quote"]).pop(0)
            op_l, hi_l, lo_l, cl_l = list(q["open"]), list(q["high"]), list(q["low"]), list(q["close"])
            cs = list()
            start_i = max(0, len(ts) - 45)
            for i in range(start_i, len(ts)):
                o_v, c_v = op_l[i], cl_l[i]
                if o_v is not None and c_v is not None:
                    cs.append(list((ts[i], o_v, hi_l[i], lo_l[i], c_v)))
            if cs:
                last_row = list(cs).pop()
                prices[pair] = round(get_c(last_row, 4), 4 if pair == "EURUSD" else 2)
                candles_cache[f"{pair}_{iv}"] = cs
            return cs
        base = "https://api." + "kraken.com"
        r = requests.get(f"{base}/0/public/OHLC?pair={pair}&interval={iv}", headers=hdr, timeout=4).json()
        key = list(k for k in r["result"].keys() if k != "last").pop(0)
        raw = list(r["result"][key])
        cs = raw[max(0, len(raw) - 45):]
        if cs:
            last_row = list(cs).pop()
            prices[pair] = round(get_c(last_row, 4), 2)
            candles_cache[f"{pair}_{iv}"] = cs
        return cs
    except Exception:
        return candles_cache.get(f"{pair}_{iv}", list())
def background_24x7_bot():
    cd_map = dict()
    loss_streak = 0
    pause_until = 0
    while True:
        try:
            now = time.time()
            if now < pause_until:
                time.sleep(5)
                continue
            cfg = cloud_state["cfg"]
            for s in ("XBTUSD", "ETHUSD", "NIFTY50", "BANKNIFTY", "EURUSD"):
                if not is_open(s):
                    continue
                cs = fetch_feed(s, 5)
                if len(cs) < 22:
                    continue
                if s in ("NIFTY50", "BANKNIFTY"):
                    ist_today = (datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)).date()
                    if datetime.datetime.fromtimestamp(get_c(list(cs).pop(), 0) + 19800).date() != ist_today:
                        continue

                last_row = list(cs).pop()
                dec = 4 if s == "EURUSD" else 2
                cur = round(get_c(last_row, 4), dec)
                mult = 1.0 if s == "XBTUSD" else (10.0 if s == "ETHUSD" else (10000.0 if s == "EURUSD" else 10.0))

                cl = list(get_c(c, 4) for c in cs)
                e9 = sum(cl[len(cl) - 9:]) / 9.0
                e21 = sum(cl[len(cl) - 21:]) / 21.0
                prev_c = cl[len(cl) - 2]

                for x in list(cloud_state["pos"]):
                    if x["s"] == s:
                        diff = (cur - x["o"]) if x["t"] == "BUY" else (x["o"] - cur)
                        gross_p = diff * x["l"] * mult
                        brk = calc_brokerage(s, x["o"], cur, x["l"])
                        x["brk"] = brk
                        x["p"] = round(gross_p - brk, 2)
                        if not x["be"] and x["p"] >= 3.5:
                            x["sl"] = x["o"]
                            x["be"] = True
                            add_log(f"Break-Even Lock: {s} SL @ {x['o']}")

                        flip_sell = (x["t"] == "BUY" and cur < e9 < e21 and x["p"] <= -4.5)
                        flip_buy = (x["t"] == "SELL" and cur > e9 > e21 and x["p"] <= -4.5)
                        ema_profit = (x["p"] >= 3.5) and ((x["t"] == "BUY" and cur < e9) or (x["t"] == "SELL" and cur > e9))
                        h_sl = x["sl"] > 0 and ((x["t"] == "BUY" and cur <= x["sl"]) or (x["t"] == "SELL" and cur >= x["sl"]))
                        h_tp = x["tp"] > 0 and ((x["t"] == "BUY" and cur >= x["tp"]) or (x["t"] == "SELL" and cur <= x["tp"]))

                        if h_sl or h_tp or ema_profit or flip_sell or flip_buy or x["p"] <= -14.0 or x["p"] >= 14.0:
                            cloud_state["pos"].remove(x)
                            cloud_state["bal"] = round(cloud_state["bal"] + x["p"], 2)
                            cloud_state["hist"].insert(0, {**x, "c": cur})
                            if x["p"] < 0:
                                loss_streak += 1
                                if loss_streak >= 2:
                                    pause_until = now + 1800
                                    loss_streak = 0
                                    add_log("[SHIELD] 2 Losses -> 30m Auto-Cooloff Activated!")
                            else:
                                loss_streak = 0

                            if (flip_sell or flip_buy) and cloud_state["algo"]:
                                new_side = "SELL" if flip_sell else "BUY"
                                sl_d = cur * (cfg["sl"] / 100.0)
                                tp_d = cur * (cfg["tp"] / 100.0)
                                n_sl = round(cur - sl_d if new_side == "BUY" else cur + sl_d, dec)
                                n_tp = round(cur + tp_d if new_side == "BUY" else cur - tp_d, dec)
                                cloud_state["pos"].append({"id": int(now * 1000) % 99999, "s": s, "t": new_side, "l": x["l"], "o": cur, "sl": n_sl, "tp": n_tp, "p": 0.0, "brk": 0.0, "be": False})
                                add_log(f"AUTO-FLIP {x['t']}->{new_side} {s} (Net P&L: ${x['p']}, Brk: ${brk})")
                            else:
                                cd_map[s] = now + (180 if x["p"] < 0 else 45)
                                add_log(f"Exit {x['t']} {s} Net P&L: ${x['p']} (Brk/Tax: ${brk})")

                if now < cd_map.get(s, 0):
                    continue

                hi_20 = max(cl[len(cl) - 20:])
                lo_20 = min(cl[len(cl) - 20:])
                rng_20 = hi_20 - lo_20
                if rng_20 <= 0 or (rng_20 / cur) * 100.0 < 0.08:
                    continue

                pos_in_range = (cur - lo_20) / rng_20
                ema_gap_pct = (abs(e9 - e21) / cur) * 100.0
                user_mode = cfg.get("mode", "AUTO")
                mode = ("TREND" if ema_gap_pct >= 0.08 else "RANGE") if user_mode == "AUTO" else user_mode

                g, l = 0.0, 0.0
                for i in range(len(cl) - 14, len(cl)):
                    df = cl[i] - cl[i - 1]
                    if df >= 0:
                        g += df
                    else:
                        l -= df
                rsi = 100.0 - (100.0 / (1.0 + (g / (l if l > 0 else 0.0001))))

                sig = ""
                if mode == "TREND":
                    if cur > e9 > e21 and 50 <= rsi <= 70 and pos_in_range <= 0.80:
                        sig = "BUY"
                    elif cur < e9 < e21 and 30 <= rsi <= 50 and pos_in_range >= 0.20:
                        sig = "SELL"
                else:
                    if pos_in_range <= 0.35 and rsi <= 45 and cur > prev_c:
                        sig = "BUY"
                    elif pos_in_range >= 0.65 and rsi >= 55 and cur < prev_c:
                        sig = "SELL"

                if sig and cloud_state["algo"] and len(cloud_state["pos"]) < int(cfg["mx"]):
                    if not any(p["s"] == s for p in cloud_state["pos"]):
                        lot_sz = float(cfg["lot"])
                        sl_d = cur * (float(cfg["sl"]) / 100.0)
                        tp_d = cur * (float(cfg["tp"]) / 100.0)
                        sl = round(cur - sl_d if sig == "BUY" else cur + sl_d, dec)
                        tp = round(cur + tp_d if sig == "BUY" else cur - tp_d, dec)
                        cloud_state["pos"].append({"id": int(now * 1000) % 99999, "s": s, "t": sig, "l": lot_sz, "o": cur, "sl": sl, "tp": tp, "p": 0.0, "brk": 0.0, "be": False})
                        cd_map[s] = now + 60
                        add_log(f"[{mode}] {sig} {lot_sz} {s} @ {cur} (RSI:{round(rsi,1)})")
        except Exception:
            pass
        time.sleep(5)

threading.Thread(target=background_24x7_bot, daemon=True).start()

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def home():
    with open("ui.html", "r", encoding="utf-8") as f:
        return f.read()

@app.get("/api/live")
def live_proxy(pair: str = "XBTUSD", iv: int = 5):
    cs = fetch_feed(pair, iv)
    return {"ok": len(cs) > 0, "c": cs, "cloud": cloud_state, "prices": prices}

@app.post("/api/cfg")
def update_cfg(mode: str = "AUTO", lot: float = 0.3, sl: float = 0.18, tp: float = 0.35, mx: int = 3, brk: int = 1):
    cloud_state["cfg"] = {"mode": mode, "lot": lot, "sl": sl, "tp": tp, "mx": mx, "brk": bool(brk)}
    save_db()
    add_log(f"Live Config Saved: {mode} | Lot:{lot} SL:{sl}% TP:{tp}% Max:{mx} Brk:{'ON' if brk else 'OFF'}")
    return {"ok": True}

@app.post("/api/sync")
def sync_restore(data: dict):
    if cloud_state["bal"] == 10000.0 and len(cloud_state["hist"]) == 0 and data.get("hist"):
        cloud_state["bal"] = float(data.get("bal", 10000.0))
        cloud_state["hist"] = list(data.get("hist", []))
        save_db()
    return {"ok": True}

@app.post("/api/cmd")
def cloud_cmd(act: str, s: str = "XBTUSD", side: str = "BUY", l: float = 0.2, oid: int = 0):
    cur = prices.get(s, 0.0)
    if act == "ord" and cur > 0:
        cloud_state["pos"].append({"id": int(time.time() * 1000) % 99999, "s": s, "t": side, "l": l, "o": cur, "sl": 0.0, "tp": 0.0, "p": 0.0, "brk": 0.0, "be": False})
        add_log(f"Manual {side} {l} {s} @ {cur}")
    elif act == "cls":
        for x in list(cloud_state["pos"]):
            if x["id"] == oid:
                cloud_state["pos"].remove(x)
                cloud_state["bal"] = round(cloud_state["bal"] + x["p"], 2)
                cloud_state["hist"].insert(0, {**x, "c": prices.get(x["s"], x["o"])})
                add_log(f"Closed {x['s']} Net P&L: ${x['p']} (Brk: ${x.get('brk',0)})")
    elif act == "clsAll":
        for x in list(cloud_state["pos"]):
            cloud_state["bal"] = round(cloud_state["bal"] + x["p"], 2)
            cloud_state["hist"].insert(0, {**x, "c": prices.get(x["s"], x["o"])})
        cloud_state["pos"].clear()
        add_log("Closed All Positions")
    elif act == "tog":
        cloud_state["algo"] = not cloud_state["algo"]
        add_log(f"AURA AI PRO {'ON' if cloud_state['algo'] else 'OFF'}")
    return {"ok": True}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=10000)
                                
