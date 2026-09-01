#!/usr/bin/env python3
"""
Termin-Wecker: sagt rechtzeitig Bescheid, wann ein Drop ansteht.

Der Sniper nuetzt nur, wenn man weiss WANN man ihn anwerfen muss. Dieses Skript
laeuft einmal taeglich mit und meldet per Telegram:
  7 Tage vorher   Vorwarnung
  1 Tag vorher    Erinnerung plus Aufforderung, den Sniper zu starten
  am Tag selbst   Startsignal

Termine pflegen: einfach die Liste TERMINE unten erweitern.
"""

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

import requests

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
STATE_FILE = Path(os.environ.get("STATE_FILE", "termine_state.json"))

# --- Die Termine -------------------------------------------------------------
# datum: ISO. was: kurz. wo: wo man kaufen kann. warum: die Geld-Begruendung.
# sniper: True, wenn es sich lohnt, den Sniper vorher anzuwerfen.
TERMINE = [
    # --- Uhrzeit-Wecker: feuern am Tag selbst zur angegebenen Zeit ------------
    # (Der lokale Laeufer prueft alle 15 Min, deshalb sind die Zeiten grob.)
    {
        "datum": "2026-07-31",
        "uhrzeit": "08:00",
        "was": "Storm Emeralda (M6) erscheint heute in Japan",
        "wo": "chiefcards.de 124,99 | pokitrio.de 139,95 | goblincards 189,99",
        "warum": "Mega-Rayquaza-ex-Set. ACHTUNG: japanische Displays fallen laut "
                 "Marktbeobachtung typisch rund 30 Prozent von der Vorbestellung bis kurz nach Release. "
                 "Der Vorgaenger Nihil Zero (M3) startete hoch und liegt heute bei 52 bis 80 Euro.",
        "sniper": False,
        "tun": "HEUTE NICHT KAUFEN. Nur Preise notieren, als Startwert fuer den Vergleich.",
    },
    {
        "datum": "2026-07-31",
        "uhrzeit": "12:00",
        "was": "Storm Emeralda: Preis-Check Mittag",
        "wo": "gleiche Shops",
        "warum": "Am Release-Tag bewegen sich die Preise am staerksten.",
        "sniper": False,
        "tun": "Preise mit dem Morgenwert vergleichen. Faellt es schon?",
    },
    {
        "datum": "2026-07-31",
        "uhrzeit": "18:00",
        "was": "Storm Emeralda: Preis-Check Abend",
        "wo": "gleiche Shops",
        "warum": "Letzter Datenpunkt des Release-Tags.",
        "sniper": False,
        "tun": "Notieren. Kaufentscheidung fruehestens in 1 bis 2 Wochen, wenn der Abfall sichtbar ist.",
    },
    {
        "datum": "2026-08-14",
        "was": "Storm Emeralda: jetzt erst den Kauf pruefen",
        "wo": "chiefcards, pokitrio, cardcosmos, card-corner",
        "warum": "Zwei Wochen nach Release ist der Vorbestell-Aufschlag meist weg. "
                 "Erst hier lohnt der Vergleich mit dem Cardmarket-Wert.",
        "sniper": False,
        "tun": "Preis gegen Cardmarket pruefen. Nur kaufen wenn deutlich unter Marktwert.",
    },
    {
        "datum": "2026-08-03",
        "was": "Steiff Pikachu geht an den Fachhandel",
        "wo": "Steiff-Fachhaendler, Teddy- und Sammlershops, Warenhaeuser",
        "warum": "350 EUR Retail, rund 1.646 der 1.996 Stueck kommen erst jetzt in den Handel. "
                 "eBay-Angebote lagen nach dem Erstverkauf bei 1.900 bis 3.000 EUR.",
        "sniper": False,
        "tun": "Vorher bei Steiff-Haendlern anrufen und vormerken lassen.",
    },
    {
        # Betriebs-Termin, kein Kauf-Termin. Steht hier, weil termine.py der
        # einzige Kanal ist, der von selbst an ein Datum erinnert.
        "datum": "2026-09-10",
        "was": "GitHub Actions wieder einschalten (Zweitkanal fuer den Release)",
        "wo": "github.com/HeaddR-web/Pok-mon-CatchR -> Actions",
        "warum": "Die Workflows sind seit 25.08.2026 abgeschaltet, weil die Freiminuten "
                 "leer waren und jeder Lauf nur noch Fehlermails erzeugt hat. Seit dem "
                 "01.09. gibt es wieder 2000 Minuten. Bei der alten Taktung reichen die "
                 "rund 145 Minuten pro Tag fuer etwa 13 Tage, also vom 10. bis rund zum "
                 "20.09. Genau die Spanne deckt den Hauptrelease am 16.09. ab. "
                 "Frueher einschalten heisst, dass die Minuten vor dem Release leer sind.",
        "sniper": False,
        "tun": "Alle drei Workflows aktivieren: gh workflow enable 'Pokemon 30 Jahre Monitor' "
               "und dasselbe fuer 'Drop-Radar' und 'Raffle-Schnellpass'. Der Mac laeuft "
               "weiter, GitHub ist ab dann der zweite Kanal, falls der Mac schlaeft.",
    },
    {
        "datum": "2026-09-16",
        "was": "Pokemon 30 Jahre Hauptrelease (weltweit gleichzeitig)",
        "wo": "alle Haendler: Mueller, MediaMarkt, Card Corner, GeeksHeaven, Feenturm, TCG-Trade",
        "warum": "Elite Trainer Box UVP 54,99, Marktwert aktuell rund 159 EUR. "
                 "Booster Bundle UVP 31,99, Marktwert rund 106 EUR.",
        "sniper": True,
        "tun": "Sniper am Vorabend starten und durchlaufen lassen.",
    },
    {
        "datum": "2026-10-02",
        "was": "30 Jahre, zweite Produktwelle",
        "wo": "Fachhaendler",
        "warum": "Nachschub-Welle, oft die realistischere Chance auf UVP-Ware als der Erstrelease.",
        "sniper": True,
        "tun": "Sniper starten.",
    },
    {
        "datum": "2026-10-30",
        "was": "30 Jahre, dritte Produktwelle",
        "wo": "Fachhaendler",
        "warum": "weitere Nachschub-Welle",
        "sniper": True,
        "tun": "Sniper starten.",
    },
    {
        "datum": "2026-11-06",
        "was": "30 Jahre Ultra-Premium-Kollektion (Umbreon und Espeon)",
        "wo": "Fachhaendler, Vorbestellung teils schon gelaufen",
        "warum": "DAS Produkt der Serie: UVP 129,99, Marktwert aktuell 545 bis 549 EUR. "
                 "Historisch das Produkt mit der staerksten Wertentwicklung.",
        "sniper": True,
        "tun": "Sniper unbedingt starten, hier liegt die groesste Spanne.",
    },
    {
        "datum": "2026-12-04",
        "was": "30 Jahre, letzte Produktwelle",
        "wo": "Fachhaendler",
        "warum": "letzte Welle vor Weihnachten, danach wird es teuer",
        "sniper": True,
        "tun": "Sniper starten.",
    },
]

VORLAUF = [7, 1, 0]     # an diesen Tagen vorher wird gemeldet


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
        print(text.replace("<b>", "").replace("</b>", ""))
        return True
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=15,
        )
        if r.status_code != 200:
            print(f"[WARN] Telegram {r.status_code}: {r.text[:120]}")
            return False
        return True
    except Exception as e:
        print("[WARN] Telegram:", e)
        return False


def main() -> int:
    from html import escape

    gemeldet = lade()
    heute = date.today()
    offen = []

    jetzt = datetime.now()
    for t in TERMINE:
        try:
            tag = datetime.strptime(t["datum"], "%Y-%m-%d").date()
        except ValueError:
            continue
        rest = (tag - heute).days

        # Uhrzeit-Wecker: feuert am Tag selbst, sobald die Zeit erreicht ist
        if t.get("uhrzeit"):
            if rest != 0:
                continue
            try:
                std, minute = [int(x) for x in t["uhrzeit"].split(":")]
            except ValueError:
                continue
            if (jetzt.hour, jetzt.minute) < (std, minute):
                continue                      # noch zu frueh
            schluessel = f"{t['datum']}|{t['uhrzeit']}"
        else:
            if rest not in VORLAUF:
                continue
            schluessel = f"{t['datum']}|{rest}"

        if schluessel in gemeldet:
            continue

        if t.get("uhrzeit"):
            kopf = f"⏰ <b>{t['uhrzeit']} Uhr</b>"
        elif rest == 0:
            kopf = "🔔🔔 <b>HEUTE</b>"
        elif rest == 1:
            kopf = "🔔 <b>MORGEN</b>"
        else:
            kopf = f"📅 <b>In {rest} Tagen</b>"

        zeilen = [
            f"{kopf}: {escape(t['was'])}",
            "",
            f"📍 {escape(t['wo'])}",
            f"💰 {escape(t['warum'])}",
            f"👉 {escape(t['tun'])}",
        ]
        if t.get("sniper") and rest <= 1:
            zeilen.append("")
            zeilen.append("⚡ <b>Sniper starten</b> (Doppelklick auf SNIPER STARTEN.command)")
        offen.append((schluessel, "\n".join(zeilen)))

    if not offen:
        naechster = min(
            ((datetime.strptime(t["datum"], "%Y-%m-%d").date() - heute).days, t["was"])
            for t in TERMINE
            if (datetime.strptime(t["datum"], "%Y-%m-%d").date() - heute).days >= 0
        ) if any((datetime.strptime(t["datum"], "%Y-%m-%d").date() - heute).days >= 0 for t in TERMINE) else None
        if naechster:
            print(f"--> nichts faellig, naechster Termin in {naechster[0]} Tagen: {naechster[1]}")
        else:
            print("--> keine Termine mehr in der Liste")
        return 0

    for schluessel, text in offen:
        if melde(text):
            gemeldet[schluessel] = True
            print(f"--> gemeldet: {schluessel}")
    sichere(gemeldet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
