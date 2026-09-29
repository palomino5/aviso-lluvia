#!/usr/bin/env python3
"""
Aviso de lluvia: consulta la estación Weathercloud y avisa por Telegram
cuando la intensidad de lluvia pasa de 0.0 a > 0.0 (y cuando vuelve a 0).
Solo usa la librería estándar de Python.
"""
import http.cookiejar
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

STATION = os.environ.get("WC_STATION", "3764284879")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
NOTIFY_STOP = os.environ.get("NOTIFY_STOP", "true").lower() == "true"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

PAGE_URL = f"https://app.weathercloud.net/d{STATION}"
DATA_URL = f"https://app.weathercloud.net/device/values/{STATION}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def get_values():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", UA)]
    opener.open(PAGE_URL, timeout=30).read()
    req = urllib.request.Request(DATA_URL, headers={
        "X-Requested-With": "XMLHttpRequest",
        "Referer": PAGE_URL,
        "Accept": "application/json",
    })
    with opener.open(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def send_telegram(text):
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    with urllib.request.urlopen(url, data=data, timeout=30) as r:
        r.read()


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"raining": False}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def main():
    values = get_values()
    rate = float(values.get("rainrate", 0) or 0)
    rain_today = values.get("rain")
    epoch = values.get("epoch")
    when = (datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M UTC")
            if epoch else "?")
    print(f"rainrate={rate} mm/h, rain today={rain_today} mm, dato de {when}")

    state = load_state()
    raining_now = rate > 0.0

    if raining_now and not state["raining"]:
        send_telegram(f"🌧️ Empieza a llover en Begues: {rate} mm/h "
                      f"(acumulado hoy {rain_today} mm).\n{PAGE_URL}")
    elif not raining_now and state["raining"] and NOTIFY_STOP:
        send_telegram(f"☀️ Ha dejado de llover en Begues "
                      f"(acumulado hoy {rain_today} mm).")

    if raining_now != state["raining"]:
        save_state({"raining": raining_now, "since": epoch})
        print("Estado cambiado")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
