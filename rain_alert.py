#!/usr/bin/env python3
"""
Aviso de lluvia: consulta la estación Weathercloud y avisa por Telegram
cuando la intensidad de lluvia pasa de 0.0 a > 0.0 (y cuando vuelve a 0).
Solo usa la librería estándar de Python.

Robustez:
- Reintenta la lectura de Weathercloud y el envío a Telegram con espera
  creciente (los fallos puntuales de red/bloqueo son habituales desde los
  runners de GitHub).
- Valida la respuesta: si no trae "rainrate" se considera error, nunca "0".
- Si Weathercloud falla de forma puntual, el run termina en verde con un
  warning y se reintenta a los 5 min. Solo falla (y GitHub avisa por correo)
  tras FAIL_AFTER ejecuciones seguidas sin poder leer el dato.
"""
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

STATION = os.environ.get("WC_STATION", "3764284879")
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
NOTIFY_STOP = os.environ.get("NOTIFY_STOP", "true").lower() == "true"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

ATTEMPTS = int(os.environ.get("ATTEMPTS", "3"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "10"))
FAIL_AFTER = int(os.environ.get("FAIL_AFTER", "6"))  # 6 runs x 5 min = 30 min
STALE_AFTER_S = int(os.environ.get("STALE_AFTER_S", "3600"))

PAGE_URL = f"https://app.weathercloud.net/d{STATION}"
DATA_URL = f"https://app.weathercloud.net/device/values/{STATION}"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def _backoff(attempt):
    time.sleep(min(2 ** attempt * 2, 15))  # 4 s, 8 s, 15 s...


def fetch_values_once():
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [("User-Agent", UA)]
    opener.open(PAGE_URL, timeout=HTTP_TIMEOUT).read()
    req = urllib.request.Request(DATA_URL, headers={
        "X-Requested-With": "XMLHttpRequest",
        "Referer": PAGE_URL,
        "Accept": "application/json",
    })
    with opener.open(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def get_values():
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            values = fetch_values_once()
            if not isinstance(values, dict) or "rainrate" not in values:
                raise ValueError(f"respuesta inesperada: {str(values)[:200]}")
            return values
        except Exception as e:  # red, HTTP 403/429/5xx, HTML en vez de JSON...
            last = e
            print(f"Weathercloud intento {attempt}/{ATTEMPTS}: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)
            if attempt < ATTEMPTS:
                _backoff(attempt)
    raise last


def send_telegram(text):
    data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": text}).encode()
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    last = None
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, data=data,
                                        timeout=HTTP_TIMEOUT + 5) as r:
                r.read()
            return
        except urllib.error.HTTPError as e:
            # 4xx (salvo 429) = token/chat mal: reintentar no sirve
            if 400 <= e.code < 500 and e.code != 429:
                raise
            last = e
        except Exception as e:
            last = e
        print(f"Telegram intento {attempt}/{ATTEMPTS}: "
              f"{type(last).__name__}: {last}", file=sys.stderr)
        if attempt < ATTEMPTS:
            _backoff(attempt)
    raise last


def load_state():
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        state.setdefault("raining", False)
        return state
    except (FileNotFoundError, json.JSONDecodeError):
        return {"raining": False}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        f.write("\n")


def main():
    state = load_state()

    try:
        values = get_values()
    except Exception as e:
        fails = int(state.get("fetch_failures", 0)) + 1
        msg = f"No se pudo leer Weathercloud ({fails} runs seguidos): {e}"
        if fails >= FAIL_AFTER:
            print(f"::error::{msg}", file=sys.stderr)
            return 1
        print(f"::warning::{msg}")
        state["fetch_failures"] = fails
        save_state(state)
        return 0

    rate = float(values.get("rainrate", 0) or 0)
    rain_today = values.get("rain")
    epoch = values.get("epoch")
    when = (datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M UTC")
            if epoch else "?")
    print(f"rainrate={rate} mm/h, rain today={rain_today} mm, dato de {when}")
    if epoch and time.time() - epoch > STALE_AFTER_S:
        print(f"::warning::El dato de Weathercloud tiene más de "
              f"{STALE_AFTER_S // 60} min ({when}); la estación puede estar "
              f"sin enviar datos")

    raining_now = rate > 0.0

    if raining_now and not state["raining"]:
        send_telegram(f"🌧️ Comença a ploure a Begues: {rate} mm/h "
                      f"(acumulat avui {rain_today} mm).\n{PAGE_URL}")
    elif not raining_now and state["raining"] and NOTIFY_STOP:
        send_telegram(f"☀️ Ha parat de ploure a Begues "
                      f"(acumulat avui {rain_today} mm).")

    if raining_now != state["raining"]:
        save_state({"raining": raining_now, "since": epoch})
        print("Estat canviat")
    elif "fetch_failures" in state:
        state.pop("fetch_failures")  # recuperado: limpiar el contador
        save_state(state)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
