#!/usr/bin/env python3
"""Loest Rebase-Konflikte in den Gedaechtnisdateien der Workflows auf.

Anlass 14.09.2026: zwei Monitor-Laeufe standen in derselben concurrency-Gruppe,
der zweite wartete brav, checkte beim Start aber den Commit von seinem
Erstellungszeitpunkt aus (GITHUB_SHA), nicht die Spitze, die der erste Lauf
inzwischen gepusht hatte. Beide haengten an gemeldet.json an, "git pull --rebase"
meldete CONFLICT, und alle fuenf Wiederholungen scheiterten am selben Konflikt.

Aufruf waehrend eines haengenden Rebase (nach gescheitertem git pull --rebase):
    python3 scripts/state_vereinen.py && git push

Regel je Datei:
  dict (state.json, gemeldet.json): Vereinigung, bei gleichem Schluessel gewinnt
      der eigene Lauf (er hat spaeter gemessen).
  feedback_state.json: {"offset": N} ist eine Hochwassermarke, je Schluessel
      gewinnt der groessere Wert, sonst holt der naechste Lauf Telegram-Updates
      doppelt (Befund Kimi 14.09.2026).
  list (drops_state.json, bewertungen.json): Vereinigung in Reihenfolge, doppelte
      Eintraege einmal; reine Text-Listen bleiben sortiert wie sie drops.py schreibt.
  last_run.txt: der eigene Lauf gewinnt, ist der spaetere.
Jede andere Datei im Konflikt bricht ab (Exit 1), der Workflow macht dann
"git rebase --abort" und versucht es neu. Haben beide Laeufe dasselbe angehaengt,
ist der eigene Commit nach der Aufloesung leer; dann wird er uebersprungen.
"""
import json
import os
import subprocess
import sys

JSON_DATEIEN = {"state.json", "gemeldet.json", "drops_state.json", "feedback_state.json", "bewertungen.json"}
TEXT_MEINS = {"last_run.txt"}
EINGERUECKT = {"gemeldet.json", "bewertungen.json"}   # so schreiben monitor.py und feedback.py


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def stufe(nr: int, datei: str):
    """:2 = Stand von origin/main (beim Rebase 'ours'), :3 = eigener Commit."""
    try:
        roh = git("show", f":{nr}:{datei}")
    except subprocess.CalledProcessError:
        return None  # Datei auf dieser Seite nicht vorhanden
    return json.loads(roh)


def vereine(datei: str, basis, meins):
    if basis is None:
        return meins
    if meins is None:
        return basis
    if isinstance(basis, dict) and isinstance(meins, dict):
        if datei == "feedback_state.json":
            return {k: max(basis.get(k, v), v) if isinstance(v, (int, float)) else v
                    for k, v in {**basis, **meins}.items()}
        return {**basis, **meins}
    if isinstance(basis, list) and isinstance(meins, list):
        gesehen, ergebnis = set(), []
        for eintrag in basis + meins:
            schluessel = json.dumps(eintrag, sort_keys=True, ensure_ascii=False)
            if schluessel not in gesehen:
                gesehen.add(schluessel)
                ergebnis.append(eintrag)
        if all(isinstance(e, str) for e in ergebnis):
            ergebnis.sort()
        return ergebnis
    raise ValueError("unvereinbare JSON-Formen")


def main() -> int:
    if not os.path.isdir(os.path.join(git("rev-parse", "--git-dir").strip(), "rebase-merge")):
        print("state_vereinen: kein Rebase offen, hier gibt es nichts zu vereinen", file=sys.stderr)
        return 1
    offen = [z for z in git("diff", "--name-only", "--diff-filter=U").split("\n") if z]
    fremd = [d for d in offen if d not in JSON_DATEIEN | TEXT_MEINS]
    if fremd:
        print(f"state_vereinen: Konflikt in {fremd}, das loese ich nicht auf", file=sys.stderr)
        return 1
    for datei in offen:
        if datei in TEXT_MEINS:
            with open(datei, "w", encoding="utf-8") as f:
                f.write(git("show", f":3:{datei}"))
            git("add", datei)
            print(f"state_vereinen: {datei} eigener Stand behalten")
            continue
        try:
            ergebnis = vereine(datei, stufe(2, datei), stufe(3, datei))
        except (ValueError, json.JSONDecodeError, TypeError) as e:
            print(f"state_vereinen: {datei}: {e}", file=sys.stderr)
            return 1
        einzug = 1 if datei in EINGERUECKT else None
        with open(datei, "w", encoding="utf-8") as f:
            f.write(json.dumps(ergebnis, ensure_ascii=False, sort_keys=True, indent=einzug))
        git("add", datei)
        print(f"state_vereinen: {datei} vereint ({len(ergebnis)} Eintraege)")
    weiter = subprocess.run(["git", "-c", "core.editor=true", "rebase", "--continue"],
                            capture_output=True, text=True)
    if weiter.returncode != 0:
        # Beide Laeufe hatten dasselbe angehaengt: der eigene Commit ist leer.
        print("state_vereinen: eigener Commit nach Aufloesung leer, wird uebersprungen")
        subprocess.run(["git", "rebase", "--skip"], check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
