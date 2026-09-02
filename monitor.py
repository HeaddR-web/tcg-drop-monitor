#!/usr/bin/env python3
"""
Pokemon TCG "30 Jahre" Verfuegbarkeits-Monitor
Prueft Haendler-Suchseiten auf neue Treffer und meldet per Telegram.

Kein Auto-Buy. Nur Beobachtung + Alert mit Direktlink.
"""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------- Konfiguration

BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TG_CHAT_ID", "")

STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Accept-Language": "de-DE,de;q=0.9",
}

# Suchbegriffe, die auf ein relevantes Produkt hindeuten
KEYWORDS = [
    "30 jahre",
    "30th anniversary",
    "30th celebration",
    "30-jahre",
    "jubilaeum",
    "jubiläum",
    # LEHRE AUS DEM 09.08.2026: Die Haendler benennen Produkte der 30-Jahre-Linie
    # NICHT einheitlich. Die "Erste Partner Illustrations-Kollektion" (UVP 17,99)
    # gehoert dazu, traegt aber weder "30" noch "Jubilaeum" im Namen und rutschte
    # deshalb komplett durch. Deshalb ab jetzt die PRODUKTLINIEN mitfuehren,
    # nicht nur die Jubilaeums-Woerter.
    "erste partner",
    "erste-partner",
    "first partner",
    "partner illustration",
    "partner-illustration",
    "illustrations-kollektion",
    "illustration collection",
    "partner card set",
    # Japanische Fassungen sind ausdruecklich erwuenscht (Ansage 26.08.2026).
    # Haendler listen JP-Importe teils mit dem japanischen Namen, dann greift
    # kein einziges der deutschen Stichwoerter.
    "30周年",
    "30 shuunen",
    "anniversary collection",
]

# ZIELLISTE 30 JAHRE (Stand 10.08.2026, abgeglichen an den Kollektionen von
# Feenturm und CardCosmos). Die Haendler benennen dieselben Produkte
# unterschiedlich, deshalb stehen hier die Eigennamen der Pokemon statt der
# Verpackungsart: "Feelinara-ex" findet die Kollektion UND die Tin-Box, egal ob
# ein Shop sie "30 Jahre" oder "30th Celebration" nennt.
ZIELLISTE = [
    "feelinara-ex", "quajutsu-ex", "nachtara-ex", "psiana-ex",
    "zeraora", "victini", "ditto premium", "figuren-kollektion",
    "figuren kollektion", "ordner-kollektion", "poster-kollektion",
    "tech-sticker", "mini-tin", "day & night", "tag & nacht",
]

# ZIELSETS (seit 02.09.2026): die laufenden Sets stehen in zielsets.txt, einer
# Datei, die von Hand gepflegt wird. Anlass: am 23.08. flogen Prismatic, Erhabene
# Helden und Co. als "Rauschquelle" aus dem Code, und eine Woche spaeter ging die
# Mega-Entwicklung-Top-Trainer-Box bei MediaMarkt Oesterreich (60 Euro, Markt
# 120) ungemeldet durch. Die Ansage dazu: ALLE laufenden Sets sind gewollt, nicht
# nur das Jubilaeum. Eine Textdatei kann bei keinem Refactoring "versehentlich"
# leer werden, und sie zeigt auf einen Blick, was der Monitor ueberhaupt sucht.
from zielsets import lade_zielsets
ZIELSETS = lade_zielsets()
KEYWORDS = KEYWORDS + ZIELLISTE + [z for z in ZIELSETS if z not in KEYWORDS]

# Produkte, die uns besonders interessieren (Prio-Markierung im Alert)
PRIO = ["ultra-premium", "ultra premium", "upc", "top-trainer", "top trainer", "ttb",
        "elite trainer", "etb", "display"]   # ETB seit 02.09.2026 (60 -> 120 gemessen)

# Zubehoer/Einzelkram, der nie gemeldet werden soll
# Zwei Stufen, und der Unterschied ist wichtig.
# EXCLUDE ist rettbar: bei der 30-Jahre-Linie heissen echte Kartenprodukte
# "Ordner-Kollektion", "Poster-Kollektion", "Figuren-Kollektion".
EXCLUDE = [
    "einzelkarte", "sleeve", "hülle", "huelle", "playmat", "spielmatte",
    "ordner", "binder", "album", "poster", "figur", "plüsch", "pluesch",
    "tasse", "becher", "verschiedene original",
]

# EXCLUDE_HART ist NICHT rettbar. Zubehoer und Merchandise enthalten nie
# Karten, also darf kein Wort wie "blister" oder "kollektion" sie zurueckholen.
# Anlass: "Pokemon back to school - Radiergummi Blister" rutschte am
# 27.08.2026 durch, weil "blister" als versiegelte Ware zaehlt.
EXCLUDE_HART = [
    "toploader", "top loader", "deckbox", "deck box", "sammelmappe",
    "kartenschutz", "schutzhülle", "schutzhuelle",
    "puzzle", "aufbewahrung", "lunchbox", "brettspiel", "lego", "kuscheltier",
    "rucksack", "t-shirt", "schlüsselanh", "schluesselanh", "bettwäsche",
    "trinkflasche", "socken", "malbuch", "radiergummi", "spardose",
    "handtuch", "schulranzen", "stundenplan",
]

# Preis-Obergrenzen je Produkttyp: UVP plus etwa 20 Prozent Toleranz.
# Nur Angebote bis zur Grenze werden gemeldet (Reselling lohnt nur zum Retail-Preis).
# Treffer OHNE erkennbaren Preis werden trotzdem gemeldet, markiert mit "Preis?".
PRICE_CAPS = [
    ("ultra-premium", 150.0),
    ("ultra premium", 150.0),
    ("top-trainer", 65.0),
    ("top trainer", 65.0),
    ("elite trainer", 65.0),
    ("display", 190.0),
    ("bundle", 42.0),
    ("tin", 40.0),
    ("kollektion", 60.0),
    ("collection", 60.0),
    ("blister", 25.0),
]
DEFAULT_CAP = 190.0

PRICE_RE = re.compile(r"(\d{1,4})[.,](\d{2})\s*€|€\s*(\d{1,4})[.,](\d{2})")


UVP_TOLERANZ = 1.20     # bis 20 Prozent ueber UVP gilt noch als Retail

# Fachshops, die selbst Reseller sind (Ansage 02.09.2026: "auf Retailer
# fokussieren"). Sie bleiben als Quelle drin, weil dort gelegentlich echte
# Ladenpreise auftauchen, melden aber nur noch Angebote BIS zur UVP. Die
# 20-Prozent-Toleranz gilt nur fuer Retailer: bei einem Reseller ist jeder
# Cent ueber UVP schon dessen Marge, da bleibt zum Flippen nichts.
RESELLER_QUELLEN = ("Card Corner", "Feenturm", "CardCosmos", "GeeksHeaven",
                    "ChiefCards", "Pokitrio", "CardsForAll", "Kartenbasis",
                    "Kofuku", "TCG-Trade")


def ist_reseller(name: str) -> bool:
    return name.startswith(RESELLER_QUELLEN)


def reseller_filter(src: dict, hits: list) -> list:
    """Wirft bei Reseller-Quellen alles ueber der reinen UVP raus."""
    if not ist_reseller(src.get("name", "")):
        return hits
    behalten, raus = [], 0
    for h in hits:
        titel, preis = h[0], (h[2] if len(h) > 2 else None)
        uvp_grenze = round(price_cap(titel) / UVP_TOLERANZ, 2)
        if sonderfall(titel):
            behalten.append(h)
            continue
        # Ohne Preis laesst sich UVP nicht pruefen, und ein Reseller-Treffer
        # ohne Preis ist nur Laerm (Card Corner liefert im Listing keinen).
        if not preis or preis > uvp_grenze:
            raus += 1
            continue
        behalten.append(h)
    if raus:
        print(f"[{src['name']}] Reseller: {raus} Treffer ueber UVP oder ohne Preis verworfen")
    return behalten

# --- Raffles und Live-Drops --------------------------------------------------
# Bei knapper Ware verlosen Haendler die Kaufrechte, statt sie zu verkaufen.
# Das ist die fairste und oft einzige Chance auf Retail-Preis. Solche Angebote
# sind nur Stunden bis wenige Tage online (die zwei Feenturm-Raffles vom
# 10.08.2026 waren am selben Tag schon wieder aus dem Katalog verschwunden).
# Deshalb gelten fuer sie Sonderregeln: kein Preisfilter, kein Ausverkauft-
# Filter, und sie stehen in der Meldung immer ganz oben.
RAFFLE_WOERTER = [
    "raffle", "verlosung", "verlost", "auslosung", "auslosung",
    "losverfahren", "losentscheid", "tickets", "ticket", "teilnahme",
    "lottery", "抽選", "gewinnspiel", "wichtelrunde",
    "zufallsprinzip", "zuteilung", "anmeldung", "registrierung",
]

# Live-Verkauf: angekuendigter Verkaufsstart zu einer festen Uhrzeit.
LIVE_WOERTER = [
    "live", "verkaufsstart", "sale start", "drop", "release-drop",
    "freischaltung", "startet um", "ab 18 uhr", "ab 20 uhr",
]


# Erkennungswoerter fuer Pokemon-Ware in shopweiten Katalogen (Shops, die auch
# Magic und One Piece fuehren). Serien-Namen stehen mit drin, weil viele Shops
# "Prismatic Evolutions Display" ohne das Wort Pokemon schreiben.
POKEMON_WOERTER = [
    "pokemon", "pokémon", "pikachu", "evoli", "eevee", "glurak", "charizard",
    "mewtu", "mewtwo", "relaxo", "snorlax", "karmesin", "purpur",
    "scarlet", "violet", "prismatic", "erhabene helden", "fatale flammen",
    "30 jahre", "30th celebration", "erste partner", "first partner",
    "nihil zero", "storm emerald", "abyss eye", "terastal", "mega evolution",
]
# Set-Namen aus zielsets.txt gelten ebenfalls als Pokemon-Beleg ("Delta-Herrschaft
# Booster" nennt kein Pokemon beim Namen). "151" bleibt draussen, zu kurz.
POKEMON_WOERTER += [z for z in ZIELSETS if z not in POKEMON_WOERTER and len(z) > 3]


# Wie lange ein frisch angelegtes Produkt als "Neuling" gilt.
# Haendler legen die Ware oft kurz vor dem Drop unter einem verfremdeten Namen
# an, damit Bots sie nicht ueber den Namen finden. Dagegen hilft kein besseres
# Woerterbuch, sondern nur ein namensunabhaengiges Signal: Anlage-Zeitpunkt.
# Schnellpass-Modus (EILIG_ONLY=1): meldet ausschliesslich Zeitkritisches.
# Gedacht fuer einen dichten Takt neben dem normalen 15-Minuten-Lauf.
EILIG_ONLY = os.environ.get("EILIG_ONLY", "").strip() in ("1", "true", "ja")

NEULING_STUNDEN = 48

# Woran ein getarnter Neuling trotzdem als Sammelkarten-Ware erkennbar bleibt.
# Den Produktnamen kann ein Shop verfremden, die Warenart praktisch nie: es
# bleibt ein Display, eine Box oder ein Bundle, sonst kann es niemand kaufen.
# Nur Ware, die selbst ein versiegeltes Produkt ist. Diese Liste rettet einen
# Treffer vor EXCLUDE (Beispiel "30th Celebration Mew Figuren Kollektion"),
# deshalb darf hier NICHTS Generisches wie "karten" oder "box" hinein.
VERSIEGELT_HINWEISE = [
    "display", "booster", "etb", "elite trainer", "top trainer", "blister",
    "tin", "bundle", "kollektion", "collection", "premium collection",
]

TCG_HINWEISE = [
    "display", "booster", "box", "bundle", "tin", "kollektion", "collection",
    "etb", "elite trainer", "top trainer", "blister", "deck", "karten",
    "tcg", "sammelkarten", "trading card", "premium",
]


def ist_neuling(produkt: dict) -> bool:
    """True, wenn das Produkt in den letzten NEULING_STUNDEN angelegt wurde."""
    from datetime import datetime, timedelta, timezone

    stempel = produkt.get("created_at") or produkt.get("published_at") or ""
    if not stempel:
        return False
    try:
        angelegt = datetime.fromisoformat(stempel.replace("Z", "+00:00"))
    except ValueError:
        return False
    if angelegt.tzinfo is None:
        angelegt = angelegt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - angelegt < timedelta(hours=NEULING_STUNDEN)


# Fristen in Raffle-Beschreibungen: "Teilnahme bis 15.08.2026", "endet am
# 15.08. um 20:00 Uhr", "Ziehung am 16.08.". Ein Raffle ohne erkannte Frist ist
# kein Fehler, dann fehlt die Zeile einfach.
FRIST_RE = re.compile(
    r"(?:bis|endet|ende|ziehung|teilnahmeschluss|deadline|schlie(?:ss|ß)t)"
    r"[^0-9]{0,25}(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{2,4})?"
    r"(?:[^0-9]{0,15}(\d{1,2})[:.](\d{2}))?",
    re.IGNORECASE,
)


def frist_aus_text(text: str) -> str:
    """Gibt die Teilnahmefrist als lesbaren String zurueck, sonst ''."""
    if not text:
        return ""
    sauber = re.sub(r"<[^>]+>", " ", text)
    sauber = " ".join(sauber.split())
    m = FRIST_RE.search(sauber)
    if not m:
        return ""
    tag, monat, jahr, std, minute = m.groups()
    try:
        tag, monat = int(tag), int(monat)
    except (TypeError, ValueError):
        return ""
    if not (1 <= tag <= 31 and 1 <= monat <= 12):
        return ""
    from datetime import date as _date
    jahr = jahr or str(_date.today().year)
    if len(jahr) == 2:
        jahr = "20" + jahr
    zeit = f" um {int(std):02d}:{minute} Uhr" if std else ""
    return f"{tag:02d}.{monat:02d}.{jahr}{zeit}"


def signal_text(produkt: dict) -> str:
    """Alles, woran Pokemon-Ware erkennbar ist, nicht nur der Titel.

    Der Titel ist das einzige Feld, das ein Haendler zur Bot-Abwehr leicht
    verfremden kann. Hersteller, Tags, Artikelnummer, URL-Kuerzel und der
    Dateiname des Produktbildes bleiben dabei fast immer sprechend:
    Titel "Artikel 4711" mit vendor "The Pokemon Company", sku "30jahre-mew"
    und Bild "30jahre-mew.webp" ist eindeutig, obwohl der Name nichts verraet.
    """
    teile = [
        str(produkt.get("title", "")),
        str(produkt.get("handle", "")),
        str(produkt.get("vendor", "")),
        str(produkt.get("product_type", "")),
        " ".join(str(t) for t in (produkt.get("tags") or [])),
    ]
    for v in (produkt.get("variants") or [])[:5]:
        teile.append(str(v.get("sku") or ""))
    for b in (produkt.get("images") or [])[:3]:
        teile.append(str(b.get("src") or "").split("/")[-1])
    return " ".join(teile).lower()


# --- Die Zielliste fuer die naechsten Wochen ---------------------------------
# Die 30-Jahre-Linie, so wie die Haendler sie tatsaechlich benennen (abgeglichen
# an den Kollektionen von Feenturm und CardCosmos am 10.08.2026). Alles hier
# Genannte hat oberste Prioritaet und umgeht die Zubehoer-Sperre.
JUBILAEUM_WOERTER = [
    "30 jahre", "30-jahre", "30th celebration", "30th anniversary",
    "jubilaeum", "jubiläum", "erste partner", "first partner",
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


def ist_versiegelt(titel: str) -> bool:
    """True nur bei versiegelter Ware (Display, Box, Bundle, Tin, Kollektion).

    Einzelkarten sind der groesste Rauschherd: ein Shop legt taeglich hunderte
    an, sie sind fuer das Flippen uninteressant und begraben die zwei Meldungen,
    auf die es ankommt. Ohne diese Huerde ist der Melder unbrauchbar.
    """
    t = titel.lower()
    if any(x in t for x in EINZELKARTEN_TELLS) or KARTENNUMMER_RE.search(t):
        return False
    if any(x in t for x in EINZEL_BOOSTER_TELLS):
        return False
    if any(x in t for x in ZUBEHOER_TELLS):
        return False
    return any(h in t for h in TCG_HINWEISE)


# Woran eine Einzelkarte erkennbar ist, auch ohne das Wort "Einzelkarte":
# Kartennummern wie "234/191", Zustandskuerzel und Grading-Angaben.
EINZELKARTEN_TELLS = [
    "einzelkarte", "single card", " psa ", "psa ", " bgs ", "cgc ",
    "gegradet", "graded", "near mint", "reverse holo", "holo rare",
    "vollbild", "full art", "alt art", "secret rare",
]
KARTENNUMMER_RE = re.compile(r"\b\d{1,3}\s*/\s*\d{2,3}\b")

# Einzelne Booster-Packs (5,99 Euro) sind keine Flip-Ware. MediaMarkt AT listet
# sie als "(1x Booster Pack)", andere Shops ohne jeden Hinweis im Namen, dann
# faengt sie nur der Preis: unter MIN_PREIS ist es ein Einzelbooster (02.09.2026).
EINZEL_BOOSTER_TELLS = ["1x booster", "(1x", "1 x booster", "einzelbooster", "einzel-booster", "einzelpack"]
MIN_PREIS = 8.0

# Zubehoer, das die Set-Namen im Titel traegt, aber keine Karten enthaelt.
# Geizhals listete eine "Acrylic Box Protezione ... 151 Ultra-Premium", die
# ueber das Stichwort "ultra premium" durchkam (Testlauf 02.09.2026).
ZUBEHOER_TELLS = ["acrylic", "acryl", "protector", "protezione", "schutzhülle",
                  "schutzhuelle", "sleeves", "toploader", "display case", "displaycase"]


def ist_raffle(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in RAFFLE_WOERTER)


def ist_live(title: str) -> bool:
    t = title.lower()
    return any(w in t for w in LIVE_WOERTER)


def sonderfall(title: str) -> bool:
    """Raffle oder Live-Drop: umgeht Preis- und Verfuegbarkeitsfilter."""
    return ist_raffle(title) or ist_live(title)


def price_cap(title: str) -> float:
    """Obergrenze, ab der ein Angebot als Reseller-Preis gilt.

    Vorrang hat die gepflegte UVP-Liste in marktwert.py: sie kennt den echten
    Herstellerpreis je Produkt. Die groben PRICE_CAPS sind nur der Rueckfall.
    Ohne diese Kopplung rutschte die Erste-Partner-Kollektion (UVP 17,99) mit
    59,99 Euro durch, weil die generische Grenze fuer "Kollektion" 60 Euro war.
    """
    try:
        import marktwert
        uvp = marktwert.uvp_ref(title)
        if uvp:
            return round(uvp * UVP_TOLERANZ, 2)
    except Exception:
        pass
    t = title.lower()
    for key, cap in PRICE_CAPS:
        if key in t:
            return cap
    return DEFAULT_CAP


# Status-Woerter: Produkt existiert, ist aber (noch) nicht kaufbar.
# Solche Treffer werden NICHT gemeldet und NICHT als gesehen markiert:
# sobald der Shop auf kaufbar umstellt, feuert der Alert automatisch.
NOT_BUYABLE = [
    "ausverkauft", "sold out", "nicht verfügbar", "nicht verfuegbar",
    "verkauf startet", "demnächst", "demnaechst", "coming soon", "benachrichtigen",
]


def extract_status(node) -> str:
    """'wartet' wenn nicht kaufbar, 'vorbestellbar' bei Preorder, sonst ''.

    Wichtig: erst die GANZE Produktkachel einsammeln, dann bewerten. Sonst
    gewinnt ein "*Vorbestellung*" im Titel gegen ein "Ausverkauft"-Etikett
    weiter aussen, und das Radar schickt zu toten Angeboten.
    """
    # Groesste noch produktbezogene Umgebung nehmen (nicht die erstbeste):
    # das Status-Etikett steht oft weiter aussen als der Titel.
    el = node
    text = ""
    for _ in range(5):
        if el is None:
            break
        t = el.get_text(" ", strip=True).lower()
        if len(t) > 700:         # zu weit oben, das ist schon die Trefferliste
            break
        text = t
        el = el.parent

    if any(w in text for w in NOT_BUYABLE):
        return "wartet"
    if "vorbestell" in text or "pre-order" in text or "preorder" in text:
        return "vorbestellbar"
    return ""


def extract_price(node) -> float:
    """Sucht den niedrigsten Euro-Preis in der Produktkachel um den Link herum."""
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

# Haendler: name, urls (eine oder mehrere Suchseiten), css-selector, base
# Die zweite URL je Shop ist die TEST-MODUS-Suche (aktuelle Sets), im September entfernen.
SOURCES = [
    {
        "name": "Müller",
        "urls": [
            "https://www.mueller.de/search/?q=pokemon%2030%20jahre",
            "https://www.mueller.de/search/?q=pokemon%20erhabene%20helden",
            "https://www.mueller.de/search/?q=pokemon%20prismatic",
        ],
        "item": "a[href*='/p/']",
        "base": "https://www.mueller.de",
    },
    {
        "name": "GameStop DE",
        "urls": [
            "https://www.gamestop.de/SearchResult/QuickSearch?q=pokemon+30+jahre",
            "https://www.gamestop.de/SearchResult/QuickSearch?q=pokemon+erhabene+helden",
            "https://www.gamestop.de/SearchResult/QuickSearch?q=pokemon+prismatic",
        ],
        "item": "a[href*='/Games/']",
        "base": "https://www.gamestop.de",
    },
    {
        "name": "Alternate",
        "urls": [
            "https://www.alternate.de/listing.xhtml?q=pokemon+30+jahre",
            "https://www.alternate.de/listing.xhtml?q=pokemon+erhabene+helden",
            "https://www.alternate.de/listing.xhtml?q=pokemon+prismatic",
            "https://www.alternate.de/listing.xhtml?q=pokemon+japanisch",
        ],
        "item": "a.productBox",
        "base": "https://www.alternate.de",
    },
    # OTTO ENTFERNT (09.08.2026): Die Pokemon-Ware dort kommt fast ausschliesslich
    # von Marketplace-Haendlern, also genau den Resellern, gegen die wir kaufen
    # wollen. Die Treffer lagen regelmaessig weit ueber UVP. Wir wollen Retailer,
    # nicht den Zweitmarkt.
    {
        "name": "Games Island",
        "urls": [
            "https://www.games-island.eu/search?sSearch=pokemon+30+jahre",
            "https://www.games-island.eu/search?sSearch=pokemon+erhabene+helden",
            "https://www.games-island.eu/search?sSearch=pokemon+prismatic",
            "https://www.games-island.eu/search?sSearch=pokemon+japanisch",
        ],
        "item": "a.product--title",
        "base": "",
    },
    {
        # Suche ist JS-only, deshalb Startseite (Neuheiten) + Set-Kategorieseiten.
        # Die 30-Jahre-Kategorien sind die eigentlichen Release-Seiten (Vorbestellung laeuft seit Juli).
        "name": "Card Corner",
        "urls": [
            "https://www.card-corner.de/",
            "https://www.card-corner.de/pokemon-30-jahre",
            "https://www.card-corner.de/pokemon-30th-celebration",
            "https://www.card-corner.de/pokemon-erhabene-helden",
        ],
        "item": "a.text-clamp-2",
        "base": "",
    },
    {
        # Grosse Kette mit echter UVP-Ware. HTML wird per JavaScript gebaut,
        # die Trefferliste liegt aber als schema.org-Daten im Quelltext.
        "name": "MediaMarkt",
        "urls": [
            "https://www.mediamarkt.de/de/search.html?query=pokemon%2030%20jahre",
            "https://www.mediamarkt.de/de/search.html?query=pokemon%20prismatic",
            "https://www.mediamarkt.de/de/search.html?query=pokemon%20erhabene%20helden",
            "https://www.mediamarkt.de/de/search.html?query=pokemon%20karten",
        ],
        "parser": "jsonld",
        "base": "https://www.mediamarkt.de",
    },
    {
        "name": "Saturn",
        "urls": [
            "https://www.saturn.de/de/search.html?query=pokemon%2030%20jahre",
            "https://www.saturn.de/de/search.html?query=pokemon%20prismatic",
            "https://www.saturn.de/de/search.html?query=pokemon%20karten",
        ],
        "parser": "jsonld",
        "base": "https://www.saturn.de",
    },
    # --- OESTERREICH (seit 02.09.2026) -------------------------------------
    # Anlass: Mega-Entwicklung-Top-Trainer-Box bei MediaMarkt AT fuer 60 Euro,
    # Markt 120, und kein einziger .at-Shop stand in der Liste. Der Kauf laeuft
    # online mit Versand nach Deutschland.
    # Geprueft und NICHT aufgenommen (02.09.2026): Mueller AT (mueller.at fuehrt
    # online nur Huellen, Alben und Deckboxen, keine versiegelte Ware, und nach
    # drei Abrufen kommt eine JavaScript-Sperre), Libro, Thalia AT, Gameware AT
    # (alle drei 403 fuer Skripte, Sortiment ungeprueft).
    {
        # MediaMarkt AT weist requests und curl mit 403 ab (gemessen 02.09.2026),
        # laesst aber einen echten Browser durch. Deshalb "browser": True, und
        # damit nur auf dem Mac (SOURCES_ONLY in scripts/local-monitor.sh), in
        # der Cloud ausgeschlossen (SOURCES_EXCLUDE in monitor.yml).
        # Saturn AT gibt es nicht mehr, saturn.at leitet auf mediamarkt.at um.
        "name": "MediaMarkt AT",
        "urls": [
            "https://www.mediamarkt.at/de/search.html?query=pokemon%20karten",
            "https://www.mediamarkt.at/de/search.html?query=pokemon%2030%20jahre",
            "https://www.mediamarkt.at/de/search.html?query=pokemon%20top-trainer-box",
        ],
        "parser": "jsonld",
        "browser": True,
        "base": "https://www.mediamarkt.at",
    },
    {
        # Smyths Toys AT (gemessen 02.09.2026): fuehrt alle aktuellen Top-Trainer-
        # Boxen zu 54,99, also unter UVP. curl bekommt nur eine 1-KB-Huelle, der
        # Browser die volle Seite. Die Suchseite kennt keinen Lagerstatus, den
        # traegt erst die Produktseite als JSON-LD (InStock/OutOfStock/PreOrder).
        # Deshalb holt der Parser je relevantem Treffer die Produktseite nach,
        # gedeckelt auf SMYTHS_MAX_DETAILS Abrufe pro Lauf.
        "name": "Smyths AT",
        "urls": [
            "https://www.smythstoys.com/at/de-at/search?text=pokemon%20top%20trainer%20box",
            "https://www.smythstoys.com/at/de-at/search?text=pokemon%2030%20jahre",
            "https://www.smythstoys.com/at/de-at/search?text=pokemon%20karten%20kollektion",
        ],
        "parser": "smyths",
        "browser": True,
        "base": "https://www.smythstoys.com",
    },
    {
        # Geizhals.at (gemessen 02.09.2026): Preisvergleich, per Browser lesbar.
        # Sammelquelle fuer oesterreichische Ladenpreis-Haendler, die selbst
        # nicht lesbar sind: Pagro & Libro (Cloudflare, auch mit Loeser 403),
        # Kaufland-Marktplatz, Amazon.at. Die Suchseite nennt je Produkt den
        # guenstigsten Preis und die Angebotszahl. Liegt der Preis unter der
        # UVP-Grenze, verkauft gerade ein Retailer, und genau das ist der Alarm.
        # "keine Angebote" = wartet (Restock-Kandidat).
        "name": "Geizhals AT",
        "urls": [
            "https://geizhals.at/?fs=pokemon+top-trainer-box&in=",
            "https://geizhals.at/?fs=pokemon+30+jahre&in=",
            "https://geizhals.at/?fs=pokemon+ultra+premium&in=",
        ],
        "parser": "geizhals",
        "browser": True,
        "base": "https://geizhals.at",
    },
    # --- KATEGORIE-WACHE ------------------------------------------------------
    # Diese Quellen ueberwachen die KOMPLETTE Pokemon-Kategorie des Shops und
    # melden ALLES, was dort neu auftaucht, ohne Stichwort-Pruefung. Damit kann
    # kein Produkt mehr durchrutschen, nur weil sein Name unbekannt ist.
    # --- 30-JAHRE-KOLLEKTIONEN: die Kernwache -------------------------------
    # Die eigentliche Zielware. Kleine, vollstaendige Listen statt Shop-Katalog:
    # hier ist jedes Produkt erfasst, auch die laengst ausverkauften. Genau die
    # sind die Restock-Kandidaten (CardCosmos: 19 Artikel, alle ausverkauft).
    {
        "name": "Feenturm 30 Jahre",
        "urls": ["https://feenturm.de/collections/pokemon-30th-celebration/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://feenturm.de",
        "alles_neue": True,
        "kernwache": True,
    },
    {
        "name": "CardCosmos 30 Jahre",
        "urls": ["https://cardcosmos.de/collections/pokemon-30th-celebration-vorbestellen/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://cardcosmos.de",
        "alles_neue": True,
        "kernwache": True,
    },
    # Deckshop bewusst NICHT als Kernwache: die dortige "30 Jahre"-Kollektion
    # enthaelt ausschliesslich Einzelkarten (Karnimani MEP048 DE und so weiter),
    # keine versiegelte Ware. Genau das Rauschen, das die echten Meldungen
    # begraebt (geprueft 10.08.2026).
    # Shopweite Katalog-Feeds (Shopify-JSON): liefern Preis UND Verfuegbarkeit
    # und sind nach zuletzt geaendert sortiert. Damit erwische ich Raffles und
    # Restocks auch dann, wenn sie in keiner Pokemon-Kategorie einsortiert sind.
    # Genau das war bei den zwei Feenturm-Raffles am 10.08.2026 der Fall.
    {
        "name": "Feenturm (Katalog)",
        "urls": ["https://feenturm.de/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://feenturm.de",
        "shopweit": True,
    },
    {
        "name": "GeeksHeaven (Katalog)",
        "urls": ["https://geeksheaven.de/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://geeksheaven.de",
        "shopweit": True,
    },
    {
        "name": "CardCosmos (Katalog)",
        "urls": ["https://cardcosmos.de/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://cardcosmos.de",
        "shopweit": True,
    },
    {
        "name": "ChiefCards (Katalog)",
        "urls": ["https://chiefcards.de/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://chiefcards.de",
        "shopweit": True,
    },
    {
        "name": "Pokitrio (Katalog)",
        "urls": ["https://pokitrio.de/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://pokitrio.de",
        "shopweit": True,
    },
    {
        "name": "CardsForAll (Katalog)",
        "urls": ["https://cardsforall.de/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://cardsforall.de",
        "shopweit": True,
    },
    {
        "name": "Kartenbasis (Katalog)",
        "urls": ["https://kartenbasis.de/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://kartenbasis.de",
        "shopweit": True,
    },
    {
        "name": "Kofuku (Katalog)",
        "urls": ["https://kofuku.de/products.json?limit=250"],
        "parser": "shopify",
        "base": "https://kofuku.de",
        "shopweit": True,
    },
    # Deckshop-Katalog ebenfalls raus: 250 von 250 Artikeln sind Einzelkarten.
    {
        "name": "Feenturm (alles neu)",
        "urls": ["https://feenturm.de/collections/pokemon"],
        "item": "a[href*='/products/']",
        "base": "https://feenturm.de",
        "alles_neue": True,
    },
    {
        "name": "CardsForAll (alles neu)",
        "urls": ["https://cardsforall.de/collections/pokemon"],
        "item": "a[href*='/products/']",
        "base": "https://cardsforall.de",
        "alles_neue": True,
    },
    {
        "name": "GeeksHeaven (alles neu)",
        "urls": ["https://geeksheaven.de/collections/pokemon-tcg"],
        "item": "a[href*='/products/']",
        "base": "https://geeksheaven.de",
        "alles_neue": True,
    },
    {
        "name": "TCG-Trade (alles neu)",
        "urls": ["https://tcg-trade.de/search?q=pokemon"],
        "item": "a[href*='/p/']",
        "base": "https://tcg-trade.de",
        "alles_neue": True,
    },
    {
        # Die 30-Jahre-Kategorieseiten: hier landet die Ware zuerst
        "name": "Card Corner (alles neu)",
        "urls": [
            "https://www.card-corner.de/pokemon-30-jahre",
            "https://www.card-corner.de/pokemon-30th-celebration",
        ],
        "item": "a.text-clamp-2",
        "base": "",
        "alles_neue": True,
    },
    {
        "name": "Elbenwald (alles neu)",
        "urls": ["https://www.elbenwald.de/pokemon"],
        "item": "a.product-name",
        "base": "https://www.elbenwald.de",
        "alles_neue": True,
    },
    {
        "name": "Müller (alles neu)",
        "urls": ["https://www.mueller.de/search/?q=pokemon"],
        "item": "a[href*='/p/']",
        "base": "https://www.mueller.de",
        "alles_neue": True,
    },
    {
        "name": "Alternate (alles neu)",
        "urls": ["https://www.alternate.de/listing.xhtml?q=pokemon"],
        "item": "a.productBox",
        "base": "https://www.alternate.de",
        "alles_neue": True,
    },
    {
        "name": "TCG-Trade",
        "urls": [
            "https://tcg-trade.de/search?q=pokemon+30+jahre",
            "https://tcg-trade.de/search?q=pokemon+prismatic",
            "https://tcg-trade.de/search?q=pokemon+erhabene+helden",
            "https://tcg-trade.de/search?q=pokemon+japanisch",
        ],
        "item": "a[href*='/p/']",
        "base": "https://tcg-trade.de",
    },
    {
        "name": "CardsForAll",
        "urls": [
            "https://cardsforall.de/collections/vorbestellung",
            "https://cardsforall.de/collections/pokemon",
        ],
        "item": "a[href*='/products/']",
        "base": "https://cardsforall.de",
    },
    {
        "name": "Elbenwald",
        "urls": [
            "https://www.elbenwald.de/search?sSearch=pokemon+30+jahre",
            "https://www.elbenwald.de/search?sSearch=pokemon+prismatic",
            "https://www.elbenwald.de/search?sSearch=pokemon+erhabene+helden",
        ],
        "item": "a.product-name",
        "base": "https://www.elbenwald.de",
    },
    {
        "name": "GeeksHeaven",
        "urls": [
            "https://geeksheaven.de/search?q=30+jahre&type=product",
            "https://geeksheaven.de/search?q=30th+celebration&type=product",
            "https://geeksheaven.de/search?q=prismatic+evolutions&type=product",
            "https://geeksheaven.de/search?q=ascendant+heroes&type=product",
            "https://geeksheaven.de/search?q=terastal&type=product",
        ],
        "item": "a[href*='/products/']",
        "base": "https://geeksheaven.de",
    },
    {
        "name": "Feenturm",
        "urls": [
            "https://feenturm.de/collections/pokemon-30th-celebration",
        ],
        "item": "a[href*='/products/']",
        "base": "https://feenturm.de",
    },
    {
        "name": "Amazon.de",
        "urls": [
            "https://www.amazon.de/s?k=pokemon+30+jahre+top+trainer+box",
            "https://www.amazon.de/s?k=pokemon+erhabene+helden",
            "https://www.amazon.de/s?k=pokemon+prismatic+evolutions",
            "https://www.amazon.de/s?k=pokemon+karten+japanisch",
        ],
        "item": "a.a-link-normal.s-link-style.a-text-normal",
        "base": "https://www.amazon.de",
    },
    # Pokémon Center EU entfernt (20.07.2026): Imperva/Incapsula-Bot-Schutz liefert
    # nur einen JS-Prüfstub, ohne echten Browser nicht abfragbar, von keiner IP.
]

# Optional: Lauf auf bestimmte Shops begrenzen, z.B. SOURCES_ONLY="Amazon.de,Pokémon Center EU"
# (genutzt vom lokalen Mac-Laeufer fuer die Shops, die GitHub-IPs blocken)
_only = os.environ.get("SOURCES_ONLY", "").strip()
if _only:
    _wanted = {n.strip() for n in _only.split(",")}
    SOURCES = [s for s in SOURCES if s["name"] in _wanted]

# Gegenstück: bestimmte Shops auslassen. GitHub setzt SOURCES_EXCLUDE="Amazon.de",
# weil der lokale Mac-Läufer Amazon macht (sonst doppelte Meldungen).
_excl = os.environ.get("SOURCES_EXCLUDE", "").strip()
if _excl:
    _drop = {n.strip() for n in _excl.split(",")}
    SOURCES = [s for s in SOURCES if s["name"] not in _drop]


# ---------------------------------------------------------------- Hilfsfunktionen

def load_state() -> dict:
    """Zustand je Produkt: {fingerprint: "kaufbar"|"wartet"}.
    Nicht nur "schon gesehen", sonst wird ein Restock (ausverkauft -> wieder
    lieferbar) nie gemeldet, und genau das ist der wichtigste Kaufmoment."""
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            if isinstance(data, list):        # altes Format migrieren
                return {fp: "kaufbar" for fp in data}
            return dict(data)
        except Exception:
            return {}
    return {}


def save_state(seen: dict) -> None:
    STATE_FILE.write_text(json.dumps(seen, ensure_ascii=False, sort_keys=True))


# Wieviele Treffer einer Meldung Bewertungs-Knoepfe bekommen. Mehr als das
# macht die Tastatur unter der Nachricht laenger als die Nachricht selbst.
MAX_BEWERTBAR = 8
# Nachschlagewerk fuer feedback.py: welcher Fingerabdruck war welches Produkt.
# Ohne das steht in der Bewertung nur ein Hash und niemand weiss, was bewertet
# wurde. Bewusst getrennt von state.json, damit ein Rueckbau der Bewertungen
# den Melde-Zustand nicht anfasst.
GEMELDET_FILE = Path(os.environ.get("GEMELDET_FILE", "gemeldet.json"))
GEMELDET_MAX = 400


def lade_gemeldet() -> dict:
    if GEMELDET_FILE.exists():
        try:
            data = json.loads(GEMELDET_FILE.read_text())
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}


def speichere_gemeldet(daten: dict) -> None:
    # Nur die juengsten Eintraege behalten, sonst waechst die Datei ewig und
    # jeder Cloud-Lauf liest sie neu ein.
    if len(daten) > GEMELDET_MAX:
        neueste = sorted(daten.items(), key=lambda kv: kv[1].get("wann", ""), reverse=True)
        daten = dict(neueste[:GEMELDET_MAX])
    GEMELDET_FILE.write_text(json.dumps(daten, ensure_ascii=False, indent=1, sort_keys=True))


def notify(text: str, knoepfe: list = None) -> bool:
    """True, wenn zugestellt (oder lokal ausgegeben). False bei Versandfehler.

    knoepfe ist eine Telegram-Tastatur (inline_keyboard). Sie haengt an genau
    EINER Teilnachricht, und zwar an der ersten: dort stehen die nummerierten
    Treffer [1] bis [8], auf die sich die Knoepfe beziehen.
    """
    if not BOT_TOKEN or not CHAT_ID:
        print("[WARN] Telegram-Credentials fehlen, Ausgabe nur lokal:")
        print(text)
        return True
    # Telegram-Limit 4096 Zeichen: lange Meldungen an Absatzgrenzen aufteilen
    if len(text) > 3800:
        ok = True
        chunk, size, erste = [], 0, True
        for block in text.split("\n\n"):
            if size + len(block) > 3800 and chunk:
                ok = _send("\n\n".join(chunk), knoepfe if erste else None) and ok
                chunk, size, erste = [], 0, False
            chunk.append(block)
            size += len(block) + 2
        if chunk:
            ok = _send("\n\n".join(chunk), knoepfe if erste else None) and ok
        return ok
    return _send(text, knoepfe)


def _send(text: str, knoepfe: list = None) -> bool:
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
                **({"reply_markup": {"inline_keyboard": knoepfe}} if knoepfe else {}),
            },
            timeout=20,
        )
        if r.status_code != 200:
            print(f"[WARN] Telegram-Fehler {r.status_code}: {r.text[:200]}")
            return False
        return True
    except Exception as e:
        print(f"[WARN] Telegram nicht erreichbar: {e}")
        return False


def is_relevant(title: str, src: dict = None) -> bool:
    """Normal: Titel muss ein bekanntes Stichwort enthalten.

    KATEGORIE-WACHE (src['alles_neue']): kein Stichwort noetig. Alles, was in
    der Pokemon-Kategorie eines Shops NEU auftaucht, wird gemeldet.
    Grund: Am 09.08.2026 ging die "Erste Partner Illustrations-Kollektion"
    komplett durch, weil ihr Name kein einziges meiner Stichwoerter enthielt.
    Solange ich nach Namen filtere, rutscht jedes unbekannte Produkt durch.
    Umgekehrt herum kann das nicht passieren.
    """
    t = title.lower()
    # Sprache zuerst: chinesische Fassungen sind unerwuenscht, egal wie gut
    # der Rest des Titels passt (Ansage 26.08.2026).
    if ist_chinesisch(title):
        return False
    # Konvolute und Gebrauchtware raus, ebenfalls vor allen anderen Pfaden.
    # Wichtig: muss VOR der Kategorie-Wache stehen, die sonst jeden neuen
    # Artikel unabhaengig vom Namen meldet.
    if ist_konvolut(title):
        return False
    # Fremdes Sammelkartenspiel namentlich im Titel: raus, egal ueber welchen
    # Pfad der Treffer kaeme (Ansage 27.08.2026).
    if ist_fremdes_tcg(title):
        return False
    # Die Zubehoer-Sperre gilt NICHT fuer die 30-Jahre-Linie. Dort heissen
    # echte Sammelprodukte "Ordner-Kollektion", "Poster-Kollektion" und
    # "Figuren-Kollektion" und enthalten Karten. Ohne diese Ausnahme sperrt
    # die Zubehoer-Liste drei Artikel der Zielliste aus (geprueft 10.08.2026).
    if any(x in t for x in EXCLUDE_HART):
        return False
    if any(x in t for x in EXCLUDE) and not ist_jubilaeum(t):
        return False
    # Harte Huerde vor allem anderen: nur versiegelte Ware. Raffles sind
    # ausgenommen, dort steht die Warenart oft gar nicht im Titel.
    if not ist_versiegelt(t) and not sonderfall(t):
        return False
    if src and src.get("shopweit"):
        # Ganzer Shop-Katalog statt Pokemon-Kategorie: hier muss der Titel
        # selbst Pokemon verraten, sonst kaemen Magic und One Piece mit.
        return len(t) > 8 and any(w in t for w in POKEMON_WOERTER)
    if src and src.get("alles_neue"):
        return len(t) > 8          # Kategorie ist schon Pokemon, Name egal
    if "pok" not in t:
        return False
    return any(k in t for k in KEYWORDS)


def is_prio(title: str) -> bool:
    t = title.lower()
    return any(p in t for p in PRIO)


def normalize_url(url: str) -> str:
    """Stabile Produkt-ID aus einer URL. Amazon hängt bei JEDEM Abruf wechselnde
    Tracking-Parameter an (/ref=sr_1_9?dib=...), das erzeugte sonst endlos Duplikate."""
    u = url.split("#")[0]
    m = re.search(r"/dp/([A-Z0-9]{10})", u)   # Amazon: ASIN ist die echte Produkt-ID
    if m:
        return "amazon:" + m.group(1)
    u = u.split("?")[0]
    u = re.sub(r"/ref=[^/]*$", "", u)
    return u.rstrip("/")


def fingerprint(source: str, title: str, url: str) -> str:
    # Shop + normalisierte URL. Titel schwankt bei manchen Shops ("Auf Lager"),
    # rohe URLs schwanken bei Amazon -> beides würde Duplikate erzeugen.
    raw = f"{source}|{normalize_url(url)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def fetch(url: str, name: str) -> str:
    """Holt eine Seite; bei Bot-Block (403) zweiter Versuch ueber curl,
    dessen TLS-Fingerprint manche Shops (Pokémon Center) durchlassen."""
    status = None
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200:
            return r.text
        status = r.status_code
    except Exception as e:
        print(f"[{name}] Netzwerkfehler: {e}")

    import subprocess
    try:
        p = subprocess.run(
            ["curl", "-sS", "--max-time", "25",
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


SCRAPLING = os.path.expanduser("~/.local/bin/scrapling")


def fetch_browser(url: str, name: str) -> str:
    """Holt eine Seite ueber einen echten Browser (Scrapling, stealthy-fetch).

    Fuer Shops, die requests UND curl mit 403 abweisen, aber einen echten
    Browser durchlassen: MediaMarkt Oesterreich (gemessen 02.09.2026, die .de-
    Seite antwortet normal). Laeuft nur auf dem Mac, nie in der Cloud, und ist
    mit 10 bis 30 Sekunden je Seite deutlich langsamer als fetch()."""
    import subprocess
    import tempfile
    if not os.path.exists(SCRAPLING):
        print(f"[{name}] Scrapling fehlt ({SCRAPLING}), Quelle uebersprungen")
        return ""
    with tempfile.TemporaryDirectory() as d:
        ziel = os.path.join(d, "seite.html")
        try:
            p = subprocess.run(
                [SCRAPLING, "extract", "stealthy-fetch", url, ziel,
                 "--disable-resources", "--network-idle", "--timeout", "60000"],
                capture_output=True, text=True, timeout=150,
            )
        except Exception as e:
            print(f"[{name}] Browser-Abruf fehlgeschlagen: {e}")
            return ""
        if os.path.exists(ziel):
            with open(ziel, encoding="utf-8", errors="replace") as f:
                html = f.read()
            if html:
                return html
        print(f"[{name}] Browser-Abruf leer: {(p.stderr or p.stdout)[-300:].strip()}")
    return ""


def check_jsonld(src: dict) -> list:
    """Parser fuer Shops, die ihre Trefferliste als schema.org-Daten ausliefern
    (JSON-LD ItemList). Damit sind Ketten erreichbar, deren HTML per JavaScript
    gebaut wird und die sonst blockiert waeren, z.B. MediaMarkt und Saturn."""
    hits = []
    for url in src["urls"]:
        if src.get("browser"):
            html = fetch_browser(url, src["name"])
        else:
            html = fetch(url, src["name"])
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(tag.string or "{}")
            except Exception:
                continue
            for eintrag in (data if isinstance(data, list) else [data]):
                if not isinstance(eintrag, dict):
                    continue
                for item in (eintrag.get("itemListElement") or []):
                    # Brotkrumen-Listen (BreadcrumbList) tragen als "item" nur
                    # eine URL-Zeichenkette. Mueller AT liefert so eine, und
                    # ohne diese Pruefung stirbt der Parser daran (02.09.2026).
                    if not isinstance(item, dict):
                        continue
                    prod = item.get("item") or {}
                    if not isinstance(prod, dict):
                        continue
                    titel = " ".join(str(prod.get("name", "")).split())
                    link = prod.get("url") or ""
                    if not titel or not link or not is_relevant(titel, src):
                        continue
                    angebot = prod.get("offers") or {}
                    if isinstance(angebot, list):
                        angebot = angebot[0] if angebot else {}
                    try:
                        preis = float(angebot.get("price") or 0)
                    except (TypeError, ValueError):
                        preis = 0.0
                    if preis and preis > price_cap(titel) and not sonderfall(titel):
                        continue
                    if preis and preis < MIN_PREIS:
                        continue          # Einzelbooster
                    verfuegbar = str(angebot.get("availability", "")).lower()
                    status = "wartet" if "outofstock" in verfuegbar else ""
                    if sonderfall(titel):
                        status = ""      # Raffle immer melden
                    hits.append((titel[:180], link, preis, status))
        time.sleep(1)
    # Dieselbe Ware taucht auf mehreren Suchseiten auf ("pokemon karten" und
    # "pokemon top-trainer-box" liefern beide die ETB). Ohne diese Stelle stand
    # sie im Testlauf am 02.09.2026 zweimal in einer Meldung.
    gesehen, eindeutig = set(), []
    for h in hits:
        if h[1] in gesehen:
            continue
        gesehen.add(h[1])
        eindeutig.append(h)
    return eindeutig


SMYTHS_MAX_DETAILS = 10  # Produktseiten je Lauf, jede kostet 15-30 s Browser


def _preis_aus_text(text: str) -> float:
    """Preis wie '54 ,99 €' (Smyths setzt Euro und Cent in getrennte Spans)."""
    m = re.search(r"(\d{1,4})\s*[.,]\s*(\d{2})\s*€", text)
    if not m:
        m = re.search(r"€\s*(\d{1,4})\s*[.,]\s*(\d{2})", text)
    if not m:
        return 0.0
    try:
        return float(f"{m.group(1)}.{m.group(2)}")
    except ValueError:
        return 0.0


def _status_aus_produktseite(html: str) -> str:
    """Liest offers.availability aus dem JSON-LD einer Produktseite."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "{}")
        except Exception:
            continue
        for eintrag in (data if isinstance(data, list) else [data]):
            if not isinstance(eintrag, dict) or eintrag.get("@type") != "Product":
                continue
            angebot = eintrag.get("offers") or {}
            if isinstance(angebot, list):
                angebot = angebot[0] if angebot else {}
            verf = str(angebot.get("availability", "")).lower()
            if "outofstock" in verf or "soldout" in verf:
                return "wartet"
            if "preorder" in verf:
                return "vorbestellbar"
            if "instock" in verf:
                return ""
    return "unklar"


def check_smyths(src: dict) -> list:
    """Smyths Toys: Kacheln aus dem HTML, Lagerstatus von der Produktseite."""
    hits, gesehen = [], set()
    for url in src["urls"]:
        html = fetch_browser(url, src["name"])
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/p/" not in href or not a.find("h2"):
                continue
            if href.startswith("/"):
                href = src["base"] + href
            if href in gesehen:
                continue
            titel = " ".join(a.find("h2").get_text(" ", strip=True).split())
            if not titel or not is_relevant(titel, src):
                continue
            gesehen.add(href)
            preis = _preis_aus_text(a.get_text(" ", strip=True))
            if preis and preis > price_cap(titel) and not sonderfall(titel):
                continue
            if preis and preis < MIN_PREIS:
                continue
            hits.append([titel[:180], href, preis, ""])
        time.sleep(1)
    # Lagerstatus nachholen: ohne ihn wuerde jede ausverkaufte Box als kaufbar
    # gemeldet, und "wartet"-Treffer bleiben absichtlich ungespeichert, damit
    # der Alarm beim Umschalten auf kaufbar feuert.
    # Reihenfolge: erst Prio-Ware (ETB, UPC, Display), dann alles, was laut
    # Gedaechtnis gerade "wartet" (Restock-Kandidaten), dann der Rest. Was
    # ueber dem Deckel liegt, geht als "unklar" raus und wird in der Meldung
    # so gekennzeichnet, statt still als kaufbar zu gelten.
    try:
        bekannt = load_state()
    except Exception:
        bekannt = {}
    def rang(h):
        if is_prio(h[0]):
            return 0
        if bekannt.get(fingerprint(src["name"], h[0], h[1])) == "wartet":
            return 1
        return 2
    hits.sort(key=rang)
    # Smyths AT antwortete am 02.09.2026 nachmittags auf jede Produktseite mit
    # einer 162-Byte-502-Huelle, waehrend die Suche normal lief. Zwei solche
    # Fehlseiten in Folge, und der Rest wird nicht mehr abgerufen: das spart
    # fuenf Minuten Browserzeit, die Treffer gehen als "unklar" raus.
    fehlseiten = 0
    geprueft = 0
    for h in hits:
        if geprueft >= SMYTHS_MAX_DETAILS or fehlseiten >= 2:
            h[3] = "unklar"
            continue
        detail = fetch_browser(h[1], src["name"])
        geprueft += 1
        if not detail or len(detail) < 2000 or "application/ld+json" not in detail:
            fehlseiten += 1
            h[3] = "unklar"
            if fehlseiten >= 2:
                print(f"[{src['name']}] Produktseiten liefern Fehlseiten "
                      f"({len(detail or '')} Bytes), Lagerstatus bleibt ungeprueft")
            continue
        fehlseiten = 0
        h[3] = _status_aus_produktseite(detail)
    return [tuple(h) for h in hits]


def check_geizhals(src: dict) -> list:
    """Geizhals-Suchergebnis: Name, guenstigster Preis, Angebotszahl."""
    hits, gesehen = [], set()
    for url in src["urls"]:
        html = fetch_browser(url, src["name"])
        if not html:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a.galleryview__name-link"):
            href = a.get("href", "")
            titel = " ".join(a.get_text(" ", strip=True).split())
            if not href or not titel or not is_relevant(titel, src):
                continue
            href = href.split("#")[0]
            if href.startswith("/"):
                href = src["base"] + href
            if href in gesehen:
                continue
            gesehen.add(href)
            # Kachel: das Elternelement, das Preis oder "keine Angebote" traegt
            el = a
            text = ""
            for _ in range(5):
                if el is None:
                    break
                t = el.get_text(" ", strip=True)
                if "€" in t or "keine Angebote" in t:
                    text = t
                    break
                el = el.parent
            if "keine Angebote" in text:
                hits.append((titel[:180], href, 0.0, "wartet"))
                continue
            preis = _preis_aus_text(text)
            if preis and preis > price_cap(titel) and not sonderfall(titel):
                continue          # nur Reseller-Preise, kein Retailer aktiv
            if preis and preis < MIN_PREIS:
                continue
            hits.append((titel[:180], href, preis, ""))
        time.sleep(1)
    return hits


def check_shopify(src: dict) -> list:
    """Shopify-Katalog als JSON statt HTML.

    Vorteil gegenueber dem Scraper: Preis und Verfuegbarkeit stehen sauber
    drin, statt aus dem Layout geraten zu werden, und die Liste ist nach
    zuletzt geaendert sortiert. Neue Ware, Restocks und Raffles stehen damit
    oben, ohne dass ich eine bestimmte Kategorieseite treffen muss.
    """
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
        teuer = 0
        for p in produkte:
            titel = " ".join(str(p.get("title", "")).split())
            if not titel:
                continue
            # WICHTIG: die drei Wege weiter unten sind mit ODER verknuepft und
            # umgehen is_relevant. Die harten Sperren muessen deshalb HIER
            # nochmal stehen, sonst greifen sie im Shopify-Katalog nicht.
            if ist_fremdes_tcg(titel) or ist_chinesisch(titel) or ist_konvolut(titel):
                continue
            # Drei Wege hinein, bewusst mit ODER verknuepft:
            #   1. der Titel selbst (Normalfall)
            #   2. die Nebenfelder, falls der Titel verfremdet ist
            #   3. frisch angelegt, dann zaehlt der Name gar nicht mehr
            signal = signal_text(p)
            pokemon = any(w in signal for w in POKEMON_WOERTER)
            frisch = ist_neuling(p)
            # Erste Huerde: irgendein Pokemon-Bezug muss her, entweder ueber
            # is_relevant (Titel) oder ueber die Nebenfelder des Produkts
            # (product_type, tags, vendor). Gemessen am 27.08.2026 tragen die
            # Pokemon-Shops diesen Bezug fast immer: ChiefCards 231 von 250,
            # CardCosmos 247 von 250, Kartenbasis 250 von 250.
            #
            # Der frueher hier stehende dritte Weg "frisch angelegt, Name egal"
            # ist RAUS. Er sollte getarnte Drops fangen, holte aber Carcassonne,
            # Warhammer und Playmobil herein, weil "box" und "deck" als
            # TCG-Hinweis zaehlen. Als Notausgang bleibt nur die Form, die ein
            # getarnter Drop wirklich hat: Jubilaeum, Raffle oder Live-Drop.
            notausgang = frisch and (ist_jubilaeum(titel) or sonderfall(titel))
            if not (is_relevant(titel, src) or pokemon or notausgang):
                continue
            # EXCLUDE greift hier nur, wenn es versiegelte Ware ist. Bewusst
            # NICHT TCG_HINWEISE: darin stehen "karten" und "sammelkarten", und
            # damit rettet sich jede Kartenhuelle selbst ("Sleeves fuer
            # Sammelkarten wie Pokemon"). Gefunden am 26.08.2026 im Livelauf.
            # Sonst faellt die "30th Celebration Mew Figuren Kollektion" wegen
            # des Wortes "Figuren" raus, obwohl es versiegelte Kartenware ist.
            tl = titel.lower()
            if any(x in tl for x in EXCLUDE_HART):
                continue
            if any(x in tl for x in EXCLUDE) and not any(h in tl for h in VERSIEGELT_HINWEISE):
                continue
            # Zweite Huerde, auf ALLEN Wegen: nur versiegelte Ware. Ohne sie
            # kam ueber den Nebenfeld-Weg der komplette Einzelkarten-Bestand
            # herein, inklusive "Virizion - EN - 97/101 - PSA Slab - 9 Mint".
            if not ist_versiegelt(tl) and not sonderfall(tl):
                continue
            # Ein Neuling ohne jeden Pokemon-Hinweis ist nur ein Verdacht, kein
            # sicherer Treffer. Ihn trotzdem zu melden ist richtig: lieber ein
            # Fehlalarm zu viel als ein getarnter Drop zu wenig.
            verdacht = notausgang and not pokemon
            varianten = p.get("variants") or []
            lieferbar = [v for v in varianten if v.get("available")]
            try:
                preis = min(float(v.get("price") or 0)
                            for v in (lieferbar or varianten)) if varianten else 0.0
            except (ValueError, TypeError):
                preis = 0.0
            link = f"{src['base']}/products/{p.get('handle', '')}"

            if sonderfall(titel):
                # Bei Raffles zaehlt die Frist mehr als der Preis: die Anmeldung
                # laeuft ab, lange bevor die Ware weg ist. Sie steht in der
                # Produktbeschreibung, die im Katalog-Feed schon mitkommt.
                frist = frist_aus_text(str(p.get("body_html") or ""))
                marke = f"{titel} [FRIST {frist}]" if frist else titel
                hits.append((marke[:180], link, preis, ""))
                continue
            # Ausverkaufte Ware IMMER auf die Wache nehmen, auch wenn der
            # aktuelle Preis ueber der UVP-Grenze liegt. Sonst faellt genau das
            # Produkt raus, auf dessen Restock ich warte: der Preisfilter
            # gehoert an den Alarm, nicht an das Gedaechtnis. (Aufgefallen am
            # 10.08.2026: 6 von 10 Artikeln der 30-Jahre-Kollektion bei
            # Feenturm standen deshalb unter keinerlei Beobachtung.)
            if not lieferbar:
                hits.append((titel[:180], link, preis, "wartet"))
                continue
            # Frisch angelegte Ware nie am Preis abweisen: gerade bei getarnten
            # Eintraegen ist der Preis oft noch ein Platzhalter.
            if preis and preis > price_cap(titel) and not frisch:
                # Kernwache (30-Jahre-Kollektionen): nie stillschweigend
                # verwerfen. Diese Artikel will ich sehen, auch wenn der Preis
                # ueber UVP liegt, dann eben ehrlich als zu teuer markiert.
                if src.get("kernwache"):
                    hits.append((titel[:180], link, preis, "frisch_teuer"))
                    continue
                teuer += 1
                continue
            # Nicht lieferbar wird als "wartet" gemerkt, nicht gemeldet. Sobald
            # der Shop auf lieferbar dreht, feuert daraus automatisch ein Restock.
            if not lieferbar:
                status = "wartet"
            elif frisch and preis and preis > price_cap(titel):
                # Durchgelassen, weil frisch, aber ehrlich als zu teuer markiert:
                # ich will den Drop sehen und selbst entscheiden, statt ihn zu
                # verpassen oder ihn faelschlich fuer einen Retail-Preis zu halten.
                status = "frisch_teuer"
            elif verdacht:
                status = "verdacht"
            elif frisch:
                status = "neuling"
            else:
                status = ""
            hits.append((titel[:180], link, preis, status))
        if teuer:
            print(f"[{src['name']}] {teuer} Treffer ueber UVP-Grenze verworfen")
        time.sleep(1)
    return hits


def check_source(src: dict) -> list:
    """Gibt Liste von (titel, url, preis, status) relevanter Treffer zurueck."""
    return reseller_filter(src, _check_source(src))


def _check_source(src: dict) -> list:
    if src.get("parser") == "jsonld":
        return check_jsonld(src)
    if src.get("parser") == "shopify":
        return check_shopify(src)
    if src.get("parser") == "smyths":
        return check_smyths(src)
    if src.get("parser") == "geizhals":
        return check_geizhals(src)
    hits = []
    for url in src["urls"]:
        html = fetch(url, src["name"])
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")
        too_expensive = 0
        for a in soup.select(src["item"]):
            title = " ".join(a.get_text(" ", strip=True).split())
            href = a.get("href", "")
            if not title or not href:
                continue
            if not is_relevant(title, src):
                continue
            if href.startswith("/"):
                href = src["base"] + href
            status = extract_status(a)
            price = extract_price(a)
            # Raffles und Live-Drops nie wegfiltern: dort ist der Preis oft ein
            # Losbeitrag oder gar keiner, und "Verkauf startet" ist genau die
            # Nachricht, die ich hoeren will, nicht ein Grund zu schweigen.
            if sonderfall(title):
                hits.append((title[:180], href, price, ""))
                continue
            if price and price > price_cap(title):
                too_expensive += 1
                continue
            if price and price < MIN_PREIS:
                continue          # Einzelbooster
            if not price and src.get("require_price"):
                continue
            hits.append((title[:180], href, price, status))
        if too_expensive:
            print(f"[{src['name']}] {too_expensive} Treffer ueber UVP-Grenze verworfen")
        time.sleep(1)

    # Duplikate innerhalb einer Seite entfernen
    seen_local = set()
    unique = []
    for t, u, p, s in hits:
        if u in seen_local:
            continue
        seen_local.add(u)
        unique.append((t, u, p, s))
    return unique


# ---------------------------------------------------------------- Hauptlauf

HOT_PHASE_START = "2026-09-10"  # ab hier laeuft jeder Trigger voll durch
THROTTLE_MINUTES = 15           # Mindestabstand vor der Hot Phase


def throttled() -> bool:
    """Vor der Hot Phase: True, wenn der letzte Lauf < THROTTLE_MINUTES her ist."""
    from datetime import date, datetime, timezone

    if date.today().isoformat() >= HOT_PHASE_START:
        return False
    if EILIG_ONLY:
        return False        # der Schnellpass soll gerade dicht laufen
    marker = Path("last_run.txt")
    now = datetime.now(timezone.utc)
    if marker.exists():
        try:
            last = datetime.fromisoformat(marker.read_text().strip())
            if (now - last).total_seconds() < THROTTLE_MINUTES * 60:
                return True
        except Exception:
            pass
    marker.write_text(now.isoformat())
    return False


def main() -> int:
    if throttled():
        print("--> Drosselung aktiv (vor Hot Phase), Lauf uebersprungen")
        return 0

    seen = load_state()
    new_items = []

    for src in SOURCES:
        hits = check_source(src)
        print(f"[{src['name']}] {len(hits)} relevante Treffer")
        for title, url, price, status in hits:
            fp = fingerprint(src["name"], title, url)
            vorher = seen.get(fp)
            jetzt = "wartet" if status == "wartet" else "kaufbar"
            if jetzt == "wartet":
                seen[fp] = "wartet"      # still beobachten, kein Alert
                continue
            if vorher == "kaufbar":
                continue                 # unverändert lieferbar, kein neuer Alert
            # neu ODER Wechsel wartet/ausverkauft -> kaufbar (Restock!)
            if vorher == "wartet":
                status = "restock"
            # Schnellpass: nur Eiliges melden (Raffle, Live-Drop, frisch
            # angelegte Ware). Alles andere wird BEWUSST nicht als gesehen
            # markiert, damit der normale Lauf es spaeter noch meldet. Sonst
            # wuerde der Schnellpass die regulaeren Treffer verschlucken.
            if EILIG_ONLY and not (
                sonderfall(title)
                or status in ("neuling", "verdacht", "frisch_teuer")
            ):
                continue
            new_items.append((src["name"], title, url, price, status, fp))
        time.sleep(2)  # hoeflich bleiben

    if new_items:
        # Prio-Treffer zuerst
        # Raffles und Live-Drops ganz nach oben: die laufen nach Stunden ab,
        # alles andere kann warten.
        # Seit 02.09.2026 steht Jubilaeumsware NICHT mehr pauschal ueber allem:
        # eine Mega-Entwicklung-ETB zum Retail-Preis ist genauso Geld wie eine
        # 30-Jahre-Tin. Das 🎂-Zeichen bleibt als Hinweis, sortiert wird nach
        # Dringlichkeit (Raffle, Restock, Neuling) und Produktwert (Prio).
        new_items.sort(key=lambda x: (
            not sonderfall(x[1]),
            x[4] not in ("neuling", "verdacht", "restock"),
            not is_prio(x[1]),
            x[0],
        ))
        from html import escape
        from urllib.parse import quote_plus

        def resell_links(title: str) -> str:
            q = re.sub(r"\(.*?\)", " ", title)
            q = " ".join(q.split()[:7])
            ebay = "https://www.ebay.de/sch/i.html?LH_Sold=1&LH_Complete=1&_nkw=" + quote_plus(q)
            cm = "https://www.cardmarket.com/de/Pokemon/Products/Search?searchString=" + quote_plus(q)
            return (
                f'<a href="{escape(ebay, quote=True)}">eBay-VK</a> · '
                f'<a href="{escape(cm, quote=True)}">Cardmarket</a>'
            )

        lines = ["<b>Pokémon – neue Treffer</b>", ""]
        # Bewertungs-Knoepfe: hoechstens fuer die ersten acht Treffer, sonst
        # wird die Tastatur unter der Nachricht laenger als die Nachricht.
        knoepfe = []
        gemeldet = lade_gemeldet()
        nummer = 0
        for shop, title, url, price, status, _fp in new_items:
            flag = "🎂 30 JAHRE · " if ist_jubilaeum(title) else ""
            if is_prio(title):
                flag += "🔥 "
            if status == "restock":
                flag = "🚨 WIEDER DA · " + flag
            elif status == "neuling":
                flag = "🆕 GERADE ANGELEGT · " + flag
            elif status == "verdacht":
                flag = "🕵️ GERADE ANGELEGT, TARNNAME? · " + flag
            elif status == "frisch_teuer":
                flag = "🆕 GERADE ANGELEGT · ⚠️ ÜBER UVP · " + flag
            if ist_raffle(title):
                flag = "🎟️ RAFFLE · SOFORT ANMELDEN · " + flag
            elif ist_live(title):
                flag = "⏱️ LIVE-DROP · " + flag
            tag = f"{price:.2f} €".replace(".", ",") if price else "Preis?"
            if status == "vorbestellbar":
                tag += " · Vorbestellung"
            elif status == "unklar":
                tag += " · Lagerstatus ungeprüft"
            wert = ""
            try:
                import marktwert
                wert = marktwert.bewertung(title, price) + "\n"
            except Exception as e:
                print(f"[Marktwert] übersprungen: {e}")
            nummer += 1
            marke = ""
            if nummer <= MAX_BEWERTBAR:
                marke = f"<code>[{nummer}]</code> "
                knoepfe.append([
                    # Die Nummer steht mit im callback_data, damit feedback.py
                    # den Titel notfalls aus dem Nachrichtentext lesen kann.
                    # Das rettet die Zuordnung fuer die vier Shops, die lokal
                    # laufen und deren gemeldet.json nie in der Cloud landet.
                    {"text": f"👍 {nummer}", "callback_data": f"g:{_fp}:{nummer}"},
                    {"text": f"👎 {nummer}", "callback_data": f"s:{_fp}:{nummer}"},
                ])
                gemeldet[_fp] = {
                    "shop": shop,
                    "titel": title,
                    "url": url,
                    "wann": time.strftime("%Y-%m-%d %H:%M"),
                }
            lines.append(
                f"{marke}{flag}<b>{shop}</b> · {tag} · <a href=\"{escape(url, quote=True)}\">LINK</a>\n"
                f"{escape(title)}\n"
                f"{wert}"
                f"↳ Resell: {resell_links(title)}\n"
            )
        if nummer > MAX_BEWERTBAR:
            lines.append(f"<i>Bewerten geht fuer die ersten {MAX_BEWERTBAR} Treffer.</i>\n")
        if notify("\n".join(lines), knoepfe or None):
            speichere_gemeldet(gemeldet)
            # Erst nach erfolgreichem Versand als erledigt merken, sonst gehen
            # Treffer bei einem Telegram-Fehler dauerhaft verloren.
            for *_rest, fp in new_items:
                seen[fp] = "kaufbar"
            print(f"--> {len(new_items)} neue Treffer gemeldet")
            # Titel mitschreiben, nicht nur die Anzahl. Am 26.08.2026 kam eine
            # Konvolut-Meldung durch und liess sich hinterher nicht mehr
            # zuordnen, weil im Log nur "22 neue Treffer" stand.
            for eintrag in new_items:
                quelle, titel = eintrag[0], eintrag[1]
                print(f"    gemeldet: [{quelle}] {titel[:110]}")
        else:
            print(f"--> Versand fehlgeschlagen, {len(new_items)} Treffer bleiben offen")
    else:
        print("--> nichts Neues")

    save_state(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
