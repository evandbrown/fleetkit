"""A stub fixture: serves ``/`` and ``/task-products.json`` so the driver's fixture check passes offline.
Runnable by hand: ``python3 -m tests.stub_fixture --port 8081``."""
from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PRODUCTS = [
    {"product_id": "p-1001", "query": "kettle", "expected_title": "Stainless Kettle 1.7L"},
    {"product_id": "p-1002", "query": "lamp", "expected_title": "Desk Lamp, Warm White"},
    {"product_id": "p-1003", "query": "notebook", "expected_title": "Dotted Notebook A5"},
]


class Handler(BaseHTTPRequestHandler):
    products = PRODUCTS
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, status, data: bytes, ctype: str):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/" or p == "/index.html":
            return self._send(200, b"<html><body><form action='/search.html'><input name='q'></form></body></html>",
                              "text/html")
        if p == "/task-products.json":
            return self._send(200, json.dumps(self.products).encode(), "application/json")
        self._send(404, b"not found", "text/plain")


class StubFixture:
    def __init__(self, port: int = 0, products=None):
        handler = type("BoundHandler", (Handler,), {"products": products or PRODUCTS})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    a = ap.parse_args(argv)
    f = StubFixture(a.port).start()
    print(f"stub fixture listening on {f.url}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    f.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
