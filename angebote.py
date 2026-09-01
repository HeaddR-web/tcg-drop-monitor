#!/usr/bin/env python3
"""
Angebots-Radar: sucht auf der ANGEBOTSSEITE nach unterbewerteter Ware.

Der Gegenentwurf zum Neuheiten-Radar. Statt limitierte Neuware im Handel zu jagen
(wo alle Reseller gleichzeitig stehen), sucht dieses Skript bei Privatverkäufern
nach Ware unter Marktwert. Grundlage: Same Page Meeting vom 21.07.2026, beide
Reviewer unabhängig: "der echte Hebel ist der Einkauf unter Marktwert".

Rechnet NETTO, nicht brutto: Verkaufserlös minus Plattformgebühr minus Versand
minus Einkauf. Alarm nur, wenn Euro-Marge UND Rendite gleichzeitig passen.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
STATE_FILE = Path(os.environ.get("STATE_FILE", "angebote_state.json"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
}

# --- Wirtschaftlichkeit -------------------------------------------------------
GEBUEHR = 0.12          # eBay-Verkaufsprovision, grob
VERSAND_STD = 7.0       # Paketversand, Durchschnitt
MIN_MARGE_EUR = 30.0    # unter 30 EUR Reingewinn lohnt der Aufwand nicht
MIN_RENDITE = 0.35      # mindestens 35 % auf den Einkauf

# --- Plausibilität ------------------------------------------------------------
# Ohne diese Filter meldet der Radar Zubehör und rechnet mit dem Preis des
# Hauptgeräts (ein 15-EUR-Aufsatz "für Thermomix" sah aus wie +392 EUR Marge).
ZUBEHOER = [
    " für ", " fuer ", "zubehör", "zubehoer", "aufsatz", "ersatzteil", "ersatz-",
    "reparatur", "service", "hülle", "huelle", "tasche", "adapter", "halter",
    "deckel", "kabel", "anleitung", "kochbuch", "rezept", "aufkleber", "folie",
    "schutz", "messer für", "akku für", "ladegerät für", "koffer",
]
GESUCH = ["suche ", "gesucht", "suchen ", "tausche", "biete an gegen"]

MIN_EK_QUOTE = 0.25     # Einkauf unter 25 % des VK: fast immer Zubehör, nicht das Gerät
MAX_RENDITE = 3.0       # über 300 %: unplausibel, fast immer ein Fehl-Match


def plausibel(titel: str, ek: float, vk: float) -> bool:
    t = " " + titel.lower() + " "
    if any(w in t for w in ZUBEHOER):
        return False
    if any(t.startswith(" " + w) or (" " + w) in t for w in GESUCH):
        return False
    if ek < vk * MIN_EK_QUOTE:
        return False
    return True

# --- Watchlist ----------------------------------------------------------------
# vk = realistischer Verkaufspreis (konservativ!), max_ek = darüber nie kaufen.
# Wird nach der Nischen-Recherche gefüllt/geschärft. Jeder Eintrag ist eine These,
# die sich an echten Verkäufen beweisen muss.
WATCHLIST = [
    # LEER seit 23.08.2026: gewuenscht: das ganze Projekt nur noch auf Pokemon.
    # Die frueheren Eintraege (Toner, Server-RAM, Objektive, Synology, Garmin)
    # sind ersatzlos raus, sie hatten mit Pokemon nichts zu tun.
    #
    # Wieder befuellen NUR mit Pokemon-Suchen, und erst dann, wenn "vk" durch
    # einen echten Beleg gedeckt ist (Cardmarket-Trend oder verkaufte eBay-
    # Angebote), nicht geschaetzt. Sonst rechnet der Radar Margen auf Fantasie.
    # Beispiel-Zeile fuer spaeter:
    # {"suche": "pokemon display 30 jahre", "vk": 0, "max_ek": 0, "versand": 7},
]

# Ausdrücklich NICHT aufnehmen (Recherche 21.07.2026, mit Begründung):
# Makita/Bosch-Akkus einzeln (Gebrauchtpreis über Neupreis, Gefahrgut UN3480),
# Festool (Sperrgut, Marge unter Neupreis), Hilti (Diebesgut Nr. 1, §935 BGB),
# Jura/Siemens-Vollautomaten (2-3 Tage Testlauf, Brühgruppe stirbt in der
# Gewährleistung), Kinderwagen und Autositze (GPSR, Unfallhistorie, Haftung),
# saisonale Arbitrage (4-8 Monate totes Kapital), Retouren-Paletten (30-50%
# Ausschuss plus eigene Gewährleistung).

_only = os.environ.get("SUCHE_ONLY", "").strip()
if _only:
    WATCHLIST = [w for w in WATCHLIST if w["suche"] == _only]


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            return {k: v for k, v in data.items()} if isinstance(data, dict) else {k: 1 for k in data}
        except Exception:
            return {}
    return {}


def save_state(seen: dict) -> None:
    STATE_FILE.write_text(json.dumps(seen, ensure_ascii=False, sort_keys=True))


def _send(text: str) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[WARN] Telegram-Fehler {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"[WARN] Telegram nicht erreichbar: {e}")
        return False


def notify(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[WARN] Telegram-Credentials fehlen, Ausgabe nur lokal:")
        print(text)
        return True
    if len(text) > 3800:
        ok, chunk, size = True, [], 0
        for block in text.split("\n\n"):
            if size + len(block) > 3800 and chunk:
                ok = _send("\n\n".join(chunk)) and ok
                chunk, size = [], 0
            chunk.append(block)
            size += len(block) + 2
        if chunk:
            ok = _send("\n\n".join(chunk)) and ok
        return ok
    return _send(text)


def fetch(url: str) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200:
            return r.text
        print(f"[Kleinanzeigen] HTTP {r.status_code}")
    except Exception as e:
        print(f"[Kleinanzeigen] Netzwerkfehler: {e}")
    try:
        p = subprocess.run(
            ["curl", "-sSL", "--max-time", "25", "-A", HEADERS["User-Agent"], url],
            capture_output=True, text=True, timeout=30,
        )
        if p.returncode == 0 and p.stdout:
            return p.stdout
    except Exception:
        pass
    return ""


def netto_marge(ek: float, vk: float, versand: float) -> tuple:
    """Reingewinn nach Gebühr und Versand, plus Rendite auf den Einkauf."""
    erloes = vk * (1 - GEBUEHR) - versand
    marge = erloes - ek
    rendite = marge / ek if ek > 0 else 0
    return marge, rendite


def suche(eintrag: dict) -> list:
    """Findet Angebote unter der Kaufschwelle, die sich netto rechnen."""
    begriff = eintrag["suche"]
    url = "https://www.kleinanzeigen.de/s-" + quote(begriff.replace(" ", "-")) + "/k0"
    html = fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    treffer = []
    verworfen = 0
    for art in soup.select("article.aditem"):
        t_el = art.select_one(".text-module-begin")
        p_el = art.select_one(".aditem-main--middle--price-shipping--price")
        o_el = art.select_one(".aditem-main--top--left")
        href = art.get("data-href", "")
        if not t_el or not p_el or not href:
            continue
        titel = " ".join(t_el.get_text(" ", strip=True).split())
        preis_txt = p_el.get_text(strip=True)
        m = re.search(r"([\d.]+)\s*€", preis_txt)
        if not m:
            continue                      # "VB", "Zu verschenken", "Gesuch": ignorieren
        ek = float(m.group(1).replace(".", ""))
        if ek <= 0 or ek > eintrag["max_ek"]:
            continue
        if not plausibel(titel, ek, eintrag["vk"]):
            verworfen += 1
            continue
        marge, rendite = netto_marge(ek, eintrag["vk"], eintrag.get("versand", VERSAND_STD))
        if marge < MIN_MARGE_EUR or rendite < MIN_RENDITE:
            continue
        if rendite > MAX_RENDITE:
            verworfen += 1          # zu schön um wahr zu sein, fast sicher Fehl-Match
            continue
        ort = " ".join(o_el.get_text(" ", strip=True).split())[:40] if o_el else ""
        treffer.append({
            "titel": titel[:120], "ek": ek, "ort": ort,
            "url": "https://www.kleinanzeigen.de" + href,
            "marge": marge, "rendite": rendite, "vk": eintrag["vk"], "begriff": begriff,
        })
    if verworfen:
        print(f"[{begriff}] {verworfen} unplausible verworfen (Zubehör/Gesuch/zu gut)")
    return treffer


def fingerprint(url: str) -> str:
    return hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:16]


def main() -> int:
    from html import escape

    if not WATCHLIST:
        print("Angebots-Radar: keine Suchen konfiguriert (Pokemon-Watchlist leer) -> nichts zu tun")
        return 0

    seen = load_state()
    neu = []
    for eintrag in WATCHLIST:
        treffer = suche(eintrag)
        print(f"[{eintrag['suche']}] {len(treffer)} lohnende Angebote")
        for t in treffer:
            fp = fingerprint(t["url"])
            if fp in seen:
                continue
            t["fp"] = fp
            neu.append(t)
        time.sleep(2)

    if not neu:
        print("--> nichts Neues")
        save_state(seen)
        return 0

    neu.sort(key=lambda x: -x["marge"])
    lines = ["<b>Angebots-Radar: unterbewertete Ware</b>", ""]
    for t in neu:
        lines.append(
            f"💶 <b>+{t['marge']:.0f} € netto</b> ({t['rendite']*100:.0f}%) · "
            f"EK {t['ek']:.0f} € → VK ~{t['vk']:.0f} €\n"
            f"{escape(t['titel'])}\n"
            f"📍 {escape(t['ort'])} · <a href=\"{escape(t['url'], quote=True)}\">ANZEIGE</a>\n"
        )
    if notify("\n".join(lines)):
        for t in neu:
            seen[t["fp"]] = 1
        print(f"--> {len(neu)} Angebote gemeldet")
    else:
        print(f"--> Versand fehlgeschlagen, {len(neu)} bleiben offen")

    save_state(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
