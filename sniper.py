#!/usr/bin/env python3
"""
Sniper: Sekundengenaue Überwachung weniger, konkreter Produktseiten.

Der Unterschied zum normalen Monitor: der durchsucht viele Shop-Suchseiten alle
15 Minuten. Dieser hier beobachtet eine kurze Liste EXAKTER Produkt-URLs im
Sekundentakt und meldet in dem Moment, in dem eine Seite von "ausverkauft" oder
"bald" auf "kaufbar" springt. Genau das, was Cooking-Group-Pings leisten.

Was er NICHT macht: kaufen, Zahlungsdaten speichern, Bot-Schutz umgehen.
Er meldet, gekauft wird von Hand. Der Alert enthält den Direktlink.

Start:  python3 sniper.py            (Intervall 15 s)
        SNIPER_INTERVALL=8 python3 sniper.py
"""

import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
STATE_FILE = Path(os.environ.get("STATE_FILE", "sniper_state.json"))
INTERVALL = float(os.environ.get("SNIPER_INTERVALL", "15"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
    "Cache-Control": "no-cache",
}

# --- Watchlist ---------------------------------------------------------------
# Nur wenige, dafür die richtigen. Jede URL wird einzeln abgefragt, deshalb
# kostet jeder Eintrag Tempo. Faustregel: höchstens 10 bis 15 Stück.
# "uvp" dient der Sofort-Einschätzung im Alert.
WATCHLIST = [
    {"name": "30 Jahre Ultra-Premium (Card Corner)",
     "url": "https://www.card-corner.de/Pokemon-30-Jahre-Ultra-Premium-Kollektion", "uvp": 129.99},
    {"name": "30 Jahre Top-Trainer-Box (Card Corner)",
     "url": "https://www.card-corner.de/Pokemon-30-Jahre-Top-Trainer-Box", "uvp": 54.99},
    {"name": "30 Jahre Booster Bundle (Card Corner)",
     "url": "https://www.card-corner.de/Pokemon-30-Jahre-Booster-Bundle", "uvp": 31.99},
    {"name": "30th Celebration UPC Night (GeeksHeaven)",
     "url": "https://geeksheaven.de/products/pokemon-30th-celebration-ultra-premium-collection-night-pikachu-ex-umbreon-ex-englisch-vorbestellung",
     "uvp": 129.99},
    {"name": "30th Celebration UPC Day (GeeksHeaven)",
     "url": "https://geeksheaven.de/products/pokemon-30th-celebration-ultra-premium-collection-day-pikachu-ex-espeon-ex-englisch-vorbestellung",
     "uvp": 129.99},
    {"name": "30th Celebration ETB (GeeksHeaven)",
     "url": "https://geeksheaven.de/products/pokemon-30th-celebration-elite-trainer-box-englisch-vorbestellung",
     "uvp": 54.99},
    {"name": "30th Celebration Booster Bundle (GeeksHeaven)",
     "url": "https://geeksheaven.de/products/pokemon-30th-celebration-booster-bundle-englisch-vorbestellung",
     "uvp": 31.99},
    {"name": "30th Celebration UPC (Feenturm)",
     "url": "https://feenturm.de/products/pokemon-30th-celebration-ultra-premium-collection-deutsch-jetzt-vorbestellen",
     "uvp": 129.99},
]

_only = os.environ.get("SNIPER_ONLY", "").strip()
if _only:
    WATCHLIST = [w for w in WATCHLIST if _only.lower() in w["name"].lower()]

# --- Kaufbarkeit erkennen -----------------------------------------------------
NICHT_KAUFBAR = [
    "ausverkauft", "sold out", "nicht verfügbar", "nicht verfuegbar",
    "vergriffen", "verkauf startet", "demnächst", "demnaechst",
    "coming soon", "benachrichtigen", "derzeit nicht", "out of stock",
]
KAUFBAR = [
    "in den warenkorb", "in den einkaufswagen", "add to cart", "jetzt kaufen",
    "sofort lieferbar", "auf lager", "vorbestellen", "pre-order", "preorder",
]

PREIS_RE = re.compile(r"(\d{1,4})[.,](\d{2})\s*€|€\s*(\d{1,4})[.,](\d{2})")


def hole(url: str) -> str:
    """Seite holen, ohne Cache. Bei Block einmal über curl nachfassen."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code == 200:
            if "charset" not in r.headers.get("Content-Type", "").lower():
                r.encoding = r.apparent_encoding
            return r.text
    except Exception:
        pass
    try:
        p = subprocess.run(
            ["curl", "-sSL", "--max-time", "12", "-A", HEADERS["User-Agent"], url],
            capture_output=True, text=True, timeout=15,
        )
        if p.returncode == 0:
            return p.stdout
    except Exception:
        pass
    return ""


def aus_strukturdaten(soup):
    """Liest Preis und Verfügbarkeit aus den schema.org-Daten der Seite.
    Deutlich verlaesslicher als Text raten: dort steht der Produktpreis,
    nicht der Versandpreis, und die Verfuegbarkeit ist eindeutig codiert."""
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            daten = json.loads(tag.string or "{}")
        except Exception:
            continue
        for eintrag in (daten if isinstance(daten, list) else [daten]):
            if not isinstance(eintrag, dict):
                continue
            if eintrag.get("@type") not in ("Product", "ProductGroup"):
                continue
            angebot = eintrag.get("offers") or {}
            if isinstance(angebot, list):
                angebot = angebot[0] if angebot else {}
            if not isinstance(angebot, dict):
                continue
            try:
                preis = float(angebot.get("price") or 0)
            except (TypeError, ValueError):
                preis = 0.0
            verf = str(angebot.get("availability", "")).lower()
            kaufbar = None
            if "outofstock" in verf or "soldout" in verf or "discontinued" in verf:
                kaufbar = False
            elif "instock" in verf or "preorder" in verf or "backorder" in verf:
                kaufbar = True
            if preis or kaufbar is not None:
                return kaufbar, preis
    return None, 0.0


def zustand(html: str):
    """Gibt (kaufbar, preis) zurück. kaufbar ist True, False oder None (unklar)."""
    if not html:
        return None, 0.0
    soup = BeautifulSoup(html, "html.parser")

    # Strukturdaten haben Vorrang
    k_struct, p_struct = aus_strukturdaten(soup)
    if k_struct is not None:
        return k_struct, p_struct
    for weg in soup(["script", "style", "noscript"]):
        weg.decompose()
    text = " ".join(soup.get_text(" ", strip=True).split()).lower()

    preis = 0.0
    treffer = []
    for m in PREIS_RE.finditer(text):
        ganz = m.group(1) or m.group(3)
        cent = m.group(2) or m.group(4)
        try:
            treffer.append(float(f"{ganz}.{cent}"))
        except ValueError:
            pass
    if p_struct:
        preis = p_struct
    elif treffer:
        # Kleinbetraege sind meist Versand oder Zubehoer, nicht das Produkt
        echte = [p for p in treffer if p >= 5.0]
        preis = min(echte) if echte else min(treffer)

    hat_nicht = any(w in text for w in NICHT_KAUFBAR)
    hat_ja = any(w in text for w in KAUFBAR)
    if hat_nicht and not hat_ja:
        return False, preis
    if hat_ja and not hat_nicht:
        return True, preis
    if hat_ja and hat_nicht:
        # Beides im Text: der Kaufen-Knopf entscheidet, wenn er aktiv ist
        knopf = soup.find(["button", "input"], string=re.compile("warenkorb|kaufen|cart", re.I))
        if knopf is not None and not knopf.has_attr("disabled"):
            return True, preis
        return False, preis
    return None, preis


def warenkorb_link(url: str, versuche: int = 3) -> str:
    """Baut fuer Shopify-Shops einen Link, der das Produkt direkt in den
    Warenkorb legt. Spart im Drop-Moment die Produktseite und das Klicken:
    du landest sofort bei der Zahlung, den Rest machst du selbst.
    Gibt '' zurueck, wenn der Shop das nicht unterstuetzt."""
    m = re.match(r"(https?://[^/]+)/products/([^/?#]+)", url)
    if not m:
        return ""
    basis, handle = m.group(1), m.group(2)
    varianten = []
    for versuch in range(versuche):
        try:
            r = requests.get(f"{basis}/products/{handle}.json", headers=HEADERS, timeout=8)
            if r.status_code == 200:
                varianten = r.json().get("product", {}).get("variants", [])
                break
            if r.status_code == 429:          # Shop bremst, kurz warten
                time.sleep(1.5 * (versuch + 1))
                continue
            return ""
        except Exception:
            time.sleep(0.8)
    if not varianten:
        return ""
    if not varianten:
        return ""
    # bevorzugt eine als verfuegbar markierte Variante
    passend = next((v for v in varianten if v.get("available")), varianten[0])
    vid = passend.get("id")
    return f"{basis}/cart/{vid}:1" if vid else ""


def lade() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def sichere(s: dict) -> None:
    STATE_FILE.write_text(json.dumps(s, ensure_ascii=False, sort_keys=True))


def melde(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[LOKAL]", text.replace("<b>", "").replace("</b>", ""))
        return True
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print("[WARN] Telegram:", e)
        return False


def bewerte(name: str, preis: float, uvp: float) -> str:
    if not preis:
        return "Preis auf der Seite prüfen"
    if not uvp:
        return f"{preis:.2f} €".replace(".", ",")
    if preis <= uvp * 1.05:
        return f"<b>{preis:.2f} € = UVP</b>, sofort zuschlagen".replace(".", ",")
    auf = (preis / uvp - 1) * 100
    return f"{preis:.2f} € (UVP {uvp:.2f} €, plus {auf:.0f}%)".replace(".", ",")


def main() -> int:
    from html import escape

    zustaende = lade()

    # Warenkorb-Links EINMAL beim Start aufloesen, nicht im Drop-Moment.
    # Im Alarmfall zaehlt jede Sekunde, da darf keine Extra-Abfrage mehr laufen.
    print("Warenkorb-Links vorbereiten ...")
    for eintrag in WATCHLIST:
        eintrag["korb"] = warenkorb_link(eintrag["url"])
        kennung = "Warenkorb-Link bereit" if eintrag["korb"] else "nur Produktseite"
        print(f"  {kennung:22} {eintrag['name'][:44]}")
        time.sleep(0.6)

    print(f"\nSniper laeuft: {len(WATCHLIST)} Seiten, alle ~{INTERVALL:.0f} s. Abbruch mit Strg+C.")
    runde = 0
    try:
        while True:
            runde += 1
            for eintrag in WATCHLIST:
                url = eintrag["url"]
                schluessel = hashlib.sha1(url.encode()).hexdigest()[:12]
                html = hole(url)
                kaufbar, preis = zustand(html)
                if kaufbar is None:
                    continue
                vorher = zustaende.get(schluessel, {}).get("kaufbar")
                zustaende[schluessel] = {"kaufbar": kaufbar, "preis": preis,
                                         "name": eintrag["name"]}
                # Alarm nur beim Wechsel auf kaufbar
                if kaufbar and vorher is False:
                    korb = eintrag.get("korb") or ""
                    zeilen = [
                        "🚨🚨 <b>JETZT KAUFBAR</b>",
                        escape(eintrag["name"]),
                        bewerte(eintrag["name"], preis, eintrag.get("uvp", 0)),
                    ]
                    if korb:
                        zeilen.append(
                            f"🛒 <a href=\"{escape(korb, quote=True)}\">IN DEN WARENKORB</a>"
                            f"  ·  <a href=\"{escape(url, quote=True)}\">Produktseite</a>"
                        )
                    else:
                        zeilen.append(f"<a href=\"{escape(url, quote=True)}\">DIREKT ZUM PRODUKT</a>")
                    text = "\n".join(zeilen)
                    if melde(text):
                        print(f"  ALARM: {eintrag['name']}")
                elif vorher is None:
                    print(f"  Start: {eintrag['name']} -> {'kaufbar' if kaufbar else 'nicht kaufbar'}")
                # kleine Pause zwischen den Seiten, damit kein Shop geflutet wird
                time.sleep(max(0.5, INTERVALL / max(len(WATCHLIST), 1) * 0.5))
            sichere(zustaende)
            if runde % 20 == 0:
                aktiv = sum(1 for v in zustaende.values() if v.get("kaufbar"))
                print(f"  [{time.strftime('%H:%M:%S')}] Runde {runde}, {aktiv} kaufbar")
            time.sleep(INTERVALL * random.uniform(0.8, 1.2))
    except KeyboardInterrupt:
        sichere(zustaende)
        print("\nSniper gestoppt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
