#!/usr/bin/env python3
"""Serve the dependency-free, read-only Regression Lab web console."""

from __future__ import annotations

import argparse
import json
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regression_lab.dashboard import DashboardRepository
from regression_lab.paths import asset_path, runtime_root


def handler_for(repository: DashboardRepository, static_root: Path):
    class ConsoleHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_root), **kwargs)

        def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def end_headers(self) -> None:
            # Console 直接读取本地源码；禁止缓存，避免刷新后继续执行旧的前端格式化逻辑。
            self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/api/dashboard": return self._json(repository.runtime_response(repository.dashboard))
            if path == "/api/trials": return self._json(repository.runtime_response(repository.trials))
            if path == "/api/experiments/latest": return self._json(repository.runtime_response(lambda: repository.latest_experiment() or {}))
            if path == "/api/gate/latest": return self._json(repository.runtime_response(lambda: repository.latest_gate() or {}))
            if path == "/api/protocol": return self._json(repository.protocol())
            if path == "/api/context": return self._json(repository.validate_runtime_context())
            if path == "/api/evolution": return self._json(repository.evolution())
            if path == "/api/policy-stop": return self._json(repository.policy_stop_evidence())
            if path == "/api/trace-diff":
                query = parse_qs(parsed.query)
                baseline = query.get("baseline", [""])[0]
                candidate = query.get("candidate", [""])[0]
                diff = repository.trace_diff(baseline, candidate)
                if diff is None:
                    return self._json({"error": "trace trial not found"}, HTTPStatus.NOT_FOUND)
                return self._json(repository.runtime_response(lambda: diff))
            if path.startswith("/api/trials/"):
                validation = repository.validate_runtime_context()
                if not validation["available"]:
                    return self._json({
                        "available": False, "reason": validation["reason"], "data": {}, "context": validation["context"],
                        **({"runtime": validation["runtime"]} if "runtime" in validation else {}),
                    })
                detail = repository.trial(unquote(path.removeprefix("/api/trials/")))
                if detail is None:
                    return self._json({"error": "trial not found"}, HTTPStatus.NOT_FOUND)
                return self._json(repository.runtime_response(lambda: detail))
            if path == "/": self.path = "/index.html"
            return super().do_GET()

        def log_message(self, format: str, *args: object) -> None:
            print("[console] " + format % args)
    return ConsoleHandler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", default=str(runtime_root() / "core-experiment-v1"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), handler_for(DashboardRepository(args.runtime), asset_path("web")))
    print(f"Observability Console: http://{args.host}:{args.port}")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
