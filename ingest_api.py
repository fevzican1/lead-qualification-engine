"""Minimal authenticated ingest API for GitHub payload_optimizer pushes.

Binds to loopback by default; GitHub Actions reaches it via SSH curl so the
Oracle HTTP probe budget is never spent on discovery fetches.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import config
import optimized_ingest

logger = logging.getLogger(__name__)


def _token_ok(header_value: str | None) -> bool:
    expected = str(getattr(config, "INGEST_API_TOKEN", "") or os.getenv("INGEST_API_TOKEN", "")).strip()
    if not expected:
        return False
    raw = (header_value or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw == expected


class IngestHandler(BaseHTTPRequestHandler):
    server_version = "devsolve-ingest/1"

    def log_message(self, fmt: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), fmt % args)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/health":
            self._json(200, {"ok": True})
            return
        self._json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/api/v1/ingest":
            self._json(404, {"error": "not_found"})
            return
        if not _token_ok(self.headers.get("Authorization")):
            self._json(401, {"error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > 5_000_000:
            self._json(400, {"error": "invalid_body_size"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid_json"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"error": "invalid_payload"})
            return
        try:
            stats = optimized_ingest.ingest_batch(payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ingest failed")
            self._json(500, {"error": "ingest_failed", "detail": str(exc)[:200]})
            return
        self._json(200, {"ok": True, **stats})


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = str(getattr(config, "INGEST_BIND_HOST", "127.0.0.1") or "127.0.0.1")
    port = int(getattr(config, "INGEST_API_PORT", 8787) or 8787)
    if not str(getattr(config, "INGEST_API_TOKEN", "") or "").strip():
        logger.error("INGEST_API_TOKEN is not configured")
        return 1
    server = ThreadingHTTPServer((host, port), IngestHandler)
    logger.info("Ingest API listening on http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Ingest API stopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
