#!/usr/bin/env python3
"""XAU/USD sweep signali -> Telegram.

Qoida (har bir taymfreymda alohida, oldingi sham bilan solishtiriladi):
  SELL: sham oldingi shamning high'idan yuqoriga chiqadi, lekin uning ichida (pastda) va qizil yopiladi.
  BUY : sham oldingi shamning low'idan pastga tushadi, lekin uning ichida (tepada) va yashil yopiladi.
Signal faqat sham YOPILGANDAN keyin tekshiriladi.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

# ====== SOZLAMALAR (kerak bo'lsa o'zgartiring) ======
SYMBOL = "XAU/USD"
# Twelve Data interval -> (nomi, daqiqa). Keraksiz taymfreymni o'chirib tashlang.
TIMEFRAMES = {
    "15min": ("M15", 15),
    "1h": ("H1", 60),
    "4h": ("H4", 240),
}
MIN_SWEEP = 0.0      # sweep kamida shuncha dollar chiqishi kerak (0 = faqat sizning qoidangiz)
GIVE_UP_MIN = 30     # sham ma'lumoti shuncha daqiqada kelmasa, tashlab ketiladi
STATE_FILE = "state.json"
LOCAL_TZ = ZoneInfo("Asia/Tashkent")
# =====================================================


def env(name):
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"{name} topilmadi (GitHub Secrets ga qo'shing)")
    return value


def send(text):
    r = requests.post(
        f"https://api.telegram.org/bot{env('TELEGRAM_TOKEN')}/sendMessage",
        json={"chat_id": env("TELEGRAM_CHAT_ID"), "text": text},
        timeout=30,
    )
    r.raise_for_status()


def fetch(interval):
    r = requests.get(
        "https://api.twelvedata.com/time_series",
        params={
            "symbol": SYMBOL,
            "interval": interval,
            "outputsize": 6,
            "timezone": "UTC",
            "order": "desc",
            "apikey": env("TWELVE_DATA_KEY"),
        },
        timeout=30,
    )
    data = r.json()
    if data.get("status") == "error" or "values" not in data:
        raise RuntimeError(data.get("message", str(data)))
    candles = {}
    for v in data["values"]:
        t = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        candles[t] = {k: float(v[k]) for k in ("open", "high", "low", "close")}
    return candles


def floor_time(now, minutes):
    base = now.replace(hour=0, minute=0, second=0, microsecond=0)
    mins = int((now - base).total_seconds() // 60)
    return base + timedelta(minutes=(mins // minutes) * minutes)


def market_open(now):
    """Oltin taxminan Yakshanba 22:00 UTC dan Juma 22:00 UTC gacha savdo qilinadi."""
    wd = now.weekday()  # Dushanba = 0
    if wd == 5:
        return False
    if wd == 4 and now.hour >= 22:
        return False
    if wd == 6 and now.hour < 22:
        return False
    return True


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1, sort_keys=True)


def fmt(x):
    return f"{x:.2f}"


def check_signal(c, p):
    """c = yopilgan sham, p = undan oldingi sham. 'SELL', 'BUY' yoki None qaytaradi."""
    if c["high"] > p["high"] + MIN_SWEEP and c["close"] < p["high"] and c["close"] < c["open"]:
        return "SELL"
    if c["low"] < p["low"] - MIN_SWEEP and c["close"] > p["low"] and c["close"] > c["open"]:
        return "BUY"
    return None


def run(now=None):
    now = now or datetime.now(timezone.utc)
    state = load_state()

    if os.environ.get("SEND_TEST") == "true":
        try:
            candles = fetch("15min")
            last = max(candles)
            send(f"✅ Bot ishlayapti.\n{SYMBOL} oxirgi M15 sham ({last:%Y-%m-%d %H:%M} UTC): close {fmt(candles[last]['close'])}")
        except Exception as e:  # noqa: BLE001
            send(f"❌ Narx ma'lumoti olinmadi: {e}")
        save_state(state)
        return

    if not market_open(now):
        save_state(state)
        return

    for interval, (label, minutes) in TIMEFRAMES.items():
        cur_start = floor_time(now, minutes)
        last_closed = cur_start - timedelta(minutes=minutes)
        key = last_closed.isoformat()
        if state.get(interval) == key:
            continue  # bu sham allaqachon tekshirilgan

        try:
            candles = fetch(interval)
        except Exception as e:  # noqa: BLE001
            print(f"{label}: ma'lumot olinmadi: {e}")
            continue

        if last_closed not in candles:
            if now - cur_start > timedelta(minutes=GIVE_UP_MIN):
                state[interval] = key  # sham umuman kelmadi (masalan, tanaffus)
            continue

        keys = sorted(candles)
        idx = keys.index(last_closed)
        if idx == 0:
            state[interval] = key
            continue
        c, p = candles[last_closed], candles[keys[idx - 1]]

        signal = check_signal(c, p)
        if signal:
            closed_local = cur_start.astimezone(LOCAL_TZ)
            if signal == "SELL":
                head = f"🔴 SELL signal | {SYMBOL} {label}"
                body = (f"Oldingi sham high: {fmt(p['high'])}\n"
                        f"Sweep high: {fmt(c['high'])}\n"
                        f"Close: {fmt(c['close'])} (qizil)")
            else:
                head = f"🟢 BUY signal | {SYMBOL} {label}"
                body = (f"Oldingi sham low: {fmt(p['low'])}\n"
                        f"Sweep low: {fmt(c['low'])}\n"
                        f"Close: {fmt(c['close'])} (yashil)")
            send(f"{head}\n{body}\nSham yopildi: {closed_local:%H:%M} (Toshkent vaqti)")

        state[interval] = key  # xabar yuborilgandan keyingina belgilanadi

    save_state(state)


if __name__ == "__main__":
    run()
