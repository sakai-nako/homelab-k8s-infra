"""HTTP サーバ (Alertmanager webhook receiver).

エンドポイント:
  POST /alerts  — Alertmanager webhook (version 4)。処理失敗は 500 を返して
                  Alertmanager のリトライに任せる。
  GET  /healthz — liveness/readiness probe 用。

通知処理はグローバルロックで直列化する。Alertmanager の通知頻度
(group_interval 単位) に対して Issue API 数回の処理は十分速く、
並行起票による重複 Issue を防ぐ方が重要。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .bridge import Bridge
from .forgejo import ForgejoClient, ForgejoError

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()


def make_handler(bridge: Bridge):
    class Handler(BaseHTTPRequestHandler):
        # BaseHTTPRequestHandler のデフォルトログ (stderr 直書き) を logging に寄せる
        def log_message(self, fmt, *args):  # noqa: N802
            logger.debug("%s %s", self.address_string(), fmt % args)

        def _respond(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            if self.path == "/healthz":
                self._respond(200, {"status": "ok"})
            else:
                self._respond(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/alerts":
                self._respond(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError):
                self._respond(400, {"error": "invalid json"})
                return
            try:
                with _LOCK:
                    action = bridge.handle(payload)
            except ForgejoError as exc:
                logger.error("Forgejo API エラー (AM がリトライする): %s", exc)
                self._respond(500, {"error": str(exc)})
                return
            except Exception:  # 想定外でも必ず応答を返す
                logger.exception("通知処理で想定外のエラー")
                self._respond(500, {"error": "internal error"})
                return
            self._respond(200, {"result": action})

    return Handler


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    token = os.environ.get("FORGEJO_TOKEN")
    if not token:
        raise SystemExit("FORGEJO_TOKEN が未設定 (write:issue PAT が必要)")
    base_url = os.environ.get(
        "FORGEJO_URL", "http://forgejo-http.forgejo.svc.cluster.local:3000"
    )
    repo = os.environ.get("FORGEJO_REPO", "sakai/local-infra")
    port = int(os.environ.get("PORT", "8080"))

    client = ForgejoClient(base_url=base_url, repo=repo, token=token)
    bridge = Bridge(client)
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(bridge))
    logger.info("listening on :%d (repo=%s, forgejo=%s)", port, repo, base_url)
    server.serve_forever()
