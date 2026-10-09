# Pump Radar

Eigenständiger Pump.fun-Live-Radar mit direkter Phantom-Verbindung. Dieser Ordner ist **kein Teil von PapayaOS**.

## Aktueller Stand

- Live-Feed neuer Pump.fun-Coins über den PumpPortal-WebSocket (`subscribeNewToken`).
- Partikelvisualisierung im Startbereich; passt sich der Bildschirmgröße an und respektiert reduzierte Bewegung.
- Suche, Mindest-Startkauf, Merkliste und optionale Browser-Benachrichtigungen.
- Direkte Verbindung zur Phantom-Browsererweiterung oder zum Phantom-In-App-Browser. Die App liest nur die öffentliche Wallet-Adresse und das SOL-Guthaben.
- Jeder Coin öffnet seine Pump.fun-Seite. Käufe und Verkäufe werden dort vom Nutzer in Phantom freigegeben.

**Noch nicht vorhanden:** automatische Bewertung, Kauf- und Verkaufstransaktionen innerhalb dieser App, Papier-Trades, Autokauf, Autoverkauf und Positionsverwaltung. Die Oberfläche behauptet diese Funktionen nicht. Die Feed-Daten sind Kandidaten zur Recherche, keine Kaufempfehlungen.

## Lokal starten

Node.js 22 oder neuer. Keine Installation von Paketen nötig.

```powershell
cd .\pumpfun-radar
node .\server.mjs
```

Dann `http://127.0.0.1:3222` in einem Browser mit Phantom-Erweiterung öffnen. Auf dem Handy muss eine Web-App im Phantom-In-App-Browser geöffnet werden, damit sie Phantom direkt verbinden kann. Für die Anzeige von Trades auf dem Handy genügt die normale Phantom-App mit derselben Wallet.

Konfiguration über Umgebungsvariablen:

| Name | Standard | Zweck |
| --- | --- | --- |
| `HOST` | `127.0.0.1` | Lokale Bind-Adresse |
| `PORT` | `3222` | Web-Port |
| `SOLANA_RPC_URL` | öffentlicher Solana-Mainnet-RPC | SOL-Guthaben |
| `PUMPPORTAL_API_KEY` | leer | Falls PumpPortal einen API-Schlüssel für den Stream verlangt |

Der PumpPortal-Stream ist ein externer Datenanbieter. Bei fehlender Verbindung zeigt die App `Stream offline` und versucht automatisch erneut zu verbinden. Für einen dauerhaften Produktivbetrieb ist ein verlässlicher RPC nötig; öffentliche Solana-RPC-Endpunkte sind begrenzt.

Die App fragt niemals nach Seed Phrase oder Private Key und speichert beides nicht. Eine normale Phantom-Verbindung erteilt keine Erlaubnis für unbeaufsichtigte Trades. Dafür wäre eine separate Signatur- und Limit-Architektur nötig.
