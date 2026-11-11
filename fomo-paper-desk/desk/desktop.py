"""Local convenience entrypoint, persistent access token and browser launch."""

import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

from desk.main import main


def launch() -> int:
    os.environ["DESK_HOST"] = "127.0.0.1"
    port = int(os.environ.get("DESK_PORT", "43123"))
    # Refuse occupied ports before opening any browser or generating a login URL.
    with socket.socket() as check:
        try:
            check.bind(("127.0.0.1", port))
        except OSError:
            print(f"Port {port} ist belegt. Bestehenden Desk beenden oder DESK_PORT ändern.")
            return 2
    token_path = Path("var/access.token")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("APP_TOKEN"):
        if not token_path.exists():
            with token_path.open("x", encoding="utf-8") as handle:
                handle.write(secrets.token_urlsafe(32))
        os.environ["APP_TOKEN"] = token_path.read_text(encoding="utf-8").strip()
    token = os.environ["APP_TOKEN"]
    if len(token) < 8:
        print("Zugangstoken ungültig: mindestens acht Zeichen erforderlich.")
        return 2
    print("FOMO Paper Desk · lokaler Start", flush=True)
    print(f"Zugangstoken: {token}", flush=True)
    print("Browser öffnet nach dem Start. Beenden mit Strg+C.", flush=True)
    url = f"http://127.0.0.1:{port}/"

    def open_when_ready():
        for _ in range(60):
            try:
                with urllib.request.urlopen(url, timeout=0.5) as response:
                    if response.status == 200:
                        webbrowser.open(url + "#t=" + token)
                        return
            except OSError:
                time.sleep(0.5)

    threading.Thread(target=open_when_ready, daemon=True).start()
    return main(["--serve", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(launch())
