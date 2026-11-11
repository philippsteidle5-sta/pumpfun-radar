# PAPER-Monitor starten

Das Paket ist der Quellstand. Es enthält kein Token, keine Datenbank und kein Git.

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
export APP_TOKEN='hier-lokal-setzen'
export DESK_DB=./var/desk.sqlite
export DESK_HOST=127.0.0.1
export DESK_PORT=43123
.venv/bin/python -m desk.main --check
.venv/bin/python -m desk.main --once DKtofLM6bhb54GQy5YFSWQjQmGiZqAkjzkQJ1LTopump
.venv/bin/python -m desk.main --serve
```

Im Browser derselben Maschine die Seite einmal mit `#t=` und dem lokalen Token öffnen. Das Fragment wird in den lokalen Speicher gelegt und aus der Adresse entfernt. Danach bleiben die API-Aufrufe mit `Authorization: Bearer`.

Ohne Token antwortet `/api` mit 401. Die HTML-Seite selbst zeigt keine Münzdaten.

`127.0.0.1` ist nur auf diesem Rechner erreichbar, nicht vom iPhone aus. Es wird kein Tunnel und kein öffentlicher Endpunkt angelegt.

`--once` liest eine Münze höchstens etwa 30 Sekunden und beendet sich. Eine fehlende Quelle bleibt rot. Der Handel bleibt PAPER. Telegram wird nicht gesendet.

Eine vorhandene `DESK_DB` wird nicht gelöscht. Die Revision steht in `REVISION`, sobald das Auslieferungspaket gebaut wurde.
