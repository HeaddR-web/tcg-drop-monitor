#!/usr/bin/env python3
"""
Marktwert-Nachschlag für Alerts: echte Sekundärmarkt-Preise statt nur Links.

Quelle: offizieller Cardmarket-Preisdump (frei, ohne Key, ausdrücklich zum
Download bereitgestellt). Deckt ausschliesslich Pokemon ab (Ansage 23.08.2026,
vorher waren Magic, Yu-Gi-Oh, One Piece und Lorcana mit drin).
Andere Kategorien (LEGO, Whisky) brauchen einen API-Key und bleiben vorerst beim
Klick-Link, siehe DROP-RADAR.md.

Wird nur geladen, wenn es wirklich neue Treffer gibt (der Dump ist ~15 MB).
"""

import json
import os
import re
from pathlib import Path

import requests

CACHE_DIR = Path(os.environ.get("MARKTWERT_CACHE", "/tmp/marktwert-cache"))

# Cardmarket-Spiele-IDs
GAMES = {
    "pokemon": 6,
}

BASE = "https://downloads.s3.cardmarket.com/productCatalog"

# Deutsche Shop-Titel auf die englischen Cardmarket-Namen bringen
SYNONYMS = [
    # Deutsche Haendlernamen auf die englischen Katalognamen bringen.
    # Ohne diese Zeilen fand "Erste Partner Illustrations-Kollektion" den
    # Katalogeintrag "First Partner Illustration Collection" nicht und der
    # Alert zeigte "ohne Datenbasis" statt des echten Werts (09.08.2026).
    ("erste partner illustrations-kollektion", "first partner illustration collection"),
    ("erste-partner-illustrations-kollektion", "first partner illustration collection"),
    ("erste partner illustrations", "first partner illustration"),
    ("erste partner", "first partner"),
    ("illustrations-kollektion", "illustration collection"),
    ("serie ", "series "),
    ("top-trainer-box", "elite trainer box"),
    ("top trainer box", "elite trainer box"),
    ("top-trainer box", "elite trainer box"),
    ("30 jahre", "30th celebration"),
    ("erhabene helden", "ascendant heroes"),
    ("fatale flammen", "phantasmal flames"),
    ("mega-entwicklung", "mega evolution"),
    ("boosterbundle", "booster bundle"),
    ("mini-tin-box", "mini tin"),
    ("mini-tin", "mini tin"),
    ("kollektion", "collection"),
    ("sammelkartenspiel", ""),
    ("pokémon", "pokemon"),
]

STOPWORDS = {"de", "en", "der", "die", "das", "und", "mit", "the", "of", "tcg",
             "pokemon", "englisch", "deutsch", "karten", "sammelkarte"}

_cache = {}


def _load(game_id: int):
    """Lädt Produktliste + Preisliste eines Spiels (mit Tagescache auf Platte)."""
    if game_id in _cache:
        return _cache[game_id]
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {}
    for kind, url in (
        ("products", f"{BASE}/productList/products_nonsingles_{game_id}.json"),
        ("prices", f"{BASE}/priceGuide/price_guide_{game_id}.json"),
    ):
        f = CACHE_DIR / f"{kind}_{game_id}.json"
        try:
            # Cache max. 24 h alt, sonst zeigt der Alert veraltete Preise an
            stale = True
            if f.exists():
                import time as _t
                stale = (_t.time() - f.stat().st_mtime) > 86400
            if stale:
                r = requests.get(url, headers={"User-Agent": "drop-radar/1.0"}, timeout=60)
                r.raise_for_status()
                f.write_bytes(r.content)
            data[kind] = json.loads(f.read_text())
        except Exception as e:
            print(f"[Marktwert] {kind} {game_id} nicht ladbar: {e}")
            return None
    prices = {g["idProduct"]: g for g in data["prices"].get("priceGuides", [])}
    index = []
    for p in data["products"].get("products", []):
        pid = p.get("idProduct")
        g = prices.get(pid)
        if not g:
            continue
        val = g.get("trend") or g.get("avg") or g.get("low")
        if not val:
            continue
        index.append((_tokens(p.get("name", "")), p.get("name", ""), pid, g))
    _cache[game_id] = index
    return index


def _tokens(text: str) -> set:
    t = text.lower()
    for a, b in SYNONYMS:
        t = t.replace(a, b)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return {w for w in t.split() if len(w) > 1 and w not in STOPWORDS}


def lookup(title: str, game: str = "pokemon"):
    """Bester Namens-Treffer im Cardmarket-Katalog. Gibt None zurück, wenn unsicher."""
    gid = GAMES.get(game)
    if not gid:
        return None
    index = _load(gid)
    if not index:
        return None
    want = _tokens(title)
    if len(want) < 2:
        return None
    # Jaccard: bestraft fehlende UND überschüssige Wörter. Ohne das matcht
    # "Booster Bundle" auf den Einzel-"Booster" (7 € statt 25 €), also falsch.
    best, best_score = None, 0.0
    for toks, name, pid, g in index:
        if not toks:
            continue
        overlap = len(want & toks)
        if overlap < 2:
            continue
        score = overlap / len(want | toks)
        if score > best_score:
            best, best_score = (name, pid, g), score
    # Streng: im Zweifel lieber KEIN Wert als ein falscher.
    if not best or best_score < 0.75:
        return None
    name, pid, g = best
    return {
        "name": name,
        "trend": g.get("trend"),
        "avg": g.get("avg"),
        "low": g.get("low"),
        "url": f"https://www.cardmarket.com/de/Pokemon/Products/Singles?idProduct={pid}",
    }


def guess_game(title: str) -> str:
    """Es gibt nur noch einen Katalog: Pokemon (Ansage 23.08.2026)."""
    return "pokemon"


# Historische Wertentwicklung je Produkttyp (aus der markt-firma-Recherche 07/2026).
# Wird genutzt, wenn KEIN exakter Marktwert vorliegt: dann lieber eine ehrlich als
# Schätzung markierte Spanne als gar keine Orientierung.
HIST = [
    # (Erkennungswort, Multiplikator-Spanne auf den Einkaufspreis, Zeithorizont)
    ("ultra premium", (2.0, 4.0), "2-3 J"),
    ("ultra-premium", (2.0, 4.0), "2-3 J"),
    ("elite trainer", (1.5, 2.8), "1-2 J nach Druckende"),
    ("top-trainer", (1.5, 2.8), "1-2 J nach Druckende"),
    ("top trainer", (1.5, 2.8), "1-2 J nach Druckende"),
    ("display", (1.6, 3.0), "2-3 J"),
    ("booster bundle", (1.3, 2.2), "1-2 J"),
    ("collector booster", (1.4, 2.5), "Wochen bis 2 J"),
    ("single cask", (1.3, 2.5), "2-5 J"),
    ("cask strength", (1.2, 1.8), "2-5 J"),
    # Chronisch unterallokierte Marken: verkaufen sich zum Ladenpreis sofort aus
    ("springbank", (1.3, 1.9), "Tage-Wochen"),
    ("longrow", (1.25, 1.8), "Tage-Wochen"),
    ("hazelburn", (1.25, 1.8), "Tage-Wochen"),
    ("kilkerran", (1.2, 1.7), "Tage-Wochen"),
    ("daftmill", (1.5, 2.5), "Tage-Wochen"),
    # Geschlossene Destillerien: Nachschub endet für immer
    ("port ellen", (1.8, 3.0), "3-10 J"),
    ("brora", (1.8, 3.0), "3-10 J"),
    ("caroni", (1.6, 2.8), "3-10 J"),
    ("rosebank", (1.6, 2.6), "3-10 J"),
    # Rum-Prestige
    ("velier", (1.4, 2.2), "1-3 J"),
    ("foursquare", (1.3, 2.0), "1-3 J"),
    ("hampden", (1.25, 1.8), "1-3 J"),
    ("batch", (1.15, 1.5), "1-2 J"),
    ("ucs", (1.8, 2.6), "2-4 J nach EOL"),
    ("ultimate collector", (1.8, 2.6), "2-4 J nach EOL"),
    ("modular", (1.8, 2.6), "2-4 J nach EOL"),
    ("icons", (1.3, 1.8), "2-4 J nach EOL"),
    # Gaming-Sammlereditionen und Vinyl (Recherche: Gewinner deutlich, Masse flach)
    ("collector's edition", (1.2, 1.9), "Monate bis 2 J"),
    ("collectors edition", (1.2, 1.9), "Monate bis 2 J"),
    ("limited run", (1.2, 1.8), "Monate bis 2 J"),
    ("signed", (1.3, 2.0), "1-2 J"),
    ("signiert", (1.3, 2.0), "1-2 J"),
    ("boxset", (1.2, 1.8), "1-2 J"),
    ("box set", (1.2, 1.8), "1-2 J"),
    ("picture disc", (1.15, 1.6), "1-2 J"),
    ("coloured vinyl", (1.15, 1.6), "1-2 J"),
    ("limited", (1.2, 2.0), "1-3 J"),
    ("limitiert", (1.2, 2.0), "1-3 J"),
    ("exclusive", (1.15, 1.6), "1-2 J"),
    ("exklusiv", (1.15, 1.6), "1-2 J"),
]

# Wenn gar nichts greift: Minimalannahme, damit JEDE Nachricht ein Urteil hat.
# Bewusst so niedrig, dass sie fast immer "LOHNT NICHT" ergibt. Das ist die
# ehrliche Aussage: ohne belegten Marktwert ist es kein Deal, sondern Recherche.
FALLBACK_FAKTOR = 1.15


def estimate(title: str, einkauf: float) -> str:
    """Grobe Schätzung aus historischen Bandbreiten, wenn kein exakter Wert da ist.
    Bewusst als Schätzung gekennzeichnet, damit sie nicht mit echten Preisen verwechselt wird."""
    if not einkauf or einkauf <= 0:
        return ""
    t = title.lower()
    for key, (lo, hi), horizont in HIST:
        if key in t:
            return (
                f"📈 Schätzung (Historie, kein Live-Preis): {einkauf*lo:.0f}-{einkauf*hi:.0f} €"
                f" in {horizont}, also etwa {(lo-1)*100:+.0f} bis {(hi-1)*100:+.0f}%"
            ).replace(".", ",")
    return ""


# --- Offizielle UVP-Referenz --------------------------------------------------
# Pokémon veröffentlicht keine zentrale UVP-Liste. Diese Werte sind die
# verifizierten Erstverkäufer-Preise (Amazon direkt, Müller, GameStop) aus
# unseren eigenen Crawls, Stand Juli 2026. Reihenfolge: speziellste zuerst.
UVP_LISTE = [
    # Speziellste zuerst! "erste partner ... kollektion" muss VOR "kollektion"
    # stehen, sonst gewinnt die generische 59,99-Regel gegen den echten UVP 17,99
    # und ein Reseller-Preis von 60 Euro rutscht als "im Rahmen" durch (09.08.2026).
    ("erste partner illustrations-kollektion", 17.99),
    ("erste partner illustration", 17.99),
    ("first partner illustration", 17.99),
    ("partner illustrations-kollektion", 17.99),
    ("first partner card set", 17.99),
    ("partner special card set", 17.99),
    ("knock out collection", 14.99),
    ("knock-out-kollektion", 14.99),
    ("ultra premium", 129.99),
    ("ultra-premium", 129.99),
    ("top-trainer", 54.99),
    ("top trainer", 54.99),
    ("elite trainer", 54.99),
    ("booster bundle", 31.99),
    ("boosterbundle", 31.99),
    ("mini-tin", 10.99),
    ("mini tin", 10.99),
    ("deluxe-pin", 29.99),
    ("tin-box", 24.99),
    ("tin box", 24.99),
    ("blister", 15.99),
    ("kampfdeck", 16.99),
    ("battle deck", 16.99),
    ("premium kollektion", 59.99),
    ("premium collection", 59.99),
    ("36 booster", 159.99),
    ("display", 159.99),
]


# Mehrfach-Gebinde: ein "10 Elite Trainer Box Case" kostet nicht einmal, sondern
# zehnmal den Einzel-UVP. Ohne diese Korrektur zeigt der Alert Fantasie-Margen.
GEBINDE = [
    (re.compile(r"(\d+)\s*(?:x\s*)?elite trainer box case", re.I), "elite trainer"),
    (re.compile(r"(\d+)[\s-]*pack", re.I), None),          # z.B. "8-Pack Mini Tins"
    (re.compile(r"\bcase\b", re.I), None),                 # Case ohne Zahl: konservativ 6
]
# Display-Inhalte (Standard-Packungsgrößen des Herstellers)
DISPLAY_INHALT = [
    ("mini tin display", "mini tin", 10),
    ("booster bundle display", "booster bundle", 10),
    ("build & battle box display", "build & battle", 10),
    ("build and battle box display", "build & battle", 10),
    ("tech sticker collection display", "tech sticker", 10),
    ("blister display", "blister", 12),
]
UVP_EINZEL = {
    "elite trainer": 54.99, "mini tin": 10.99, "booster bundle": 31.99,
    "build & battle": 24.99, "tech sticker": 29.99, "blister": 15.99,
}


def uvp_ref(title: str):
    t = title.lower()

    # 1) Display mit bekannter Packungsgröße
    for key, einzel, anzahl in DISPLAY_INHALT:
        if key in t:
            return round(UVP_EINZEL[einzel] * anzahl, 2)

    # 2) Case oder Mehrfachpack mit Stückzahl im Titel
    m = re.search(r"(\d+)\s*(?:x\s*)?elite trainer box case", t)
    if m:
        return round(UVP_EINZEL["elite trainer"] * int(m.group(1)), 2)
    m = re.search(r"(\d+)[\s-]*pack\b", t)
    if m:
        for key, uvp in UVP_LISTE:
            if key in t:
                return round(uvp * int(m.group(1)), 2)
    if "case" in t:
        for key, uvp in UVP_LISTE:
            if key in t:
                return round(uvp * 6, 2)      # konservativ, Cases sind meist 6 bis 12

    # 3) Einzelprodukt
    for key, uvp in UVP_LISTE:
        if key in t:
            return uvp
    return None


# --- Einheitliche Bewertung: Retail vs. Resell in JEDER Nachricht -------------
GEBUEHR = 0.12      # Verkaufsprovision, grob (eBay; Cardmarket wäre günstiger)
VERSAND = 6.0       # Paketversand
MIN_MARGE = 15.0    # darunter lohnt der Aufwand nicht


def bewertung(title: str, retail: float, game: str = "pokemon") -> str:
    """Immer eine Zeile mit Einkauf, Marktwert und Netto-Ergebnis.
    Beantwortet die einzige Frage, die zählt: lohnt es sich oder nicht.

    WICHTIG (Lehre aus dem Fall Nihil Zero M3, 26.07.2026):
    Ein gruenes "LOHNT" darf NUR erscheinen, wenn der Marktwert wirklich
    gemessen ist (Cardmarket-Treffer). Bei geschaetzten Werten steht immer
    "PRUEFEN", egal wie gut die Rechnung aussieht. Sonst kauft man auf Basis
    einer Multiplikation statt auf Basis von Daten.
    """
    v = lookup(title, game)
    wert, quelle, warnung = None, "", ""
    gemessen = False                      # True nur bei echtem Cardmarket-Treffer
    if v:
        trend = v.get("trend") or v.get("avg")
        low = v.get("low")
        # Wer schnell verkaufen will, muss gegen das GÜNSTIGSTE Angebot antreten,
        # nicht gegen den Trendpreis. Sonst rechnet man sich reich.
        if trend and low and low < trend * 0.85:
            wert = low
            quelle = "Cardmarket, günstigstes Angebot"
            warnung = f" (Trend {trend:.0f} €, aber Angebote ab {low:.0f} €)".replace(".", ",")
            gemessen = True
        else:
            wert = trend
            quelle = "Cardmarket"
            gemessen = bool(trend)
    if not wert:
        # historische Bandbreite als Ersatz, Mittelwert für die Rechnung
        t = title.lower()
        for key, (lo, hi), _horizont in HIST:
            if key in t and retail:
                # Bewusst der UNTERE Rand: eine zu optimistische Schätzung kostet
                # echtes Geld, eine zu vorsichtige nur einen verpassten Deal.
                wert = retail * lo
                quelle = f"Schätzung, vorsichtig ({lo:.1f}x)".replace(".", ",")
                break
    if not retail:
        if wert:
            return f"💰 Marktwert ~{wert:.0f} € ({quelle}) · Einkaufspreis unbekannt".replace(".", ",")
        return "❓ Kein Preis und kein Marktwert: über die Resell-Links selbst prüfen"
    # Plausibilitätssperre: liegt der gefundene Marktwert absurd unter dem
    # Einkaufspreis, ist es fast immer ein Fehl-Match (Einzel-Booster statt
    # Display). Dann lieber ehrlich "unbekannt" als eine erfundene Zahl.
    if wert and retail and wert < retail * 0.3:
        wert, quelle, warnung, gemessen = None, "", "", False

    if not wert:
        wert = retail * FALLBACK_FAKTOR
        quelle = "ohne Datenbasis, Minimalannahme"
        gemessen = False

    netto = wert * (1 - GEBUEHR) - VERSAND - retail
    if not gemessen:
        # Geschaetzt heisst geschaetzt. Kein gruenes Signal auf einer Annahme.
        symbol, urteil = "🔍", "UNGEPRÜFT"
        warnung += "\n   ⚠️ Marktwert ist GESCHÄTZT, nicht gemessen. Vor dem Kauf" \
                   " über die Resell-Links gegenprüfen."
    elif netto >= MIN_MARGE * 2:
        symbol, urteil = "✅", "LOHNT"
    elif netto >= MIN_MARGE:
        symbol, urteil = "🟡", "knapp"
    else:
        symbol, urteil = "❌", "LOHNT NICHT"

    # UVP-Referenz: zeigt, ob der Shop-Preis wirklich Retail ist oder Aufschlag
    uvp = uvp_ref(title) if game != "keine" else None
    uvp_teil = ""
    if uvp:
        if retail > uvp * 1.05:
            uvp_teil = f" · ⚠️ über UVP ({uvp:.2f} €)".replace(".", ",")
        else:
            uvp_teil = f" · UVP {uvp:.2f} €".replace(".", ",")

    return (
        f"{symbol} <b>{urteil}</b> · Einkauf {retail:.0f} €{uvp_teil} → Resell ~{wert:.0f} € ({quelle})\n"
        f"   Netto nach Gebühren und Versand: <b>{netto:+.0f} €</b>{warnung}"
    )


def format_value(title: str, einkauf: float, game: str = "pokemon") -> str:
    """Marktwert-Zeile für den Alert. Exakter Cardmarket-Preis wenn möglich,
    sonst eine als Schätzung gekennzeichnete historische Bandbreite."""
    v = lookup(title, game)
    if not v:
        return estimate(title, einkauf)
    wert = v.get("trend") or v.get("avg")
    if not wert:
        return estimate(title, einkauf)
    line = f"💰 Marktwert {wert:.2f} €".replace(".", ",") + " (Cardmarket-Trend"
    if v.get("low"):
        line += f", ab {v['low']:.2f}".replace(".", ",") + " €"
    line += ")"
    if einkauf and einkauf > 0:
        delta = wert - einkauf
        pct = delta / einkauf * 100
        line += f"\n   → Spanne {delta:+.0f} € ({pct:+.0f}%) gegen {einkauf:.2f} €".replace(".", ",")
    return line


if __name__ == "__main__":
    import sys
    t = " ".join(sys.argv[1:]) or "Pokémon 30 Jahre Top-Trainer-Box"
    print(t)
    print(format_value(t, 52.99))
