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

## Oesterreich-Quellen und Reseller-Regel (seit 02.09.2026)

Zwei weitere Quellen laufen nur lokal ueber den Browser-Weg (`parser: smyths` und `parser: geizhals`, in der Cloud per `SOURCES_EXCLUDE` ausgeschlossen):

- **Smyths Toys AT**: drei Suchseiten, danach fuer bis zu 10 Treffer die Produktseite, weil nur dort im JSON-LD der Lagerstatus steht. Prio-Ware (Top-Trainer-Box, Ultra-Premium, Display) und bekannte Warteposten kommen zuerst dran. Liefert der Shop zwei Fehlseiten in Folge (am 02.09. waren es 162-Byte-502-Huellen), bricht der Abruf ab und die Treffer gehen mit "Lagerstatus ungeprueft" raus.
- **Smyths Toys DE** (smythstoys.com/de): **eingebaut seit 14.09.2026**, gleicher Parser `smyths`, nur lokal. Drei Suchseiten (30 Jahre, Top-Trainer-Box, Kollektion). Die Suchseite listet alle neun Launch-Produkte des 30-Jahre-Sets zur UVP (ETB 54,99, ex-Boxen und Tins 24,99, Ordner 46,99, Poster 23,99, Tech-Sticker 19,99, Blister 12,99). Testlauf 14.09.2026: 23 relevante Treffer, 10 Produktseiten mit JSON-LD gelesen (alle OutOfStock, also "wartet"), der Rest "Lagerstatus ungeprüft". Direkt danach lieferten die Produktseiten wieder die 162-Byte-502-Hülle, in Stufe 1 und Stufe 2 gleichermaßen: das ist eine Drossel nach vielen Abrufen, keine Frage des Browsers. Im echten Browser zeigt Smyths DE auf Produktseiten außerdem einen Imperva-Klick-Check, der nicht umgangen wird.
- **Browser-Abruf mit Stufenleiter (seit 14.09.2026):** `fetch_browser()` versucht erst den Scrapling-Standard, und nur wenn weniger als 2000 Bytes zurückkommen, Stufe 2 mit `--real-chrome --solve-cloudflare --locale de-DE` (startet das installierte Google Chrome). Gleiche Regel wie in der Workspace-CLAUDE.md, Anlass war mueller.de am 13.09. Bei einer echten 502-Hülle kostet Stufe 2 rund 3 Sekunden und hilft nicht, bei einer Bot-Wand kann sie die Seite öffnen.
- **Geizhals.at**: Preisvergleich als Sammelquelle fuer Haendler, die selbst Bots sperren (Pagro, Libro). Preis ist das guenstigste Angebot, "keine Angebote" gilt als wartet.

Zubehoer mit Set-Namen im Titel (Acrylboxen, Protektoren) faengt `ZUBEHOER_TELLS` ab.

## Deutschland-Retailer, zweite Runde am 14.09.2026 (vor dem Launch 16.09.)
- **Geizhals.de**: **eingebaut seit 14.09.2026**, gleicher Parser `geizhals` wie Geizhals AT, nur lokal (in `SOURCES_ONLY` des Mac-Laufs und in `SOURCES_EXCLUDE` der Cloud). Drei Suchseiten (30 Jahre, Top-Trainer-Box, Ultra Premium). Testlauf 14.09.: 15 relevante Treffer, das ganze Jubiläumsset mit je einem Angebot (Blister 12,99, Tech-Sticker 18,99, Poster 23,99, ex-Kollektionen und Tins 24,99, Top-Trainer-Box 52,99). Wert: macht deutsche Händler sichtbar, die den Monitor direkt aussperren (Thalia, Kaufland.de, Galeria, idealo: alle curl 403). Bewusst in Kauf genommen: dasselbe Produkt kann auf geizhals.at und geizhals.de auftauchen und wird dann zweimal gemeldet (Fingerprint ist Shop plus URL). Das ist gewollt, weil die AT-Meldung den günstigsten AT-Händler und die DE-Meldung den günstigsten DE-Händler zeigt, und AT-Händler wie Pagro nur innerhalb Österreichs liefern.
- **Filter-Loch geschlossen (14.09.2026):** Smyths DE nennt die ex-Kollektionen "Pokémon 30 Jahre Edition Feelinara-ex" ohne Karten, Box oder Kollektion im Namen. `ist_versiegelt()` verlangte eines dieser Wörter und warf beide raus (7 von 9 Jubiläumsprodukten im Bestand, gemessen über die Fingerprints). Jetzt gilt ein Titel mit Jubiläumswort und "pok" auch ohne Warenwort als versiegelt, Einzelkarten- und Zubehör-Tells sperren weiter. Kuscheltiere tragen "30 cm", nicht "30 Jahre". Nach dem Fix: 25 statt 23 Treffer bei Smyths DE. Kimi-Review 14.09.: die Regel hätte Merch der Linie ("30 Jahre Plüschtier", "30 Jahre Tasse") durchgelassen, weil `is_relevant` die EXCLUDE-Liste bei Jubiläumstiteln überspringt. Deshalb sperrt die neue Regel EXCLUDE-Wörter selbst. Ein Promokarten-Tell als Einzelkarten-Sperre wurde probiert und verworfen: er traf drei echte Boxen, deren Titel die beiliegende Promokarte nennen.
- **Smyths DE, Suche "pokemon 30" (Stefanies Link):** eine Seite, 30 Artikel, keine Seite 2. Davon 9 Sammelkarten-Produkte, identisch mit der Suche "pokemon 30 jahre", die der Monitor nutzt. Rest sind Kuscheltiere, LEGO, Figuren.
- **Thalia**: lesbar, Suche "pokemon 30 jahre" liefert 3 Treffer ohne Sammelkarten. Nichts zum Jubiläum gelistet.
- **Expert**: 2 Treffer, beides Ravensburger-Brettspiele. **Hugendubel**: kein Treffer. **Euronics**: Suche antwortet 405. **Rossmann**: 200, aber leere JS-Hülle (226 Zeichen Text). **Fantasywelt**: Cloudflare 403. **Netto**: 404. **JB Spielwaren**: Suche zeigt nur LEGO. **Galeria**: nichts Verwertbares. **Kaufland.de, idealo, Otto**: Marktplatz-Reseller (Otto schon am 09.08. entfernt). Keinen davon aufnehmen.
- **Smyths-Filiale Ingolstadt**: Filialbestand zeigt Smyths erst nach Filialwahl auf der Produktseite, mein Abruf wurde auf die Startseite umgeleitet. Nicht gebaut, offen.

**Reseller nur bis UVP:** Fachshops, die selbst Reseller sind, stehen in `RESELLER_QUELLEN`. Sie melden nur noch Angebote bis exakt UVP und nur mit erkanntem Preis. Die 20-Prozent-Toleranz (`UVP_TOLERANZ`) gilt nur fuer Retailer: bei einem Reseller ist jeder Cent ueber UVP schon dessen Marge.

## Zielprodukte ausserhalb Pokemon (seit 02.09.2026)

`ZIELPRODUKTE` in monitor.py nimmt Ware auf, die im Handel unter dem Zweitmarkt liegt, egal welches Hobby (erster Eintrag: PS5 Pro, UVP 899,99). Jeder Eintrag traegt Pflicht-Stichwoerter, Ausschluesse (Controller, Staender, Spiele), UVP und einen von Hand gepflegten Marktwert mit Quelle und Datum. Zielprodukte umgehen die Pokemon-Pruefungen, zaehlen als Prio und werden nur bis UVP plus 2 Prozent gemeldet. Die Suche haengt als zusaetzliche URL an den Retailern; bei jsonld-Quellen wird fuer den Lagerstatus die Produktseite nachgeladen, weil die Suchseite bei Konsolen keine Verfuegbarkeit traegt.

