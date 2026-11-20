"""A controllable local HTTP server used to simulate availability and outages
without depending on the internet."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
import threading
import time


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/ok":
            self._send(200, b"OK")
        elif path == "/fail":
            self._send(500, b"Internal server error")
        elif path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "/ok")
            self.end_headers()
        elif path == "/redirect-private":
            # redirecting towards a forbidden address must be blocked by SSRF
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/" % self.server.port_9)
            self.end_headers()
        elif path == "/redirect-loop":
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
        elif path == "/sleep":
            try:
                seconds = float(parse_qs(parsed.query)["s"][0])
            except (KeyError, ValueError):
                seconds = 1.0
            time.sleep(seconds)
            self._send(200, b"done")
        else:
            self._send(404, b"not found")

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # noqa: ANN002
        pass


@pytest.fixture(scope="session")
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.port_9 = 9  # port 9 (discard) — connection refused expected
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()