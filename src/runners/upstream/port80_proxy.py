#!/usr/bin/env python3
"""Tiny HTTP proxy for local runner compatibility spikes."""

import http.client
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

TARGET = os.environ["GITHUB_EMULATOR_PORT80_PROXY_TARGET"].rstrip("/")
TARGET_URL = urlparse(TARGET)


class ProxyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self._proxy()

    def do_POST(self):
        self._proxy()

    def do_PATCH(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()

    def do_OPTIONS(self):
        self._proxy()

    def _proxy(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length else None
        path = self.path
        conn = http.client.HTTPConnection(
            TARGET_URL.hostname,
            TARGET_URL.port or 80,
            timeout=120,
        )
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in ("host", "content-length")
        }
        if self.headers.get("Host"):
            headers["Host"] = self.headers["Host"]
        if body is not None:
            headers["Content-Length"] = str(len(body))
        conn.request(self.command, path, body=body, headers=headers)
        response = conn.getresponse()
        data = response.read()
        self.send_response(response.status)
        for key, value in response.getheaders():
            if key.lower() not in ("transfer-encoding", "connection"):
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)
        conn.close()

    def log_message(self, fmt, *args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 80), ProxyHandler).serve_forever()
