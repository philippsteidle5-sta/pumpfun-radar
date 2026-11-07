# Backend-Stand

PAPER-only. Fünf Rollen teilen sich eine SQLite-Datenbank (WAL). Der Notifier schreibt nur in die Outbox.

## Annahmen

- Nachtgrenze ist ein festes UTC+1, weil die Originalzeitzone fehlt. Zeitstempel in der Datenbank sind UTC.
- Analyst-Score rundet half-even. Gebühren runden auf Cent mit ROUND_HALF_UP.
- `auditor.retries` ist die Zahl der Versuche inklusive des ersten. Abstand 2 Sekunden, im Test ohne Warten.
- Brutto-Prozent ist die Preisänderung. Netto-PnL zieht Kauf- und Verkaufsgebühr je einmal ab. Die Reservierung hält nur das Ticket.
- Gas ist im Modell 0 und als Annahme gekennzeichnet, nicht als geprüfte FOMO-Tatsache.
- Die Gebührenstufen sind die vorgegebene Papierformel. Der Referral-Rabatt 0.10 bleibt nominal. Wirksam ist 0, solange `referral_discount_verified` falsch ist.
- Der Chart-Bereich LIVE ist die laufende Owner-Nacht ab Nachtstart, kein festes 15-Minuten-Fenster.
- `close_blocked` bleibt Exposure: Cash bleibt reserviert, der Mint belegt weiter den offenen Slot, Risk lässt sich nicht pausieren, ein zweiter Einstieg scheitert.

## Widersprüche, konservativ gehalten

- Die Übergabe hielt die Coin-URL für fehlend. Die ergänzte Vorgabe ist die exakte Vorlage in `desk/links.py`. Unbekannte Chain fällt auf die Signup-URL zurück.
- Auditor aus braucht zwei Bestätigungen `DISABLE AUDITOR` und hält Münzen. Er markiert sie nicht als bestanden.
- Leerer Graph und `nodes`/`links` ohne `edges` bleiben rot. Gleicher Dev-Bestand ist kein `sold 0`.
- LIVE bleibt in diesem Chunk aus, auch wenn eine Nachtzeile in der Datenbank gefälscht wird.

## API

Jeder `/api`-Pfad verlangt `Authorization: Bearer`. Ein Token in der Query ist 401. Das Token kommt nur zur Laufzeit aus der Umgebung und steht nicht in Logs, Antworten oder diesem Bericht.

`GET /api/state`, `/api/chart`, `/api/activity`, `/api/coin/{mint}`, `/api/audit/{mint}`, `/api/agents/{name}/decisions`, `/api/account`, `/api/settings`, `/api/stream`. `POST /api/settings`, `/api/agents/{name}/pause`, `/api/agents/{name}/resume`, `/api/mode`, `/api/push`. Push wird nur gespeichert.

## Monitor

`--serve` startet die PAPER-Ansicht und genau eine PumpPortal-Migration-Verbindung. Dex, RugCheck und RPC laufen über die injizierten Leseadapter. `--once MINT` prüft eine gewählte Münze innerhalb von 30 Sekunden, ohne sie zu dimensionieren. Eine historische Prüfung ist keine frische Migration. Fehlende Dev-Historie und ein Graph ohne `edges` bleiben rot.

## Nicht in diesem Chunk

Echte PAPER-Nacht, LIVE, Telegram-Versand, Web-Push-Zustellung, Zweistunden-Replay, Anbindung der fünf bestehenden Chats. Jev ist nur als Adapter im Code und wird hier nicht aufgerufen. Die lokale Oberfläche wurde bei 390px Browserbreite geprüft; Zugriff vom iPhone und echte Geräteprüfung sind nicht eingerichtet.

## Erweiterung 0.2

Windows-Starter `Start.cmd` richtet eine lokale Python-Umgebung ein, erzeugt ein persistentes Zugangstoken und öffnet den Browser. `-Offline` unterbindet öffentliche Marktabfragen. Direkt geöffnete HTML-Dateien laden ihre lokalen Assets und zeigen eine Starthilfe.

Die Übersicht verwendet bestehende Kontobuchungen, Positionen und Agentenzustände. Neue authentifizierte Routen: `GET /api/positions` und `GET /api/report?date=YYYY-MM-DD&format=json|csv`. Berichte verwenden die feste Owner-Tagesgrenze. Realisierter PnL und Gebühren stammen nur aus gebuchten PAPER-Abschlüssen mit Verkaufszeitpunkt im gewählten Tag; TESTDATA wird separat ausgewiesen. Offene Positionen im Bericht entsprechen dem aktuellen Stand. CSV schützt Textfelder vor Formelinterpretation.
