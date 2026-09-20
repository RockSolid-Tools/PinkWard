"""Local server for the dashboard: serves it and cleans what is marked safe.

Opened as a file (file://) the dashboard cannot delete anything: the browser
will not allow it, and rightly so. To clean from it, PinkWard serves it on
http://127.0.0.1 while it keeps running. Precautions:

  * It only listens on 127.0.0.1: nothing outside this PC can connect.
  * Every run makes a random token. Without it the page is not served, and
    cleanups require it in a header of their own: another site open in your
    browser cannot trigger them (nor put the dashboard in an iframe).
  * It checks the Host and Origin headers (guards against DNS rebinding).
  * The browser only sends ids. What gets deleted, and where, is decided by
    the scan (cleaner.plan), never by the request.
"""

from __future__ import annotations

import http.server
import json
import secrets
import shutil
import threading
import urllib.parse

import cleaner
from report import ascii_text, human

_API_TAG = '<script id="api" type="application/json">null</script>'
_CSP = ("default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; "
        "frame-ancestors 'none'")


class DashboardServer:
    def __init__(self, html_path: str, actions: dict, target: str, log_path: str) -> None:
        self.html_path = html_path
        self.actions = actions        # {dashboard node id: cleaner.Action}
        self.target = target          # to report free space after cleaning
        self.log_path = log_path
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()  # one cleanup at a time
        self.page = b""
        self.hosts: set = set()
        self.origins: set = set()
        self.httpd = None

    def start(self) -> str:
        """Start in the background and return the URL (token included)."""
        with open(self.html_path, encoding="utf-8") as fh:
            html = fh.read()
        config = json.dumps({"token": self.token})
        self.page = html.replace(
            _API_TAG, f'<script id="api" type="application/json">{config}</script>', 1
        ).encode("utf-8")

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.daemon_threads = True
        self.httpd.owner = self
        port = self.httpd.server_address[1]
        self.hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        self.origins = {f"http://{host}" for host in self.hosts}
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{port}/?t={self.token}"

    def wait(self) -> None:
        """Block until the user presses Enter (or Ctrl+C)."""
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def clean(self, ids: list[int]) -> dict:
        results = {}
        with self.lock:
            for ident in ids:
                action = self.actions.get(ident)
                if action is None:
                    results[str(ident)] = {"ok": False, "message":
                                           "This is not something the app can clean."}
                    continue
                out = cleaner.run(action)
                cleaner.log(self.log_path, action, out)
                results[str(ident)] = out
                print(f"  Cleaned: {ascii_text(action.rule.label)}  {human(out['freed'])}"
                      f"  ({ascii_text(out['message'])})  {action.path}", flush=True)
        try:
            usage = shutil.disk_usage(self.target)
            disk = {"total": usage.total, "used": usage.used, "free": usage.free}
        except OSError:
            disk = None
        return {"results": results, "disk": disk}


class _Handler(http.server.BaseHTTPRequestHandler):
    def version_string(self) -> str:
        return "PinkWard"

    def log_message(self, format, *args) -> None:   # no noise in the console
        pass

    def _reply(self, code: int, body, ctype: str = "application/json; charset=utf-8") -> None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        self.wfile.write(data)

    def _denied(self, reason: str) -> None:
        self._reply(403, {"error": reason})

    def _same(self, given: str, expected: str) -> bool:
        return secrets.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))

    def do_GET(self) -> None:
        owner = self.server.owner
        if self.headers.get("Host", "") not in owner.hosts:
            return self._denied("host")
        url = urllib.parse.urlsplit(self.path)
        if url.path != "/":
            return self._reply(404, {"error": "not found"})
        token = urllib.parse.parse_qs(url.query).get("t", [""])[0]
        if not self._same(token, owner.token):
            return self._reply(403, b"Invalid link: use the one PinkWard shows in the "
                                    b"console.", "text/plain; charset=utf-8")
        self._reply(200, owner.page, "text/html; charset=utf-8")

    def do_POST(self) -> None:
        owner = self.server.owner
        if self.headers.get("Host", "") not in owner.hosts:
            return self._denied("host")
        origin = self.headers.get("Origin")
        if origin is not None and origin not in owner.origins:
            return self._denied("origin")
        if not self._same(self.headers.get("X-PinkWard-Token", ""), owner.token):
            return self._denied("token")
        if urllib.parse.urlsplit(self.path).path != "/api/clean":
            return self._reply(404, {"error": "not found"})
        if self.headers.get_content_type() != "application/json":
            return self._reply(415, {"error": "JSON expected"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if not 0 < length <= 65536:
            return self._reply(400, {"error": "size"})
        try:
            body = json.loads(self.rfile.read(length))
            ids = [int(i) for i in body["ids"]][:1000]
        except (ValueError, KeyError, TypeError):
            return self._reply(400, {"error": "bad request"})
        self._reply(200, owner.clean(ids))
