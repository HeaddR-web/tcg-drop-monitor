#!/usr/bin/env python3
"""Rechnet aus, wie viel ECHTE Ueberwachungszeit eine Betriebsluecke gekostet hat.

Der Waechter im local-monitor.sh darf nicht stumpf die Stunden seit dem letzten
Lauf zaehlen. Sonst schlaegt er jeden Morgen Alarm, obwohl die Nachtpause von
0 bis 7 Uhr gewollt ist. Umgekehrt darf er eine Nacht auch nicht pauschal
entschuldigen: wenn der Mac schon um 22:25 zuklappt und erst um 08:25 aufgeht,
sind drei Stunden aktives Fenster verloren, und genau das soll gemeldet werden.

Das Fenster endet um 22 Uhr, weil der Rechner abends ohnehin zugeht. Was
danach passiert, haette sie auch mit einem laufenden Monitor nicht mitbekommen.

Aufruf:  luecke.py <epoch-des-letzten-laufs>
Ausgabe: verpasste Minuten im aktiven Fenster (7 bis 22 Uhr Ortszeit)
"""
import sys
import time
from datetime import datetime, timedelta

AKTIV_VON = 7   # ab dieser Stunde wird ueberwacht
AKTIV_BIS = 22  # bis dahin (der echte Tagesrhythmus, Ansage 26.08.2026)
MAX_TAGE = 30   # Sicherheitsnetz gegen kaputte Zeitstempel


def verpasste_minuten(prev_epoch: float, now_epoch: float) -> int:
    if now_epoch <= prev_epoch:
        return 0
    if now_epoch - prev_epoch > MAX_TAGE * 86400:
        prev_epoch = now_epoch - MAX_TAGE * 86400

    start = datetime.fromtimestamp(prev_epoch).replace(second=0, microsecond=0)
    ende = datetime.fromtimestamp(now_epoch).replace(second=0, microsecond=0)

    minuten = 0
    t = start
    while t < ende:
        if AKTIV_VON <= t.hour < AKTIV_BIS:
            minuten += 1
        t += timedelta(minutes=1)
    return minuten


def main() -> int:
    if len(sys.argv) < 2:
        print(0)
        return 0
    try:
        prev = float(sys.argv[1])
    except ValueError:
        print(0)
        return 0
    if prev <= 0:
        print(0)
        return 0
    print(verpasste_minuten(prev, time.time()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
