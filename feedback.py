#!/usr/bin/env python3
"""Holt die Daumen-hoch/runter-Klicks aus Telegram ab und schreibt sie mit.

Warum ueberhaupt: der Monitor haengt seit 01.09.2026 unter jeden Treffer zwei
Knoepfe. Geklickt wird im Telegram-Client, aber die Klicks liegen danach auf
Telegrams Server und muessen abgeholt werden. Genau das macht diese Datei.

WICHTIG, sonst geht Feedback verloren: getUpdates ist ein Postfach, das beim
Lesen geleert wird. Es darf deshalb IMMER NUR EINE Stelle pollen. Hier ist das
die Cloud (Workflow feedback.yml). Der lokale Mac ruft diese Datei nicht auf.

Bewertet wird gesammelt, nicht automatisch gelernt. Aus einem Daumen runter
selbsttaetig neue Sperrwoerter abzuleiten ist der gefaehrliche Teil: ein
falsches Wort ("box", "kollektion") wuerde stumm echte Treffer wegfiltern.
Die Auswertung laeuft deshalb ueber `--bericht` und die Entscheidung von Hand.
"""

import json
import os
import sys
import time
from pathlib import Path

import requests

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = str(os.environ.get("TG_CHAT_ID", ""))

GEMELDET_FILE = Path(os.environ.get("GEMELDET_FILE", "gemeldet.json"))
BEWERTUNGEN_FILE = Path(os.environ.get("BEWERTUNGEN_FILE", "bewertungen.json"))
OFFSET_FILE = Path(os.environ.get("FEEDBACK_STATE_FILE", "feedback_state.json"))

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def _lade(pfad: Path, leer):
    if pfad.exists():
        try:
            return json.loads(pfad.read_text())
        except Exception:
            return leer
    return leer


def _antworte(callback_id: str, text: str) -> None:
    """Stoppt das Wartekreisel im Client und zeigt eine kurze Bestaetigung."""
    try:
        requests.post(
            f"{API}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] answerCallbackQuery fehlgeschlagen: {e}")


def _sende(text: str) -> None:
    try:
        r = requests.post(
            f"{API}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        print(f"[Telegram] sendMessage HTTP {r.status_code}")
    except Exception as e:
        print(f"[WARN] sendMessage fehlgeschlagen: {e}")


def aus_nachricht(msg: dict, nummer: str) -> dict:
    """Notfall-Zuordnung, wenn der Fingerabdruck nicht im Nachschlagewerk steht.

    Passiert planmaessig bei den vier Shops, die auf dem Mac laufen: deren
    gemeldet.json liegt lokal, gepollt wird aber in der Cloud. Der Text der
    Nachricht steckt im Callback mit drin, und jeder Treffer traegt vorne
    seine Nummer, also lesen wir Shop und Titel einfach dort ab.
    """
    if not nummer:
        return {}
    zeilen = (msg.get("text") or "").splitlines()
    for i, z in enumerate(zeilen):
        if z.startswith(f"[{nummer}]"):
            kopf = z.split("]", 1)[1].strip()
            shop = "?"
            for stueck in kopf.split("·"):
                stueck = stueck.strip()
                if stueck and not stueck.endswith("€") and stueck != "LINK":
                    shop = stueck
            titel = zeilen[i + 1].strip() if i + 1 < len(zeilen) else ""
            return {"shop": shop, "titel": titel, "url": ""}
    return {}


def bericht(bewertungen: list) -> str:
    if not bewertungen:
        return "Noch keine Bewertungen da."
    gut = [b for b in bewertungen if b["urteil"] == "gut"]
    schlecht = [b for b in bewertungen if b["urteil"] == "schlecht"]
    zeilen = [f"<b>Bewertungen</b>: {len(gut)}x 👍 / {len(schlecht)}x 👎", ""]
    if schlecht:
        zeilen.append("<b>Zuletzt als Rausch markiert:</b>")
        for b in schlecht[-10:]:
            zeilen.append(f"· {b.get('shop', '?')}: {b.get('titel', '?')[:70]}")
    return "\n".join(zeilen)


def main() -> int:
    if not BOT_TOKEN or not CHAT_ID:
        print("[FEHLER] TG_BOT_TOKEN oder TG_CHAT_ID fehlt.")
        return 1

    bewertungen = _lade(BEWERTUNGEN_FILE, [])
    if not isinstance(bewertungen, list):
        bewertungen = []

    if "--bericht" in sys.argv:
        _sende(bericht(bewertungen))
        return 0

    gemeldet = _lade(GEMELDET_FILE, {})
    offset = _lade(OFFSET_FILE, {}).get("offset", 0)

    try:
        r = requests.get(
            f"{API}/getUpdates",
            params={
                "offset": offset,
                "timeout": 0,
                "allowed_updates": json.dumps(["callback_query", "message"]),
            },
            timeout=25,
        )
        daten = r.json()
    except Exception as e:
        print(f"[FEHLER] getUpdates: {e}")
        return 1

    if not daten.get("ok"):
        # Absichtlich ohne den Antwortkoerper: darin steht der Chat-Name.
        print("[FEHLER] Telegram hat getUpdates abgelehnt.")
        return 1

    updates = daten.get("result", [])
    print(f"[Feedback] {len(updates)} Updates abgeholt (offset {offset})")
    neu = 0
    hoechste = offset

    for u in updates:
        hoechste = max(hoechste, u.get("update_id", 0) + 1)

        cb = u.get("callback_query")
        if cb:
            # Nur der eigene Chat darf bewerten. Der Bot ist zwar privat, aber
            # jeder, der den Bot-Namen kennt, kann ihn anschreiben.
            von_chat = str(((cb.get("message") or {}).get("chat") or {}).get("id", ""))
            if von_chat != CHAT_ID:
                _antworte(cb.get("id", ""), "Nicht dein Bot.")
                continue
            roh = cb.get("data", "")
            teile = roh.split(":")
            art = teile[0] if teile else ""
            fp = teile[1] if len(teile) > 1 else ""
            nummer = teile[2] if len(teile) > 2 else ""
            if art not in ("g", "s") or not fp:
                _antworte(cb.get("id", ""), "Unbekannter Knopf.")
                continue
            info = gemeldet.get(fp) or aus_nachricht(cb.get("message") or {}, nummer)
            urteil = "gut" if art == "g" else "schlecht"
            bewertungen.append({
                "fp": fp,
                "urteil": urteil,
                "shop": info.get("shop", "?"),
                "titel": info.get("titel", "(nicht mehr im Nachschlagewerk)"),
                "url": info.get("url", ""),
                "wann": time.strftime("%Y-%m-%d %H:%M"),
            })
            neu += 1
            kurz = info.get("titel", "")[:40] or fp
            _antworte(cb.get("id", ""), f"{'👍' if urteil == 'gut' else '👎'} notiert: {kurz}")
            continue

        msg = u.get("message") or {}
        if str((msg.get("chat") or {}).get("id", "")) != CHAT_ID:
            continue
        text = (msg.get("text") or "").strip().lower()
        if text.startswith("/bericht"):
            _sende(bericht(bewertungen))

    if neu:
        BEWERTUNGEN_FILE.write_text(
            json.dumps(bewertungen, ensure_ascii=False, indent=1)
        )
        print(f"[Feedback] {neu} neue Bewertungen gespeichert")

    if hoechste != offset:
        OFFSET_FILE.write_text(json.dumps({"offset": hoechste}))

    return 0


if __name__ == "__main__":
    sys.exit(main())
