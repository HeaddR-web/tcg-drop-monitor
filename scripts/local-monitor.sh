#!/bin/zsh
# Lokaler Voll-Laeufer.
#
# Stand 25.08.2026: die GitHub-Actions-Freiminuten des Kontos sind am 20.08.
# aufgebraucht, seitdem startet dort KEIN Lauf mehr. Bis das geklaert ist,
# macht dieser Mac die komplette Arbeit, nicht mehr nur die Shops, die
# Cloud-IPs blocken.
#
# Wird alle 15 Min von launchd gestartet (com.pokemon.catchr.monitor).
# Pause ausserhalb 7-22 Uhr. Zustand und Logs liegen getrennt vom Repo unter
# ~/Library/Application Support/pokemon-catchr, damit die lokalen Laeufe den
# Repo-Zustand nicht anfassen und es kein Durcheinander mit GitHub gibt.

REPO="$HOME/pokemon-catchr"
DATA="$HOME/Library/Application Support/pokemon-catchr"

# Aktives Fenster 7 bis 22 Uhr (Ansage 26.08.2026). Vorher lief es
# bis Mitternacht, aber der Rechner ist abends ohnehin zu, und dann meldet der
# Waechter jeden Morgen eine Luecke, die niemand haette nutzen koennen.
hour=$(date +%H)
if [ "$hour" -lt 7 ] || [ "$hour" -ge 22 ]; then
  exit 0
fi

mkdir -p "$DATA"
cd "$DATA" || exit 1

# --- Sperre gegen ueberlappende Laeufe ---
# mkdir ist atomar, anders als "Datei da?" plus "Datei anlegen". Ohne diese
# Sperre kann ein zweiter Lauf starten, waehrend der erste noch scannt. Genau
# das passierte am 01.09.2026 um 17:15: der Handlauf und der launchd-Lauf
# liefen gleichzeitig, und das Kuerzen des Logs am Ende (tail nach tmp, dann
# mv) hat die Ausgabe des anderen Laufs komplett verschluckt.
LOCK="$DATA/.lauf.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  # Verwaiste Sperre nach einem Absturz nicht ewig stehen lassen.
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +20 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null
    mkdir "$LOCK" 2>/dev/null || exit 0
    echo "=== $(date '+%F %H:%M') verwaiste Sperre entfernt ===" >> "$DATA/monitor.log"
  else
    echo "=== $(date '+%F %H:%M') laeuft schon, uebersprungen ===" >> "$DATA/monitor.log"
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT INT TERM

# Telegram-Zugang aus lokaler env-Datei (nicht im Repo, nicht committen)
if [ -f "$REPO/.env.local" ]; then
  set -a
  source "$REPO/.env.local"
  set +a
fi

PY=/Library/Frameworks/Python.framework/Versions/3.14/bin/python3
if [ ! -x "$PY" ]; then
  PY=$(command -v python3) || exit 1
fi

# --- Waechter: meldet Luecken im Betrieb ---
# Der Mac ist seit 25.08.2026 der einzige Laeufer. Schlaeft er oder ist er aus,
# laeuft nichts, und das faellt sonst erst auf, wenn ein Drop schon weg ist.
# Deshalb bei JEDEM Lauf pruefen, wie lange der letzte her ist, und bei mehr
# als zwei Stunden einmal Bescheid geben. Die normale Nachtpause (0-7 Uhr)
# ist keine Luecke und wird ausgenommen.
HB="$DATA/heartbeat.txt"
NOW=$(date +%s)
PREV=$(cat "$HB" 2>/dev/null || echo 0)
if [ "$PREV" -gt 0 ]; then
  # Nicht die reine Uhrzeit-Differenz zaehlen, sondern nur die verlorene
  # Ueberwachungszeit im aktiven Fenster 7-22 Uhr. Sonst gibt es jeden Morgen
  # Fehlalarm wegen der gewollten Nachtpause, und umgekehrt bleibt es still,
  # wenn der Mac schon um 22:25 zuklappt und erst um 08:25 aufgeht.
  VERLOREN=$("$PY" "$REPO/scripts/luecke.py" "$PREV")
  if [ "$VERLOREN" -gt 120 ]; then
    STD=$(( VERLOREN / 60 ))
    MIN=$(( VERLOREN % 60 ))
    TXT="⚠️ Drop-Wächter: ${STD}h ${MIN}min Überwachungszeit verloren. Letzter Check: $(date -r "$PREV" '+%d.%m. %H:%M'). Der Mac war wahrscheinlich zu oder aus. Läuft ab jetzt wieder."
    CODE="kein-token"
    if [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ]; then
      # Nur den HTTP-Status mitschreiben, nie die URL: darin steckt der Token.
      CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
        "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TG_CHAT_ID}" \
        --data-urlencode "text=${TXT}")
    fi
    echo "WAECHTER: ${STD}h ${MIN}min aktive Zeit verloren, gemeldet (Telegram HTTP $CODE)" >> "$DATA/monitor.log"
  fi
fi
echo "$NOW" > "$HB"

# Beim allerersten lokalen Lauf den Zustand aus dem Repo uebernehmen, sonst
# gilt jeder bereits gemeldete Treffer als neu und Telegram wird geflutet.
[ -f "$DATA/state_local.json" ]       || cp "$REPO/state.json"       "$DATA/state_local.json"       2>/dev/null
[ -f "$DATA/drops_state_local.json" ] || cp "$REPO/drops_state.json" "$DATA/drops_state_local.json" 2>/dev/null

echo "=== $(date '+%F %H:%M') Lauf gestartet ===" >> "$DATA/monitor.log"

# 1) Pokemon-Monitor ueber ALLE Quellen. Frueher lief hier nur Amazon,
#    MediaMarkt und Saturn, den Rest machte GitHub. Solange GitHub steht,
#    laeuft alles hier.
STATE_FILE="$DATA/state_local.json" \
  "$PY" "$REPO/monitor.py" >> "$DATA/monitor.log" 2>&1

# 2) Drop-Radar (Pokemon-Vorbestellungen + Pokemon-News).
#    Stuendlich reicht, wie auf GitHub auch.
if [ "$(cat "$DATA/drops_last_run" 2>/dev/null)" != "$(date +%Y-%m-%dT%H)" ]; then
  STATE_FILE="$DATA/drops_state_local.json" \
    "$PY" "$REPO/drops.py" >> "$DATA/monitor.log" 2>&1
  date +%Y-%m-%dT%H > "$DATA/drops_last_run"
fi

# 3) Angebots-Radar: PAUSIERT seit 23.08.2026 (Watchlist leer, nur Pokemon).
#    Sobald belegte Pokemon-Suchen drin stehen, wieder einkommentieren.
# STATE_FILE="$DATA/angebote_state.json" "$PY" "$REPO/angebote.py" >> "$DATA/monitor.log" 2>&1

# 4) Termin-Wecker: einmal taeglich. Meldet 7 Tage, 1 Tag und am Tag selbst
#    vor einem Drop, und sagt wann der Sniper laufen muss.
if [ "$(cat "$DATA/termine_last_run" 2>/dev/null)" != "$(date +%F)" ]; then
  STATE_FILE="$DATA/termine_state.json" "$PY" "$REPO/termine.py" >> "$DATA/monitor.log" 2>&1
  date +%F > "$DATA/termine_last_run"
fi

# Log klein halten
tail -n 800 "$DATA/monitor.log" > "$DATA/monitor.log.tmp" && mv "$DATA/monitor.log.tmp" "$DATA/monitor.log"
