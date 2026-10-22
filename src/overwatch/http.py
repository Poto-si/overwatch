"""Serve the local snapshot API and explicitly allowed web assets."""

from importlib.resources import files
from http.server import BaseHTTPRequestHandler
import json
from urllib.parse import parse_qs, urlsplit

from .actions import SessionActions


def make_handler(observer):
    observers = observer if isinstance(observer, dict) else {"codex": observer}
    actions = {provider: SessionActions(item) for provider, item in observers.items()}
    class Handler(BaseHTTPRequestHandler):
        def action_response(self, status, body):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

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
            elif path in ("/api/session", "/api/session/delete-preview"):
                query = parse_qs(request.query, keep_blank_values=True)
                providers, identifiers = query.get("provider", []), query.get("id", [])
                if len(providers) != 1 or providers[0] not in actions or len(identifiers) != 1 or set(query) - {"provider", "id"}:
                    self.send_error(400, "Invalid session action request")
                    return
                try:
                    if path == "/api/session":
                        result = actions[providers[0]].open(identifiers[0])
                    else:
                        result = actions[providers[0]].preview(identifiers[0])
                except LookupError as exc:
                    self.action_response(404, {"error": str(exc)})
                    return
                except ValueError as exc:
                    self.action_response(409 if "session identifier" not in str(exc).lower() and "invalid session" not in str(exc).lower() else 400, {"error": str(exc)})
                    return
                except OSError:
                    self.action_response(409, {"error": "The session files changed or could not be read. Refresh the dashboard and try again."})
                    return
                data, content_type = json.dumps(result, ensure_ascii=False).encode(), "application/json; charset=utf-8"
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

        def do_POST(self):
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            hosts = self.headers.get_all("Host", [])
            origins = self.headers.get_all("Origin", [])
            lengths = self.headers.get_all("Content-Length", [])
            host = self.headers.get("Host", "")
            if (len(hosts) != 1 or len(origins) != 1 or host not in allowed
                    or origins[0] != "http://" + host):
                self.send_error(403, "Local same-origin access only")
                return
            if urlsplit(self.path).path != "/api/session/delete":
                self.send_error(404)
                return
            if self.headers.get("X-Overwatch-Action") != "1" or self.headers.get_content_type() != "application/json":
                self.send_error(403, "Explicit local action required")
                return
            length = lengths[0] if len(lengths) == 1 else ""
            if (self.headers.get_all("Transfer-Encoding") or not length.isascii()
                    or not length.isdecimal() or int(length) > 8192):
                self.send_error(400, "Invalid request body")
                return
            try:
                body = json.loads(self.rfile.read(int(length)))
            except (ValueError, UnicodeError):
                self.send_error(400, "Invalid JSON body")
                return
            if not isinstance(body, dict) or set(body) != {"provider", "id", "revision"}:
                self.send_error(400, "Invalid session action request")
                return
            provider = body.get("provider")
            if not isinstance(provider, str) or provider not in actions:
                self.send_error(400, "Unknown session provider")
                return
            try:
                result = actions[provider].delete(body.get("id"), body.get("revision"))
            except LookupError as exc:
                self.action_response(404, {"error": str(exc)})
                return
            except ValueError as exc:
                self.action_response(409 if "session identifier" not in str(exc).lower() and "invalid session" not in str(exc).lower() else 400, {"error": str(exc)})
                return
            except OSError:
                self.action_response(409, {"error": "The session files changed or could not be read. Refresh the dashboard and try again."})
                return
            self.action_response(200, result)

        def log_message(self, format, *args):
            pass
    return Handler
