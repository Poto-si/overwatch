"""Serve the local snapshot API and explicitly allowed web assets."""

from importlib.resources import files
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit


def make_handler(observer):
    observers = observer if isinstance(observer, dict) else {"codex": observer}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.respond(False)

        def do_HEAD(self):
            self.respond(True)

        def respond(self, head):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            host = self.headers.get("Host", "")
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin != "http://" + host):
                self.send_error(403, "Local same-origin access only")
                return
            request = urlsplit(self.path)
            path = request.path
            assets = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/index.html": ("index.html", "text/html; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
                      "/theme.css": ("theme.css", "text/css; charset=utf-8"),
                      "/assets/inter-tight-800.ttf": ("assets/inter-tight-800.ttf", "font/ttf"),
                      "/assets/openai.svg": ("assets/openai.svg", "image/svg+xml"),
                      "/assets/claude.svg": ("assets/claude.svg", "image/svg+xml")}
            if path == "/api/snapshot":
                providers = parse_qs(request.query, keep_blank_values=True).get("provider", ["codex"])
                if len(providers) != 1 or providers[0] not in observers:
                    self.send_error(400, "Unknown session provider")
                    return
                data, content_type = observers[providers[0]].snapshot(), "application/json; charset=utf-8"
            elif path in assets:
                filename, content_type = assets[path]
                data = files("overwatch").joinpath("web").joinpath(filename).read_bytes()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def log_message(self, format, *args):
            pass
    return Handler
