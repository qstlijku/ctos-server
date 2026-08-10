"""Minimal logging server for the ctOS companion app Hello World test.

Listens on 0.0.0.0:8080, logs every request (method, path, headers, body),
and returns 200 {} so the app keeps talking as long as possible.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import datetime
import sys


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _handle(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""

        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"\n{'=' * 70}")
        print(f"[{stamp}] >> {self.command} {self.path}")
        print(f"{'-' * 70}")
        for key, value in self.headers.items():
            print(f"  {key}: {value}")
        if body:
            try:
                print(f"  -- body --\n  {body.decode('utf-8', 'replace')[:4000]}")
            except Exception:
                print(f"  -- body (raw) --\n  {body[:2000]!r}")
        sys.stdout.flush()

        payload = b"{}"
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = _handle

    def log_message(self, *args):
        pass  # suppress default noise; we print our own


if __name__ == "__main__":
    print("ctOS logging server listening on 0.0.0.0:8080")
    print("app is patched to call http://192.168.1.87:8080/{version}")
    sys.stdout.flush()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
