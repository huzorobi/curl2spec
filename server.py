#!/usr/bin/env python3
"""curl2spec PRO: local server.

Serves the curl2spec UI and adds one endpoint, POST /api/capture, that drives a headless browser to log in at
a URL with supplied credentials and returns the generated login spec (see capture.py). Bound to 127.0.0.1
ONLY. This is a local operator tool, never a network service. The free client-side manual mode still works by
opening index.html directly; this server is only needed for Pro auto-capture.

Run:  python server.py            # then open http://127.0.0.1:8099
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
HOST, PORT = "127.0.0.1", int(os.environ.get("CURL2SPEC_PORT", "8099"))
_MAX_BODY = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = "curl2spec/pro"

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, (bytes, bytearray)) else str(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._serve_file("index.html", "text/html; charset=utf-8")
        if path == "/screenshot.png":
            return self._serve_file("screenshot.png", "image/png")
        if path == "/health":
            return self._send(200, json.dumps({"ok": True}))
        return self._send(404, json.dumps({"error": "not found"}))

    def _serve_file(self, name, ctype):
        fp = os.path.join(HERE, name)
        if not os.path.isfile(fp):
            return self._send(404, json.dumps({"error": f"{name} not found"}))
        with open(fp, "rb") as fh:
            return self._send(200, fh.read(), ctype)

    def do_POST(self):  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/capture":
            return self._send(404, json.dumps({"error": "not found"}))
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > _MAX_BODY:
                return self._send(413, json.dumps({"error": "request too large"}))
            body = json.loads(self.rfile.read(n).decode("utf-8", "replace") or "{}")
        except Exception as e:  # noqa: BLE001
            return self._send(400, json.dumps({"error": f"bad request: {e}"}))

        url = (body.get("url") or "").strip()
        username = (body.get("username") or "").strip()
        password = body.get("password") or ""
        if not url or not username or not password:
            return self._send(400, json.dumps({"error": "url, username and password are required"}))

        account_b = None
        b = body.get("account_b") or {}
        if b.get("username"):
            account_b = {"username": b.get("username", ""), "password": b.get("password", "")}

        admin = None
        adm = body.get("admin") or {}
        if adm.get("username"):
            admin = {"username": adm.get("username", ""), "password": adm.get("password", "")}

        try:
            from capture import capture_login, capture_register, CaptureError
            headless = bool(body.get("headless", True))
            try:
                res = capture_login(url, username, password,
                                    login_url_hint=(body.get("login_url_hint") or "").strip(),
                                    account_b=account_b, admin=admin, headless=headless)
            except CaptureError as ce:
                return self._send(200, json.dumps({"error": str(ce)}))
            # Optional: also auto-capture the signup → register spec (Pro parity with manual mode).
            if body.get("want_register"):
                base = (res.get("login_spec") or [{}])[0]
                fields = base.get("fields") or {}
                email_field = next((k for k in fields if "pass" not in k.lower()), "email")
                pw_field = next((k for k in fields if "pass" in k.lower()), "password")
                try:
                    reg = capture_register(
                        url, username, password,
                        register_url_hint=(body.get("register_url_hint") or "").strip(),
                        security_answer=(body.get("security_answer") or "").strip(),
                        login_url=base.get("login_url", ""), check_url=res.get("check_url", ""),
                        email_field=email_field, pw_field=pw_field, headless=headless)
                    res["register_spec"] = reg["register_spec"]
                    res["register_captured"] = reg.get("captured")
                    res.setdefault("notes", []).extend(reg.get("notes", []))
                except CaptureError as ce:
                    res["register_error"] = str(ce)   # non-fatal: login spec still returned
            return self._send(200, json.dumps(res))
        except Exception as e:  # noqa: BLE001
            return self._send(200, json.dumps({"error": f"{type(e).__name__}: {e}"}))

    def log_message(self, fmt, *args):   # keep the console quiet (no per-request noise, no data logged)
        return


def main():
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"curl2spec PRO: serving on http://{HOST}:{PORT}  (Ctrl+C to stop)")
    print("  free manual mode also works by opening index.html directly.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
        srv.shutdown()


if __name__ == "__main__":
    main()
