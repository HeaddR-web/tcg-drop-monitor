# Checkout-Autofill

Userscript für Tampermonkey. Füllt im Checkout Adressdaten aus, damit im
Drop-Moment nur noch Zahlung und Bestätigung bleiben.

**Datei:** `checkout-autofill.user.js`

## Installation
1. Tampermonkey im Browser installieren
2. Tampermonkey-Icon → "Neues Skript erstellen"
3. Inhalt von `checkout-autofill.user.js` komplett hineinkopieren
4. Oben im Block `PROFIL` die echten Daten eintragen, speichern
5. Im Checkout: Strg+Shift+1 oder den ⚡-Button unten rechts

## Grundlage
Basiert auf einer Vorlage von Kimi, hier korrigiert. Fünf Änderungen:

1. **Läuft nur auf hinterlegten Shop-Domains** statt auf jeder Website.
   Vorher klebte der Autofill-Button auf jeder Seite, auch beim Banking.
2. **Der Bestellknopf ist gesperrt.** In der Vorlage stand
   "zahlungspflichtig bestellen" in der Liste der Knöpfe, die automatisch
   geklickt werden dürfen. Jetzt gibt es eine Sperrliste (kaufen, bestellen,
   zahlungspflichtig, buy, order, pay), die einen Auto-Klick verhindert.
   Kaufen bleibt immer ein bewusster Handgriff.
3. **Rechnungs- UND Lieferadresse** werden gefüllt. Die Vorlage nahm immer
   nur das erste passende Feld und ließ die zweite Adresse leer.
4. **Hotkey layout-unabhängig** über `e.code` statt `e.key`.
5. **Auto-Modus standardmäßig aus.** Du entscheidest, wann gefüllt wird.

## Grenzen, ehrlich
- Kreditkartenfelder liegen in gesicherten iFrames, da kommt kein Userscript
  hinein. Das ist Absicht der Shops und gut so.
- Ein Shop-Account mit gespeicherter Adresse ist **schneller** als jedes
  Autofill. Das Skript hilft nur beim Gastkauf oder bei neuen Shops.
- Für den Steiff-Termin am 03.08. ist unbekannt, welche Partner-Shops die
  Ware bekommen. Selektoren lassen sich erst ergänzen, wenn das feststeht.

## Vorbereitung, die mehr bringt als das Skript
- Bei den bekannten Shops (Card Corner, GeeksHeaven, Feenturm, TCG-Trade)
  vorab ein Konto anlegen, Adresse hinterlegen, eingeloggt bleiben
- PayPal im Browser verknüpft lassen
- Browser-Autofill für Adressen aktivieren
