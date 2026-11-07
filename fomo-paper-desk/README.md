# FOMO Paper Desk

Privater PAPER-Desk mit lokaler Browseroberfläche. Ein Prozess koordiniert Scanner, Auditor, Analyst, Executor und Risk. Der Handel ist simuliert, Telegram bleibt aus.

## Windows: starten

**`Start.cmd` doppelklicken.** Die lokale Python-Umgebung wird beim ersten Start eingerichtet; dafür wird Internet benötigt. Python 3.12 oder neuer ist erforderlich. Auf diesem Rechner ist die Umgebung bereits eingerichtet.

Der Starter öffnet `http://127.0.0.1:43123/` automatisch und meldet den Browser über ein lokal erzeugtes Token an. Das Token bleibt unter `var/access.token`, die Datenbank unter `var/desk.sqlite`. Beenden im Startfenster mit Strg+C.

Die Oberfläche zeigt Paper-Kontowert, offene Positionen, fünf Rollen mit Pause/Fortsetzen, Aktivitäten, Coin-Prüfungen und datierte Nachtberichte mit CSV-Export. Der Kontowert enthält keine offenen Kursgewinne. TESTDATA-Trades fließen nicht in den realisierten Bericht-PnL ein.

`Start.cmd -Offline` zeigt vorhandene Daten ohne öffentliche Marktverbindungen. `Start.cmd -Check` prüft die Installation. Ein direktes Öffnen von `desk/static/monitor.html` zeigt das Design und eine Starthilfe; für Kontodaten wird der lokale Server benötigt.

Wenn Python nicht automatisch gefunden wird, lässt sich sein Pfad in PowerShell über `$env:DESK_PYTHON='C:\Pfad\python.exe'` vorgeben.

## Offline starten

```bash
python3 -m pip install -e ".[dev]"
PYTHONPATH=. python3 -m desk.main --check
```

`--check` prüft die Defaults und beendet sich.

```bash
export APP_TOKEN='lokal-setzen'
export DESK_DB=./var/desk.sqlite
PYTHONPATH=. python3 -m desk.main --once DKtofLM6bhb54GQy5YFSWQjQmGiZqAkjzkQJ1LTopump
PYTHONPATH=. python3 -m desk.main --serve
```

`--serve` bindet standardmäßig `127.0.0.1:43123` und liest öffentliche Marktdaten. Der Handel bleibt PAPER. Telegram bleibt aus. Die bestehende Datenbank wird nicht geleert. Start ohne `APP_TOKEN` endet mit Code 2.

Die Seite nimmt das Token aus dem Fragment `#t=` oder dem lokalen Anmeldefeld und legt es in den lokalen Speicher. `/api` ohne Bearer ist 401. Abmelden entfernt das gespeicherte Token. `127.0.0.1` ist nicht vom iPhone aus erreichbar.

## Tests

```bash
PYTHONPATH=. python3 -m pytest tests -o addopts= --tb=short -ra
```

22 Backend-Tests bestanden; Browserprüfung: Design bei direktem Dateiöffnen, Anmeldung, Konto, fünf Rollen, Pause/Fortsetzen, Nachtbericht, CSV-Download, Analyse, Layout bei 390px und Abmelden. Details stehen in `test-output.txt`. Öffentliche Marktdaten liegen unter `fixtures/`. Testdaten, die eine Runde bestehen, sind als `TESTDATA` markiert und zählen nicht als PAPER-Nacht. LIVE bleibt gesperrt. Eine vollständige PAPER-Nacht mit öffentlichen Daten ist weiterhin nicht nachgewiesen.

Einzelheiten, Annahmen und offene Punkte: `docs/BACKEND.md`.
