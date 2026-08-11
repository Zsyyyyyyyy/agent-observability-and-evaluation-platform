#!/usr/bin/env python3
"""Serve the dependency-free, read-only Regression Lab web console."""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.dashboard import DashboardRepository


REGRESSION = Path(__file__).resolve().parents[1]


def handler_for(repository: DashboardRepository, static_root: Path):
    class ConsoleHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_root), **kwargs)

        def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path == "/api/dashboard": return self._json(repository.dashboard())
            if path == "/api/trials": return self._json(repository.trials())
            if path == "/api/experiments/latest": return self._json(repository.latest_experiment() or {})
            if path == "/api/gate/latest": return self._json(repository.latest_gate() or {})
            if path.startswith("/api/trials/"):
                detail = repository.trial(unquote(path.removeprefix("/api/trials/")))
                return self._json(detail or {"error": "trial not found"}, HTTPStatus.OK if detail else HTTPStatus.NOT_FOUND)
            if path == "/": self.path = "/index.html"
            return super().do_GET()

        def log_message(self, format: str, *args: object) -> None:
            print("[console] " + format % args)
    return ConsoleHandler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", default=str(REGRESSION / ".runtime" / "core-experiment-v1"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(DashboardRepository(args.runtime), REGRESSION / "web"))
    print(f"Observability Console: http://{args.host}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
