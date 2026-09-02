#!/usr/bin/env python3
"""
Drop-Radar: Wächter für limitierte POKEMON-Releases mit Reselling-Potenzial.
Meldet per Telegram neue Pokemon-Listungen und Pokemon-News, die nach
"limitiert" aussehen, mit Preis und LINK.

NUR POKEMON (Entscheidung 23.08.2026). Whisky, Rum, LEGO, Vinyl, Merch und
fremde Kartenspiele sind raus, samt ihrer Quellen und Bewertungslogik.

Kein Auto-Buy. Die Kauf-Einschätzung (Marktwert, Flip vs. Hold) macht der Mensch
mit Claude pro Alert, das Radar liefert die Frühwarnung.
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")
STATE_FILE = Path(os.environ.get("STATE_FILE", "drops_state.json"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
}

# Woerter, die auf ein limitiertes/flipbares Release hindeuten (kategorie-uebergreifend)
HOT_WORDS = [
    "limited", "limitiert", "limitierte",
    "edition", "sonderedition", "jubiläum", "jubilaeum", "anniversary",
    "exklusiv", "exclusive", "collab", "collaboration",
    "vorbestell", "pre-order", "preorder",
    "release", "drop",
]

# Marken/Serien mit belegter Sekundaermarkt-Historie: immer melden, auch ohne Hot-Word
PRIO_BRANDS = [
    # Pokemon-Serien und Produktlinien mit belegter Sekundaermarkt-Historie:
    # immer melden, auch ohne Hot-Word im Titel.
    "30th celebration", "30 jahre", "ultra premium",
    "erste partner", "first partner", "illustrations-kollektion",
    "prismatic", "erhabene helden", "fatale flammen",
]
# Seit 02.09.2026 kommen die laufenden Sets aus zielsets.txt (eine Datei fuer
# Monitor und Drop-Radar, von Hand gepflegt).
from zielsets import lade_zielsets
PRIO_BRANDS += [z for z in lade_zielsets() if z not in PRIO_BRANDS]

# Ausschluss: Standard-Sortiment-Rauschen
# Zubehoer und Kleinkram, der nie ein Drop ist. Bis 26.08.2026 standen hier
# noch die Reste aus der Whisky- und Rum-Zeit (5cl, Tumbler, Glaeser), also
# Woerter, die in einem Pokemon-Titel nie vorkommen. Der Filter war damit
# praktisch wirkungslos. Jetzt spiegelt er die Liste aus monitor.py.
# Die Sperre gilt NICHT fuer die 30-Jahre-Linie: dort heissen echte
# Sammelprodukte "Ordner-Kollektion" und "Poster-Kollektion" und enthalten Karten.
EXCLUDE = [
    "einzelkarte", "sleeve", "hülle", "huelle", "playmat", "spielmatte",
    "ordner", "binder", "album", "poster", "figur", "plüsch", "pluesch",
    "tasse", "becher", "schlüsselanhänger", "schluesselanhaenger",
    "t-shirt", "socken", "buch", "puzzle",
    # Nicht rettbar, gleicher Stand wie monitor.py (27.08.2026).
    "toploader", "top loader", "deckbox", "deck box", "sammelmappe",
    "kartenschutz", "aufbewahrung", "lunchbox", "brettspiel", "lego",
    "kuscheltier", "rucksack", "trinkflasche", "malbuch", "radiergummi",
]

PRICE_RE = re.compile(r"(\d{1,5})[.,](\d{2})\s*€|€\s*(\d{1,5})[.,](\d{2})")

# Signalwörter für News-Quellen: melden nur harte Limitierungen und Kollabs,
# nicht jede Spielenachricht. Der Steiff-Fall (350 Retail, Angebote ab 1900)
# wäre über "steiff", "numbered", "limited to" rund zwei Wochen früher gekommen.
KOLLAB_SIGNALE_EN = [
    "steiff", "rimowa", "first 4 figures", "first4figures", "wand company",
    "santa cruz", "maison de sabre", "balmain", "van gogh", "louvre",
    "collab", "collaboration", "kollab", "x pokemon", "x pokémon",
    "limited to", "numbered", "limited edition", "lottery", "raffle",
    "抽選", "exclusive", "pre-order", "preorder", "restock", "drops",
    "ichiban kuji", "pokemon center exclusive", "pokémon center exclusive",
    "sold out", "auflage", "limitiert", "sammlerfigur", "collector",
]

SOURCES = [
    {
        # Shopify-Vorbestell-Collection: alle kommenden TCG-Displays an einem Ort
        "name": "cardcosmos",
        "category": "TCG",
        # Ueber die Shopify-JSON statt HTML: liefert Preis UND Verfuegbarkeit
        # mit. Aus dem HTML kam beides nicht, deshalb stand ueberall "Preis?".
        "urls": ["https://cardcosmos.de/collections/tcg-vorbestellung/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://cardcosmos.de",
    },
    {
        # Merch-Fokus, Pokémon-Center-Kalender, meldet Sammlerstücke früh
        "name": "PokeShopper",
        "category": "News",
        "urls": ["https://pokeshopper.net/news"],
        "item": "a[href*='/20']",
        "base": "https://pokeshopper.net",
        "hot": KOLLAB_SIGNALE_EN,
    },
    {
        # Japan-Ankündigungen: dort starten fast alle harten Limitierungen
        "name": "PocketMonsters",
        "category": "News",
        "urls": ["https://pocketmonsters.net/news"],
        "item": "a[href*='/news/']",
        "base": "https://pocketmonsters.net",
        "hot": KOLLAB_SIGNALE_EN,
    },
    {
        # Seit 25 Jahren oft als Erstes bei offiziellen Ankündigungen
        "name": "Serebii",
        "category": "News",
        "urls": ["https://www.serebii.net/index2.shtml"],
        "item": "h2 a[href]",
        "base": "https://www.serebii.net",
        "hot": KOLLAB_SIGNALE_EN,
    },
    {
        # Kollab-Frühwarnung: News-Artikel über neue Pokémon-Produkte/Kooperationen.
        # Hätte den Steiff-Pikachu-Release (Retail 350, Resell 1400) gemeldet.
        "name": "PokeZentrum News",
        "category": "News",
        "urls": ["https://pokezentrum.de/pokemon-karten-news/"],
        "item": "a[href*='/pokemon-karten-news/']",
        "base": "https://pokezentrum.de",
        "hot": [
            "steiff", "kooperation", "kollab", "collab", "zusammenarbeit",
            "sammelfigur", "plüsch", "pluesch", "figur", "van gogh", "louvre",
            "limitiert", "exklusiv", "vorbestell", "vorverkauf", "restock",
            "erhabene helden", "fatale flammen", "prismatic", "30 jahre",
            "jubiläum", "jubilaeum", "enthüllt", "enthuellt", "angekündigt",
            "angekuendigt", "release",
        ],
    },
    {
        # Steiff-Fachhaendler: hier landen ab 03.08.2026 die restlichen rund
        # 1.646 Pikachus. Steiff nennt die Partner nicht, deshalb ueberwachen
        # wir die groessten Steiff-Haendler direkt auf Pokemon-Ware.
        "name": "Teddys Rothenburg",
        "category": "Collab",
        "urls": [
            "https://teddys-rothenburg.de/search/?qs=pikachu",
            "https://teddys-rothenburg.de/search/?qs=pokemon",
        ],
        "item": "a.niu-itembox-link",
        "base": "",
        "titel_aus_url": True,
        "hot": ["pikachu", "pokemon", "pokémon"],
    },
    {
        "name": "SammlerKontor",
        "category": "Collab",
        "urls": [
            "https://www.sammlerkontor.de/search?sSearch=pikachu",
            "https://www.sammlerkontor.de/search?sSearch=pokemon",
        ],
        "item": "a.product--title",
        "base": "",
        "hot": ["pikachu", "pokemon", "pokémon"],
    },
    {
        # Steiff direkt: Neuheiten-Seite, nur Pokémon-Artikel melden
        "name": "Steiff",
        "category": "Collab",
        "urls": ["https://www.steiff.com/de-de/neuheiten/"],
        "item": "a[title]",
        "base": "https://www.steiff.com",
        "hot": ["pokemon", "pokémon", "pikachu", "glumanda", "bisasam",
                "schiggy", "evoli", "relaxo", "charmander", "eevee", "snorlax"],
    },
]

# --- Pokemon-Fokus -----------------------------------------------------------
# gewuenscht: ausschliesslich Pokemon (Ansage 23.08.2026). Es gibt keinen
# Schalter mehr zurueck auf andere Kategorien: Whisky, Rum, LEGO, Vinyl, Merch
# und fremde Kartenspiele sind samt Quellen und Bewertungslogik entfernt.
FOKUS = "pokemon"
NUR_TCG = True
TCG_KATEGORIEN = {"TCG", "News"}
NICHT_TCG_QUELLEN = set()

# Ein News-Artikel zaehlt im TCG-Fokus nur, wenn er auch nach Karten klingt.
# Sonst melden Serebii und PocketMonsters jede Spiele- und Anime-Nachricht.
TCG_WOERTER = [
    "tcg", "karte", "karten", "card", "cards", "booster", "display",
    "elite trainer", "etb", "collection box", "kollektion", "collection",
    "premium", "tin", "deck", "pack", "set ",
    "illustration", "promo", "expansion", "erweiterung", "30 jahre",
    "30th", "jubilaeum", "jubiläum", "anniversary",
]

# Woerter, an denen ein Titel als Pokemon-Ware erkennbar ist. Fremde Karten-
# spiele (Magic, Star Wars Unlimited, One Piece) fallen damit raus, ohne dass
# ich sie einzeln sperren muss. Die Serien-Namen stehen mit drin, weil manche
# Shops "Prismatic Evolutions Display" ohne das Wort Pokemon schreiben.
POKEMON_WOERTER = [
    "pokemon", "pokémon", "pokeball", "pikachu", "evoli", "eevee",
    "glurak", "charizard", "relaxo", "snorlax", "mewtu", "mewtwo",
    "30 jahre", "30th celebration", "erste partner", "first partner",
    "prismatic", "erhabene helden", "fatale flammen", "karmesin", "purpur",
    "scarlet", "violet", "sonne", "mond", "schwert", "schild",
]


def ist_pokemon(titel: str) -> bool:
    t = titel.lower()
    return any(w in t for w in POKEMON_WOERTER)


# Die 30-Jahre-Linie ist das Ziel des ganzen Projekts (Release 16.09.2026) und
# steht seit dem 23.08.2026 in jedem Alert ganz oben, vor allem anderen.
JUBILAEUM_WOERTER = [
    "30 jahre", "30-jahre", "30th celebration", "30th anniversary",
    "30 years", "jubilaeum", "jubiläum", "erste partner", "first partner",
    "illustrations-kollektion", "illustration collection",
]


# --- Sprach-Regel (Ansage 26.08.2026) -------------------------------
# Japanisch ist ausdruecklich erwuenscht, genauso Deutsch und Englisch.
# Chinesische Ware will sie NICHT: der Sammlermarkt dafuer ist hier duenn und
# die Wiederverkaufspreise liegen deutlich unter den JP- und EN-Fassungen.
# Wortgrenzen sind Absicht: ein reines "in enthalten" wuerde bei Namen wie
# "Machin..." oder "Cinccino" falsch anschlagen.
CHINESISCH_MUSTER = re.compile(
    r"\b("
    r"chin(a|es(e|isch)?)"          # china, chinese, chinesisch
    r"|[st]-chin\w*"               # s-chinese / t-chinese (simplified/traditional)
    r"|cn"
    r")\b",
    re.IGNORECASE,
)
CHINESISCH_ZEICHEN = ("简体", "繁體", "繁体")


# --- Fremde Sammelkartenspiele (Ansage 27.08.2026: "ich will Pokemon") ---
# Haendler wie CardCosmos fuehren auch Yu-Gi-Oh, Lorcana und Riftbound. Die
# rutschten bisher ueber den Neuling-Verdacht durch: ein frisch angelegtes
# Produkt, das nach Sammelkarten aussieht, wird absichtlich auch ohne
# Pokemon-Wort gemeldet, damit ein getarnter Drop nicht durchfaellt. Der Preis
# dieser Regel war eine Yu-Gi-Oh-Flut.
# Steht ein fremdes Spiel NAMENTLICH im Titel, ist nichts getarnt und der
# Verdacht faellt weg. Unklare Namen bleiben weiterhin drin.
FREMDES_TCG_MUSTER = re.compile(
    r"\b("
    r"yu-?gi-?oh!?|yugioh|konami"
    r"|lorcana"
    r"|magic:? the gathering|\bmtg\b"
    r"|one piece"
    r"|digimon"
    r"|dragon ?ball"
    r"|weiss schwarz|weiß schwarz"
    r"|union arena"
    r"|cardfight|vanguard"
    r"|flesh and blood"
    r"|star wars"
    r"|gundam"
    r"|metazoo"
    r"|riftbound|league of legends"
    r"|grand archive"
    r"|shadowverse"
    r"|battle spirits"
    r"|akademiya|altered tcg"
    r")\b",
    re.IGNORECASE,
)


def ist_fremdes_tcg(titel: str) -> bool:
    """True, wenn im Titel ein anderes Sammelkartenspiel NAMENTLICH steht."""
    return bool(FREMDES_TCG_MUSTER.search(titel))


# --- Konvolut-Sperre (Ansage 26.08.2026) ----------------------------
# Privat zusammengewuerfelte Posten sind fuer das Flippen wertlos: Zustand
# unbekannt, Inhalt nicht pruefbar, Wiederverkauf muehsam. Sie rutschten bisher
# durch, weil die Kategorie-Wache absichtlich JEDEN neuen Artikel einer
# Pokemon-Kategorie meldet, egal wie er heisst. Genau diese Luecke schliesst
# die Sperre, ohne die Wache selbst abzuschalten.
# Wortgrenzen sind Absicht: "lot" wuerde sonst in "Pilot" oder "Lotad"
# anschlagen, "bulk" in Markennamen.
KONVOLUT_MUSTER = re.compile(
    r"\b("
    r"konvolut\w*"
    r"|sammlungsaufl(oe|ö)sung"
    r"|sammlung"
    r"|restsammlung"
    r"|lose\s+(karten|booster|packs?)"
    r"|bulk"
    r"|lot|lots"
    r"|repack|re-pack"
    r"|mystery[- ]?box"
    r"|wundert(ue|ü)te"
    r"|grabbelbox|gl(ue|ü)cksbox"
    r"|gebraucht\w*|bespielt\w*|gespielt\w*"
    r"|gemischte?s?"
    r")\b",
    re.IGNORECASE,
)


def ist_konvolut(titel: str) -> bool:
    """True bei zusammengewuerfelten Posten und Gebrauchtware."""
    return bool(KONVOLUT_MUSTER.search(titel))


def ist_chinesisch(titel: str) -> bool:
    """True, wenn der Titel auf eine chinesische Fassung hindeutet."""
    if any(z in titel for z in CHINESISCH_ZEICHEN):
        return True
    return bool(CHINESISCH_MUSTER.search(titel))


def ist_jubilaeum(titel: str) -> bool:
    return any(w in titel.lower() for w in JUBILAEUM_WOERTER)


if NUR_TCG:
    SOURCES = [
        s for s in SOURCES
        if s["category"] in TCG_KATEGORIEN and s["name"] not in NICHT_TCG_QUELLEN
    ]

_only = os.environ.get("SOURCES_ONLY", "").strip()
if _only:
    _wanted = {n.strip() for n in _only.split(",")}
    SOURCES = [s for s in SOURCES if s["name"] in _wanted]

_excl = os.environ.get("SOURCES_EXCLUDE", "").strip()
if _excl:
    _drop = {n.strip() for n in _excl.split(",")}
    SOURCES = [s for s in SOURCES if s["name"] not in _drop]


def load_state() -> set:
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text()))
        except Exception:
            return set()
    return set()


def save_state(seen: set) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False))


def _send(text: str, knoepfe: list = None) -> None:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                **({"reply_markup": {"inline_keyboard": knoepfe}} if knoepfe else {}),
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[WARN] Telegram-Fehler {r.status_code}: {r.text[:200]}")
    except Exception as e:
        print(f"[WARN] Telegram nicht erreichbar: {e}")


def notify(text: str, knoepfe: list = None) -> None:
    """knoepfe ist die Bewertungs-Tastatur. Sie haengt unter genau EINER
    Teilnachricht, und zwar der ersten: dort stehen die nummerierten Treffer."""
    if not BOT_TOKEN or not CHAT_ID:
        print("[WARN] Telegram-Credentials fehlen, Ausgabe nur lokal:")
        print(text)
        return
    if len(text) > 3800:
        chunk, size, erste = [], 0, True
        for block in text.split("\n\n"):
            if size + len(block) > 3800 and chunk:
                _send("\n\n".join(chunk), knoepfe if erste else None)
                chunk, size, erste = [], 0, False
            chunk.append(block)
            size += len(block) + 2
        if chunk:
            _send("\n\n".join(chunk), knoepfe if erste else None)
        return
    _send(text, knoepfe)


def fetch(url: str, name: str) -> str:
    status = None
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200:
            if "charset" not in r.headers.get("Content-Type", "").lower():
                r.encoding = r.apparent_encoding
            return r.text
        status = r.status_code
    except Exception as e:
        print(f"[{name}] Netzwerkfehler: {e}")
    try:
        p = subprocess.run(
            ["curl", "-sSL", "--max-time", "25",
             "-A", HEADERS["User-Agent"],
             "-H", f"Accept-Language: {HEADERS['Accept-Language']}",
             url],
            capture_output=True, text=True, timeout=30,
        )
        if p.returncode == 0 and p.stdout:
            return p.stdout
    except Exception:
        pass
    if status is not None:
        print(f"[{name}] HTTP {status} – uebersprungen")
    return ""


def is_hot(title: str, src: dict = None) -> bool:
    t = title.lower()
    # Sprache zuerst: chinesische Fassungen sind unerwuenscht (Ansage
    # 26.08.2026). News-Quellen sind ausgenommen, dort ist eine Meldung ueber
    # einen China-Release trotzdem eine Information wert.
    ist_news_quelle = bool(src) and src.get("category") == "News"
    if ist_chinesisch(title) and not ist_news_quelle:
        return False
    if ist_konvolut(title) and not ist_news_quelle:
        return False
    if ist_fremdes_tcg(title) and not ist_news_quelle:
        return False
    # Im TCG-Fokus muss eine News zusaetzlich nach Karten klingen. Ohne das
    # meldet Serebii jede Spiele- und Anime-Nachricht als "Drop".
    # Klingt sie nach Karten, ist sie ein Treffer, auch ohne Kollab-Signalwort:
    # "Erste Partner Illustrations-Kollektion" hat keins und war trotzdem DER Drop.
    # Harte Vorstufe im Pokemon-Fokus: was nicht nach Pokemon klingt, ist raus.
    # Betrifft vor allem den TCG-Shop, der auch Magic und Star Wars fuehrt.
    # Ausgenommen die News-Quellen: das sind reine Pokemon-Seiten, dort steht
    # das Wort oft gar nicht mehr im Titel ("Neue Displays angekuendigt").
    ist_news = bool(src) and src.get("category") == "News"
    if FOKUS == "pokemon" and not ist_news:
        if not ist_pokemon(t):
            return False
        # Umgekehrt: erkannte Pokemon-Ware ist im Pokemon-Fokus IMMER ein
        # Treffer. Sonst waere "Prismatic Evolutions Booster Bundle" durch-
        # gefallen, weil im Titel kein Wort wie "limitiert" steht.
        if not (any(x in t for x in EXCLUDE) and not ist_jubilaeum(t)):
            return True
    if NUR_TCG and src and src.get("category") == "News":
        if not any(w in t for w in TCG_WOERTER):
            return False
        return True
    # Quellen mit eigener Trefferliste (z.B. Steiff: nur Pokémon-Artikel melden,
    # News-Seiten: nur Kollab-/Release-Artikel) nutzen die statt der globalen Listen.
    if src and src.get("hot"):
        return any(w in t for w in src["hot"])
    if any(x in t for x in EXCLUDE) and not ist_jubilaeum(t):
        return False
    if any(b in t for b in PRIO_BRANDS):
        return True
    return any(w in t for w in HOT_WORDS)


def is_prio(title: str) -> bool:
    t = title.lower()
    return any(b in t for b in PRIO_BRANDS)


def extract_price(node) -> float:
    el = node
    for _ in range(4):
        if el is None:
            break
        text = el.get_text(" ", strip=True)
        prices = []
        for m in PRICE_RE.finditer(text):
            whole = m.group(1) or m.group(3)
            cents = m.group(2) or m.group(4)
            try:
                prices.append(float(f"{whole}.{cents}"))
            except ValueError:
                pass
        if prices:
            return min(prices)
        el = el.parent
    return 0.0


# --- Einschätzungs-Wissen: was ein Kenner am Titel abliest ---------------------
# Nur noch Pokemon. Die frueheren Bloecke zu geschlossenen Destillerien,
# Rum-Prestige-Abfuellern und LEGO-Sammlerthemen sind am 23.08.2026 entfernt.

# Story/Anlass, der Sammler triggert
STORY = ["james bond", "jubiläum", "jubilaeum", "anniversary", "100 jahre",
         "final", "last release", "commemorat"]


# Flip-Geschwindigkeit je Verdict: kleiner = schneller Kapital zurück.
# Emoji macht die Geschwindigkeit im Alert sofort sichtbar.
SPEED = {
    "SOFORT-FLIP": (0, "⚡"),
    "FLIP+HOLD": (1, "🔄"),
    "HOLD": (2, "📦"),
    "PRÜFEN": (3, "🔍"),
    "LANG-HOLD": (4, "🐢"),
    "SKIP": (5, "⛔"),
}


def assess(title: str, price: float, category: str):
    """Kenner-Einschätzung. Gibt (verdict, text) zurück; verdict steuert die Sortierung."""
    t = title.lower()
    signals = []
    hold = None
    margin = None
    verdict = None

    if category in ("TCG", "Pokémon"):
        if "sealed" in t or "display" in t or "case" in t or "box" in t:
            signals.append("versiegelt/Display (bester Wertträger)")
        if "collector booster" in t or "collector-booster" in t:
            signals.append("Collector Booster (schlägt Play/Draft)")
            hold, margin, verdict = "Flip-Wochen / Hold 2-3 J", "+20-80%", "FLIP+HOLD"
        if "ultra premium" in t or "upc" in t:
            signals.append("Ultra Premium (Top-Sammlerstück)")
            hold, margin, verdict = "1-3 J", "+30-120%", "HOLD"
        if any(s in t for s in ("top-trainer", "top trainer", "elite trainer", "etb")):
            # Kalibrierpunkt 02.09.2026: Mega-Entwicklung-ETB bei
            # MediaMarkt AT 60 Euro, Sekundaermarkt 120. Zum Retail-Preis ist
            # eine ETB eines laufenden Sets ein Sofort-Flip, kein Pruef-Fall.
            signals.append("Top-Trainer-Box zum Retail (Kalibrierpunkt: 60 → 120)")
            hold, margin, verdict = "Tage bis Wochen", "+50-100%", "SOFORT-FLIP"
        if "1st edition" in t or "1. edition" in t or "erstauflage" in t:
            signals.append("Erstauflage")
        if any(s in t for s in STORY) or "30 jahre" in t or "30th" in t:
            signals.append("Jubiläums-/Anlass-Set")
            hold = hold or "Flip am Drop / Hold 2-3 J"
            verdict = verdict or "FLIP+HOLD"

    if not verdict:
        verdict, hold, margin = "PRÜFEN", "?", "unklar → Resell-Links checken"
    if not signals:
        signals = ["keine starken Signale im Titel"]

    emoji = SPEED.get(verdict, ("", ""))[1]
    parts = [f"{emoji} <b>{verdict}</b>"]
    if hold:
        parts.append(f"Halten: {hold}")
    if margin:
        parts.append(f"Marge: {margin}")
    text = " · ".join(parts) + "\n   Warum: " + ", ".join(signals[:3])
    return verdict, text


def resell_links(title: str, category: str) -> str:
    """Baut HTML-Links zu Sekundaermarkt-Preisen fuer den Alert."""
    from urllib.parse import quote_plus
    from html import escape

    # Titel fuer die Suche eindampfen: Fuellwoerter und Klammern raus
    q = re.sub(r"\(.*?\)", " ", title)
    q = re.sub(r"\d+[.,]\d+\s*%", " ", q)
    q = " ".join(q.split()[:7])
    ebay = (
        "https://www.ebay.de/sch/i.html?LH_Sold=1&LH_Complete=1&_nkw="
        + quote_plus(q)
    )
    links = [f'<a href="{escape(ebay, quote=True)}">eBay-VK</a>']
    if category in ("TCG", "Pokémon"):
        cm = "https://www.cardmarket.com/de/Pokemon/Products/Search?searchString=" + quote_plus(q)
        links.append(f'<a href="{escape(cm, quote=True)}">Cardmarket</a>')
    return " · ".join(links)


def normalize_url(url: str) -> str:
    """Stabile Produkt-ID: Tracking-/Session-Parameter raus (sonst Duplikate)."""
    u = url.split("#")[0]
    m = re.search(r"/dp/([A-Z0-9]{10})", u)
    if m:
        return "amazon:" + m.group(1)
    u = u.split("?")[0]
    u = re.sub(r"/ref=[^/]*$", "", u)
    return u.rstrip("/")


def fingerprint(source: str, title: str, url: str) -> str:
    raw = f"{source}|{normalize_url(url)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def check_shopify(src: dict) -> list:
    """Shopify-Shops liefern ihren Katalog als JSON: Titel, Preis und
    Verfügbarkeit ohne HTML-Parsing. Ein Parser für beliebig viele Shops."""
    hits = []
    for url in src["urls"]:
        raw = fetch(url, src["name"])
        if not raw:
            continue
        try:
            produkte = json.loads(raw).get("products", [])
        except Exception as e:
            print(f"[{src['name']}] JSON unlesbar: {e}")
            continue
        for p in produkte:
            titel = " ".join(str(p.get("title", "")).split())
            if not titel or not is_hot(titel, src):
                continue
            varianten = p.get("variants") or []
            if not varianten:
                continue
            # nur lieferbare Ware ist eine Kaufgelegenheit
            lieferbar = [v for v in varianten if v.get("available")]
            if not lieferbar:
                continue
            try:
                preis = min(float(v.get("price") or 0) for v in lieferbar)
            except (ValueError, TypeError):
                preis = 0.0
            handle = p.get("handle", "")
            hits.append((titel[:180], f"{src['base']}/products/{handle}", preis))
        time.sleep(1)
    return hits


def check_source(src: dict) -> list:
    if src.get("parser") == "shopify":
        return check_shopify(src)
    hits = []
    for url in src["urls"]:
        html = fetch(url, src["name"])
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select(src["item"]):
            href = a.get("href", "")
            if src.get("titel_aus_url"):
                # Manche Shops verstecken Formularfelder im Produktlink, dann ist
                # der Linktext Muell. Der saubere Name steht in der URL.
                roh = re.sub(r"^https?://[^/]+/", "", href).split("?")[0].strip("/")
                title = roh.replace("-", " ").replace("_", " ").strip()
            else:
                title = " ".join(a.get_text(" ", strip=True).split())
            if not title or not href or len(title) < 10:
                continue
            if not is_hot(title, src):
                continue
            if href.startswith("/"):
                href = src["base"] + href
            price = extract_price(a)
            hits.append((title[:180], href, price))
        time.sleep(1)

    seen_local = set()
    unique = []
    for t, u, p in hits:
        if u in seen_local:
            continue
        seen_local.add(u)
        unique.append((t, u, p))
    return unique


def main() -> int:
    from html import escape

    seen = load_state()
    new_items = []

    for src in SOURCES:
        hits = check_source(src)
        print(f"[{src['name']}] {len(hits)} heisse Treffer")
        for title, url, price in hits:
            fp = fingerprint(src["name"], title, url)
            if fp in seen:
                continue
            seen.add(fp)
            new_items.append((src["category"], src["name"], title, url, price))
        time.sleep(2)

    if new_items:
        # Einschätzung vorziehen, dann nach Flip-Geschwindigkeit sortieren:
        # schnelles Kapital (SOFORT-FLIP) oben, langsames (LANG-HOLD) unten.
        scored = []
        unrentabel = 0
        for cat, shop, title, url, price in new_items:
            verdict, atext = assess(title, price, cat)
            rank = SPEED.get(verdict, (9, ""))[0]
            # Rechnet sich nach Gebühren und Versand nichts, ist es kein Alarm
            # wert. Ausnahme: Prio-Ware (30 Jahre, Top-Marken) immer melden,
            # dort ist der Drop selbst das Ereignis.
            try:
                import marktwert as _mw
                _game = _mw.guess_game(title) if cat in ("TCG", "Pokémon") else "keine"
                if "LOHNT NICHT" in _mw.bewertung(title, price, _game) and not is_prio(title):
                    unrentabel += 1
                    continue
            except Exception:
                pass
            scored.append((rank, cat, shop, title, url, price, atext))
        if unrentabel:
            print(f"--> {unrentabel} Treffer verworfen (rechnen sich netto nicht)")
        if not scored:
            print("--> nichts Rentables")
            save_state(seen)
            return 0
        # 30 Jahre zuerst, danach wie gehabt nach Flip-Geschwindigkeit.
        scored.sort(key=lambda x: (not ist_jubilaeum(x[3]), x[0], not is_prio(x[3]), x[1]))

        lines = ["<b>Drop-Radar – 30 Jahre zuerst, dann schnellste Flips</b>", ""]
        # Bewertungs-Knoepfe wie beim Monitor. Die Helfer liegen dort, damit
        # beide Melder in dasselbe Nachschlagewerk schreiben und feedback.py
        # nur eine Datei kennen muss.
        from monitor import MAX_BEWERTBAR, lade_gemeldet, speichere_gemeldet
        knoepfe = []
        gemeldet = lade_gemeldet()
        nummer = 0
        trenner_gesetzt = False

        def knopf(fp: str, shop: str, title: str, url: str) -> str:
            """Vergibt die naechste Nummer und haengt die Knoepfe an. Gibt die
            sichtbare Marke zurueck, leer wenn das Kontingent voll ist."""
            nonlocal nummer
            nummer += 1
            if nummer > MAX_BEWERTBAR:
                return ""
            knoepfe.append([
                {"text": f"👍 {nummer}", "callback_data": f"g:{fp}:{nummer}"},
                {"text": f"👎 {nummer}", "callback_data": f"s:{fp}:{nummer}"},
            ])
            gemeldet[fp] = {
                "shop": shop,
                "titel": title,
                "url": url,
                "wann": time.strftime("%Y-%m-%d %H:%M"),
            }
            return f"<code>[{nummer}]</code> "
        for rank, cat, shop, title, url, price, atext in scored:
            if not ist_jubilaeum(title) and not trenner_gesetzt:
                lines.append("———— sonstige Pokémon-Treffer ————\n")
                trenner_gesetzt = True
            if cat == "News":
                # Ankündigungen kompakt: Titel + Link reichen, Bewertung folgt
                # erst, wenn das Produkt bei einem Händler auftaucht.
                marke = "🎂 30 JAHRE · " if ist_jubilaeum(title) else ""
                marke = knopf(fingerprint(shop, title, url), shop, title, url) + marke
                lines.append(
                    f"{marke}📰 <b>{shop}</b> · <a href=\"{escape(url, quote=True)}\">ARTIKEL</a>\n"
                    f"{escape(title)}\n"
                )
                continue
            flag = "🎂 30 JAHRE · " if ist_jubilaeum(title) else ""
            if is_prio(title):
                flag += "🔥 "
            tag = f"{price:.2f} €".replace(".", ",") if price else "Preis?"
            wert = ""
            try:
                import marktwert
                game = marktwert.guess_game(title) if cat in ("TCG", "Pokémon") else "keine"
                wert = marktwert.bewertung(title, price, game) + "\n"
            except Exception as e:
                print(f"[Marktwert] übersprungen: {e}")
            flag = knopf(fingerprint(shop, title, url), shop, title, url) + flag
            lines.append(
                f"{flag}<b>{cat} · {shop}</b> · {tag} · <a href=\"{escape(url, quote=True)}\">LINK</a>\n"
                f"{escape(title)}\n"
                f"📊 {atext}\n"
                f"{wert}"
                f"↳ Resell: {resell_links(title, cat)}\n"
            )
        if nummer > MAX_BEWERTBAR:
            lines.append(f"<i>Bewerten geht fuer die ersten {MAX_BEWERTBAR} Treffer.</i>\n")
        notify("\n".join(lines), knoepfe or None)
        speichere_gemeldet(gemeldet)
        print(f"--> {len(new_items)} neue Drops gemeldet")
    else:
        print("--> nichts Neues")

    save_state(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
