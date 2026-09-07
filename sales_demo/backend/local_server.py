"""Servidor HTTP stdlib para contract/E2E local, nunca para despliegue."""

from __future__ import annotations

import argparse
import json
import logging
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .lambda_api import handle_event
from .memory import build_local_bundle


LOGGER = logging.getLogger("sales_demo.local")
_BUNDLE = build_local_bundle(now=datetime.now(timezone.utc), auto_finalize=True)


class Handler(BaseHTTPRequestHandler):
    server_version = "APPROVALSSalesLocal/1.0"

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def _dispatch(self) -> None:
        # The standalone demo uses wall time; unit tests keep an injected clock.
        _BUNDLE.clock.value = datetime.now(timezone.utc)
        length = min(int(self.headers.get("content-length", "0")), 16_385)
        raw_body = self.rfile.read(length).decode("utf-8") if length else None
        subject = self.headers.get("x-demo-sub")
        event = {
            "version": "2.0",
            "rawPath": urlsplit(self.path).path,
            "headers": {key.lower(): value for key, value in self.headers.items()},
            "body": raw_body,
            "isBase64Encoded": False,
            "requestContext": {
                "requestId": f"local-{uuid.uuid4().hex[:16]}",
                "http": {"method": self.command, "path": urlsplit(self.path).path},
                "authorizer": {"jwt": {"claims": {"sub": subject}}} if subject else {},
            },
        }
        result = handle_event(event, _BUNDLE.api)
        body = result["body"].encode("utf-8")
        self.send_response(result["statusCode"])
        for key, value in result["headers"].items():
            self.send_header(key, value)
        self._cors_headers()
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self) -> None:
        self.send_header("access-control-allow-origin", "http://127.0.0.1:5173")
        self.send_header("access-control-allow-methods", "GET,POST,OPTIONS")
        self.send_header("access-control-allow-headers", "authorization,content-type,idempotency-key,x-demo-sub")
        self.send_header("vary", "origin")

    def log_message(self, format: str, *args) -> None:
        LOGGER.info(json.dumps({"event": "local_http", "message": format % args}))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the APPROVALS sales pilot local API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    LOGGER.info(json.dumps({"event": "local_server_started", "host": args.host, "port": args.port}))
    server.serve_forever()


if __name__ == "__main__":
    main()
