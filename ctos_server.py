"""ctOS companion app - local backend stand-in.

Level 2: answers the login handshake and serves the app's config with all
Ubisoft hostnames rewritten to point back at this server.

Run:  python ctos_server.py
The app is patched to call http://192.168.1.87:8080/{version}
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import base64
import datetime
import hashlib
import json
import os
import re
import struct
import sys
import uuid

WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

HOST_IP = "192.168.1.87"
PORT = 8080
SELF = f"http://{HOST_IP}:{PORT}"

# Real config captured from Ubisoft's live endpoint (ctOS appId).
# Re-fetch any time with:
#   curl -H "Ubi-AppId: <APP_ID>" \
#        https://api-ubiservices.ubi.com/v1/applications/<APP_ID>/configuration
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "app_configuration.json")

# The wd_companion_config space entity (game_networks, uplay account urls).
SPACES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "spaces_custom_config.json")

# Everything printed to the console is also appended here.
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "ctos_server.log")

APP_ID = "c6de8e9b-9b75-4e8a-b9dd-325e8c5a721e"
SPACE_ID = "87987649-94f6-4641-a3db-68629a86703a"

# Stable fake identity so the app sees the same player across restarts.
PROFILE_ID = "11111111-2222-3333-4444-555555555555"
USER_ID = PROFILE_ID
PLAYER_NAME = "ctOSLocal"

# Live message-delivery endpoints, keyed by profileId. Populated when a
# websocket is opened; reported back by /profiles/connections.
CONNECTIONS = {}


class Tee:
    """Write everything to the console and to LOG_FILE at the same time."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for stream in self.streams:
            try:
                stream.write(data)
                stream.flush()
            except Exception:
                pass
        return len(data)

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:
                pass


def now_iso(offset_hours=0):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=offset_hours)
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


def load_spaces():
    """The real wd_companion_config space entity (game_networks + urls)."""
    if not os.path.exists(SPACES_FILE):
        return None
    with open(SPACES_FILE, "r", encoding="utf-8") as fh:
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

    # ---------- websocket ----------

    def _ws_send(self, payload, opcode=0x1):
        """Send one unmasked server->client frame."""
        if isinstance(payload, str):
            payload = payload.encode("utf-8")
        header = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < (1 << 16):
            header.append(126)
            header += struct.pack(">H", n)
        else:
            header.append(127)
            header += struct.pack(">Q", n)
        self.connection.sendall(bytes(header) + payload)

    def _ws_read_frame(self):
        """Read one client->server frame. Returns (opcode, payload) or None."""
        hdr = self.rfile.read(2)
        if len(hdr) < 2:
            return None
        opcode = hdr[0] & 0x0F
        masked = bool(hdr[1] & 0x80)
        n = hdr[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", self.rfile.read(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self.rfile.read(8))[0]
        mask = self.rfile.read(4) if masked else b""
        data = self.rfile.read(n) if n else b""
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return opcode, data

    def _websocket(self):
        """Complete the RFC6455 handshake, then log everything the app sends.

        Opening this socket registers a "connection" - a delivery endpoint the
        peer can be found at - which /profiles/connections then reports.
        """
        # register this connection so /profiles/connections can report it
        types = re.findall(r"messageTypes=([^&]+)", self.path)
        conn_id = str(uuid.uuid4())
        CONNECTIONS[PROFILE_ID] = {
            "connectionId": conn_id,
            "profileId": PROFILE_ID,
            "applicationId": APP_ID,
            "spaceId": SPACE_ID,
            "processId": os.getpid(),
            "contactUrl": f"ws://{HOST_IP}:{PORT}/",
            "contactProtocol": "websocket",
            "messageTypes": types[0].split(",") if types else [],
            "lastModifiedDate": now_iso(),
        }
        print(f"  registered connection {conn_id} types={CONNECTIONS[PROFILE_ID]['messageTypes']}")

        key = self.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(
            hashlib.sha1((key + WS_MAGIC).encode()).digest()
        ).decode()

        self.connection.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + accept.encode() + b"\r\n\r\n"
        )
        print(f"  << 101 Switching Protocols  (websocket open)")
        sys.stdout.flush()

        try:
            while True:
                frame = self._ws_read_frame()
                if frame is None:
                    break
                opcode, data = frame
                if opcode == 0x8:                      # close
                    print("  ws << close")
                    break
                elif opcode == 0x9:                    # ping -> pong
                    self._ws_send(data, opcode=0xA)
                elif opcode == 0xA:                    # pong
                    pass
                else:
                    text = data.decode("utf-8", "replace")
                    print(f"  ws << {text[:4000]}")
                sys.stdout.flush()
        except Exception as exc:
            print(f"  ws error: {exc}")
        finally:
            print("  ws closed")
            sys.stdout.flush()
            self.close_connection = True

    # ---------- routes ----------

    def _route(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        self._log(body)

        # websocket upgrade (the CompanionPayload / CBLink / CBUnlink channel)
        if self.headers.get("Upgrade", "").lower() == "websocket":
            return self._websocket()

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

        # spaces entities - serve the real wd_companion_config if we have it
        if path.endswith("/spaces/entities") or ("/spaces/" in path and path.endswith("/entities")):
            spaces = load_spaces()
            if spaces:
                return self._send(spaces)
            return self._send({"entities": []})
        # A "connection" is a registered message-delivery endpoint, not a
        # presence flag. Field names (connectionId / processId / applicationId /
        # contactUrl / contactProtocol / lastModifiedDate / messageTypes) come
        # from strings in libWatchDogs.so. Opening the websocket with
        # ?messageTypes=... is what registers one.
        if path.endswith("/profiles/connections") or path.endswith("/connections"):
            wanted = re.findall(r"profileIds=([0-9a-fA-F\-,]+)", self.path)
            ids = wanted[0].split(",") if wanted else [PROFILE_ID]
            out = []
            for pid in ids:
                conn = CONNECTIONS.get(pid)
                if conn:
                    out.append(conn)
            return self._send({"connections": out})

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
    log_handle = open(LOG_FILE, "a", encoding="utf-8")
    sys.stdout = Tee(sys.__stdout__, log_handle)

    cfg = load_config()
    started = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'#' * 70}")
    print(f"# session started {started}")
    print(f"{'#' * 70}")
    print(f"ctOS local backend on 0.0.0.0:{PORT}  (advertising {SELF})")
    print(f"config file: {'loaded' if cfg else 'NOT FOUND - ' + CONFIG_FILE}")
    print(f"logging to:  {LOG_FILE}")

    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        log_handle.close()
