"""The console's HTTP server.

Standard library only, deliberately. A web framework two days before a talk is a
resolution risk with no payoff: the API here is five endpoints returning JSON, and
`http.server` serves that without adding a single dependency to a project whose
`pyproject.toml` already argues at length against unnecessary ones.

It binds to loopback and refuses any request whose Host header is not loopback, which is
what stops a page on the open internet from driving it through DNS rebinding. That matters
more here than in a normal dev server, because one of these endpoints approves a send.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from cos.console import gates, graph, outbox_view
from cos.logging import get_logger

log = get_logger("console.server")

STATIC = Path(__file__).parent / "static"

ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}

class ConsoleBindError(RuntimeError):
    """The console could not take its port. Reported as a sentence, not a traceback."""


CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


class ConsoleHandler(BaseHTTPRequestHandler):
    server_version = "cos-console"

    def __init__(self, *args: Any, repo: str, dry_run: bool, **kwargs: Any) -> None:
        self.repo = repo
        self.dry_run = dry_run
        super().__init__(*args, **kwargs)

    # —— plumbing ————————————————————————————————————————————————

    def log_message(self, fmt: str, *args: Any) -> None:
        # The default handler writes to stderr and would bury the operator's own output
        # on stage. Route it through structlog at debug instead.
        log.debug("http", detail=fmt % args)

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        return host in ALLOWED_HOSTS

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status=status)

    # —— routes ——————————————————————————————————————————————————

    def do_GET(self) -> None:
        if not self._host_ok():
            self._error(403, "This console only accepts loopback requests.")
            return

        parsed = urlparse(self.path)
        route = parsed.path

        if route in {"/", "/index.html"}:
            self._static("index.html")
            return
        if route.startswith("/static/"):
            self._static(route[len("/static/") :])
            return

        if route == "/api/runs":
            self._json({"runs": graph.list_runs()})
            return

        if route == "/api/run":
            query = parse_qs(parsed.query)
            run_id = (query.get("id") or [""])[0] or graph.latest_run()
            if not run_id:
                self._json({"run_id": None, "nodes": [], "edges": [], "calls": []})
                return
            self._json(graph.build(run_id))
            return

        if route == "/api/outbox":
            self._json(outbox_view.pending())
            return

        if route == "/api/gates":
            payload = gates.state(self.repo)
            payload["dry_run"] = self.dry_run
            self._json(payload)
            return

        self._error(404, "No such endpoint.")

    def do_POST(self) -> None:
        if not self._host_ok():
            self._error(403, "This console only accepts loopback requests.")
            return

        # A browser will not send this header cross-origin without a preflight, and this
        # server answers no preflight. It is a cheap second lock on the write routes.
        if self.headers.get("X-Console") != "1":
            self._error(403, "Missing console header.")
            return

        route = urlparse(self.path).path
        body = self._read_json()
        if body is None:
            self._error(400, "Expected a JSON body.")
            return

        try:
            if route == "/api/approve":
                run_db_id = int(body["run_id"])
                environment_id = int(body["environment_id"])
                comment = str(body.get("comment") or "Approved from the Chief of Staff console.")
                self._json(gates.approve(self.repo, run_db_id, environment_id, comment))
                return

            if route == "/api/merge":
                self._json(gates.merge_pull(self.repo, int(body["number"])))
                return
        except (KeyError, TypeError, ValueError):
            self._error(400, "Malformed request.")
            return
        except gates.GateError as exc:
            # A refused approval is the system working. Report it verbatim.
            self._error(409, str(exc))
            return

        self._error(404, "No such endpoint.")

    def _read_json(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length <= 0 or length > 1_000_000:
            return None
        try:
            loaded = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def _static(self, name: str) -> None:
        path = (STATIC / name).resolve()
        if not path.is_file() or STATIC.resolve() not in path.parents:
            self._error(404, "Not found.")
            return
        ctype = CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self._send(200, path.read_bytes(), ctype)


def serve(
    *,
    repo: str,
    dry_run: bool,
    host: str = "127.0.0.1",
    port: int = 7378,
    open_browser: bool = True,
) -> None:
    handler = partial(ConsoleHandler, repo=repo, dry_run=dry_run)
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        # A console left running from the last rehearsal is the likeliest cause, and a
        # forty-line traceback is the last thing anyone wants on a projector.
        raise ConsoleBindError(
            f"Port {port} is already in use — most likely a console from an earlier run.\n"
            f"  Find it:  lsof -nP -iTCP:{port} -sTCP:LISTEN\n"
            f"  Or pick another port:  cos console --port {port + 1}"
        ) from exc
    url = f"http://{host}:{port}/"

    log.info("console listening", url=url, repo=repo, dry_run=dry_run)
    print(f"\n  Chief of Staff console  →  {url}")
    print(f"  repo {repo}   dry_run={dry_run}")
    print("  ctrl-c to stop\n")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  console stopped\n")
    finally:
        httpd.server_close()
