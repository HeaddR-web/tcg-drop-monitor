# TCG Drop Monitor

Ein Melder für Sammelkarten-Drops. Er beobachtet Shops und News-Quellen,
erkennt neue oder wieder lieferbare versiegelte Ware und schickt eine
Telegram-Nachricht. Er kauft nichts. Die Kaufentscheidung bleibt beim
Menschen, bewusst.

Gebaut für Pokemon. Die Wortlisten sind austauschbar, die Mechanik nicht
spielspezifisch.

## Was drin steckt

| Datei | Aufgabe |
|---|---|
| `monitor.py` | Hauptmelder. Scannt Shops (HTML-Scraper und Shopify-Kataloge) auf neue oder wieder lieferbare Ware |
| `drops.py` | Drop-Radar für Vorbestellungen, Kollaborationen und News-Quellen |
| `feedback.py` | Holt die Daumen-hoch/runter-Klicks unter den Meldungen ab und schreibt sie mit. Darf nur an EINER Stelle laufen, weil `getUpdates` das Postfach beim Lesen leert |
| `termine.py` | Termin-Wecker. Meldet 7 Tage, 1 Tag und am Tag selbst vor einem bekannten Release |
| `sniper.py` | Enges Zeitfenster um einen bekannten Termin, kurzer Abstand statt Dauerlauf |
| `marktwert.py` | Grobe Einschätzung, ob ein Fund über oder unter Marktwert liegt |
| `angebote.py` | Gebrauchtmarkt-Suche, standardmäßig leer und aus |
| `scripts/local-monitor.sh` | Läufer für den eigenen Rechner (launchd, alle 15 Minuten) |
| `scripts/luecke.py` | Rechnet aus, wie viel Überwachungszeit im aktiven Fenster verloren ging |
| `checkout/` | Tampermonkey-Userscript, das im Checkout Adressfelder füllt. Der Bestellknopf ist ausdrücklich gesperrt |

## Einrichten

```bash
git clone <dieses-repo> && cd tcg-drop-monitor
cp .env.example .env.local     # Telegram-Token und Chat-ID eintragen
python3 monitor.py             # einmal von Hand
```

Es gibt keine Abhängigkeiten außer `requests` und `beautifulsoup4`.

Dauerbetrieb auf einem Mac: `scripts/com.pokemon.catchr.monitor.plist`
anpassen (Pfad eintragen), nach `~/Library/LaunchAgents/` legen, dann
`launchctl load`. Der Läufer pausiert außerhalb 7 bis 22 Uhr und meldet
selbst, wenn Überwachungszeit verloren ging, etwa weil der Rechner zu war.

In `.github/workflows/` liegen dieselben Läufe als GitHub Actions. Achtung:
auf dem kostenlosen Kontingent werden geplante Läufe stillschweigend
verworfen. Aus 289 geplanten Läufen pro Tag wurden gemessene 83.

## Zwei Dinge, die beim Bauen wehgetan haben

**Harte Sperren müssen an jedem Eintrittsweg stehen.** Im Shopify-Zweig
waren drei Wege mit ODER verknüpft, und einer davon umging die zentrale
Relevanzprüfung. Ergebnis: Yu-Gi-Oh, Lorcana und ein Brettspiel namens
Carcassonne landeten im Melder, obwohl die Filter dafür längst existierten.

**Ein Ausschlussfilter braucht zwei Stufen.** Manche Ausschlüsse sind
rettbar (eine "Ordner-Kollektion" enthält wirklich Karten), andere nie
(ein Radiergummi bleibt ein Radiergummi, auch wenn er im Blister steckt).
Mit nur einer Stufe rettet sich das Zubehör über das eigene Verpackungswort
selbst zurück in die Meldung.

## Grenzen, ehrlich

- Die Shop-Selektoren sind auf deutsche Händler zugeschnitten und altern.
  Ändert ein Shop sein Layout, meldet der Melder dort still nichts mehr.
- Preisgrenzen sind fest verdrahtete UVP-Schätzungen plus Toleranz.
- Der Gebrauchtmarkt-Teil ist absichtlich abgeschaltet.
- Kein Auto-Buy, und das ist Absicht, keine fehlende Funktion.

## Lizenz

Keine. Alle Rechte vorbehalten, bis hier eine Lizenzdatei liegt.

## Zielsets (seit 02.09.2026)

Welche Sets gemeldet werden, steht in `zielsets.txt`: eine Zeile je Suchwort, von Hand gepflegt, gelesen von `monitor.py` (Stichwoerter und Pokemon-Erkennung) und `drops.py` (Immer-melden-Liste). Jubilaeumsware traegt weiter die Marke `30 JAHRE`, steht aber nicht mehr pauschal ueber allem. Top-Trainer-Boxen laufender Sets zum Retail-Preis gelten als Sofort-Flip. Einzelne Booster-Packs (unter 8 Euro) werden nie gemeldet.

`MediaMarkt AT` weist Skripte ab und laeuft deshalb nur lokal ueber einen echten Browser (Scrapling, `fetch_browser()`). In der Cloud ist die Quelle per `SOURCES_EXCLUDE` ausgeschlossen.

