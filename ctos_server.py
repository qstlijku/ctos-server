"""ctOS companion app - local backend stand-in.

Level 2: answers the login handshake and serves the app's config with all
Ubisoft hostnames rewritten to point back at this server.

Run:  python ctos_server.py
The app is patched to call http://192.168.1.87:8080/{version}
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import datetime
import json
import os
import re
import sys
import uuid

HOST_IP = "192.168.1.87"
PORT = 8080
SELF = f"http://{HOST_IP}:{PORT}"

# Real config captured from Ubisoft's live endpoint (ctOS appId).
# Re-fetch any time with:
#   curl -H "Ubi-AppId: <APP_ID>" \
#        https://api-ubiservices.ubi.com/v1/applications/<APP_ID>/configuration
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "app_configuration.json")

APP_ID = "c6de8e9b-9b75-4e8a-b9dd-325e8c5a721e"
SPACE_ID = "87987649-94f6-4641-a3db-68629a86703a"

# Stable fake identity so the app sees the same player across restarts.
PROFILE_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = PROFILE_ID
PLAYER_NAME = "ctOSLocal"


def now_iso(offset_hours=0):
    t = datetime.datetime.utcnow() + datetime.timedelta(hours=offset_hours)
    return t.strftime("%Y-%m-%dT%H:%M:%S.0000000Z")


def rewrite_hosts(text):
    """Point every Ubisoft URL in the config back at this server."""
    text = re.sub(r"https?://\{env\}[a-z0-9\-]*api-ubiservices\.ubi\.com", SELF, text)
    text = re.sub(r"https?://msr-\{env\}[a-z0-9\-]*api-ubiservices\.ubi\.com", SELF, text)
    text = re.sub(r"https?://useast1-\{env\}[a-z0-9\-]*api-ubiservices\.ubi\.com", SELF, text)
    text = re.sub(r"https?://\{env\}public-ubiservices\.ubi\.com", SELF, text)
    text = re.sub(r"wss?://\{env\}api-ws-ubiservices\.ubi\.com", f"ws://{HOST_IP}:{PORT}", text)
    # Rendezvous sandboxes -> us (companion + PC game)
    text = re.sub(r"https?://lb-rdv-prod\.ubi\.com", SELF, text)
    return text


def load_config():
    if not os.path.exists(CONFIG_FILE):
        return None
    with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
        return json.loads(rewrite_hosts(fh.read()))


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ---------- helpers ----------

    def _send(self, obj, code=200):
        payload = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        print(f"  << {code} {json.dumps(obj)[:400]}")
        sys.stdout.flush()

    def _log(self, body):
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n{'=' * 70}")
        print(f"[{stamp}] >> {self.command} {self.path}")
        for key, value in self.headers.items():
            if key.lower() == "authorization" and value.lower().startswith("basic "):
                try:
                    decoded = base64.b64decode(value.split(None, 1)[1]).decode("utf-8", "replace")
                    user = decoded.split(":", 1)[0]
                    value = f"Basic <user={user} password redacted>"
                except Exception:
                    value = "Basic <undecodable>"
            print(f"  {key}: {value}")
        if body:
            print(f"  -- body --\n  {body.decode('utf-8', 'replace')[:2000]}")
        sys.stdout.flush()

    # ---------- routes ----------

    def _route(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        self._log(body)

        path = self.path.split("?")[0]

        # login handshake
        if path.endswith("/profiles/sessions") and self.command == "POST":
            return self._send({
                "platformType": "uplay",
                "ticket": "LOCALTICKET." + uuid.uuid4().hex,
                "profileId": PROFILE_ID,
                "userId": USER_ID,
                "nameOnPlatform": PLAYER_NAME,
                "idOnPlatform": PROFILE_ID.upper(),
                "environment": "Prod",
                "expiration": now_iso(24),
                "serverTime": now_iso(),
                "spaceId": SPACE_ID,
                "sessionId": str(uuid.uuid4()),
                "clientIp": HOST_IP,
            })

        # app configuration (real one, hosts rewritten to us)
        if "/applications/" in path and path.endswith("/configuration"):
            cfg = load_config()
            if cfg:
                return self._send(cfg)
            print("  !! config file missing, returning empty")

        # spaces / profiles entities - empty but well-formed
        if "/spaces/" in path and path.endswith("/entities"):
            return self._send({"entities": []})
        if path.endswith("/profiles/me/friends"):
            return self._send({"friends": []})
        if "/profiles" in path and self.command == "GET":
            return self._send({"profiles": []})

        # default: valid-but-empty JSON so the app keeps going
        return self._send({})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _route

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    cfg = load_config()
    print(f"ctOS local backend on 0.0.0.0:{PORT}  (advertising {SELF})")
    print(f"config file: {'loaded' if cfg else 'NOT FOUND - ' + CONFIG_FILE}")
    sys.stdout.flush()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
