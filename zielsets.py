"""Liest zielsets.txt: die Liste der gewollten Sets und Produktlinien.

Warum eine Textdatei: die Sets standen bis 02.09.2026 tief im Code, und beim
Aufraeumen am 23.08. sind die laufenden Sets komplett rausgeflogen. Eine Datei,
die von Hand gepflegt wird, kann nicht "aus Versehen beim Refactoring" leer
werden, und sie sieht auf einen Blick, was der Monitor ueberhaupt sucht.
"""

from pathlib import Path

DATEI = Path(__file__).with_name("zielsets.txt")


def lade_zielsets() -> list:
    woerter = []
    if not DATEI.exists():
        print(f"[WARN] {DATEI} fehlt, Zielsets leer")
        return woerter
    for zeile in DATEI.read_text(encoding="utf-8").splitlines():
        wort = zeile.split("#", 1)[0].strip().lower()
        if wort and wort not in woerter:
            woerter.append(wort)
    return woerter


if __name__ == "__main__":
    for w in lade_zielsets():
        print(w)
