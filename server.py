#!/usr/bin/env python3
"""
Hermes Dashboard — Frontend server + API proxy (port 3800)
Serves static files and proxies /api/* to backend on 3801.
"""

import os, sys, http.server, socketserver, urllib.request, urllib.error, urllib.parse
from http.server import HTTPServer

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "frontend")
BACKEND_PORT = 3801
FRONTEND_PORT = 3800

class Proxy(http.server.BaseHTTPRequestHandler):
    def _proxy(self, method):
        if self.path.startswith("/api/"):
            url = f"http://127.0.0.1:{BACKEND_PORT}{self.path}"
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len else b""

            # Build headers to forward (including Cookie + Authorization for credentials)
            fwd_headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
            cookie = self.headers.get("Cookie")
            if cookie:
                fwd_headers["Cookie"] = cookie
            auth = self.headers.get("Authorization")
            if auth:
                fwd_headers["Authorization"] = auth
            origin = self.headers.get("Origin") or self.headers.get("Referer", "")
            if origin:
                # Strip path to get origin
                from urllib.parse import urlparse
                parsed = urlparse(origin)
                fwd_headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"

            try:
                req = urllib.request.Request(url, data=body, headers=fwd_headers, method=method)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")

                    # Forward Set-Cookie from backend so browser stores session cookie
                    sc = resp.headers.get("Set-Cookie")
                    if sc:
                        self.send_header("Set-Cookie", sc)

                    # CORS: allow credentials when browser sends origin
                    allow_origin = fwd_headers.get("Origin", "*")
                    self.send_header("Access-Control-Allow-Origin", allow_origin)
                    self.send_header("Access-Control-Allow-Credentials", "true")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
                    self.send_header("Access-Control-Allow-Headers",
                                     "Content-Type, Cookie, Authorization, X-Requested-With")
                    self.end_headers()
                    self.wfile.write(data)
            except urllib.error.HTTPError as e:
                # Pass through backend HTTP error codes (401, 403, 404, 500, etc.)
                data = e.read()
                self.send_response(e.code)
                self.send_header("Content-Type", "application/json")
                allow_origin = fwd_headers.get("Origin", "*")
                self.send_header("Access-Control-Allow-Origin", allow_origin)
                self.send_header("Access-Control-Allow-Credentials", "true")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers",
                                 "Content-Type, Cookie, Authorization, X-Requested-With")
                self.end_headers()
                self.wfile.write(data)
            except urllib.error.URLError as e:
                self.send_error(502, "Backend error: %s" % e)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy("GET")
        elif self.path == "/" or self.path == "/index.html":
            self.serve_index()
        elif self.path.startswith("/css/") or self.path.startswith("/js/"):
            self.serve_static(self.path)
        else:
            self.serve_index()

    def do_POST(self):
        if self.path.startswith("/api/"):
            self._proxy("POST")
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path.startswith("/api/"):
            self._proxy("PUT")
        else:
            self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/"):
            self._proxy("DELETE")
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        origin = self.headers.get("Origin") or "*"
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Credentials", "true")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers",
                         "Content-Type, Cookie, Authorization, X-Requested-With")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def serve_index(self):
        idx = os.path.join(FRONTEND_DIR, "index.html")
        if os.path.exists(idx):
            with open(idx, "rb") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404, "index.html not found")

    def serve_static(self, path):
        parsed = urllib.parse.urlparse(path)
        clean_path = parsed.path
        fname = os.path.basename(clean_path)
        local = os.path.join(FRONTEND_DIR, clean_path.lstrip("/"))
        if os.path.exists(local) and os.path.isfile(local):
            ext = fname.split(".")[-1]
            ctype = {"css": "text/css", "js": "application/javascript",
                     "png": "image/png", "jpg": "image/jpeg",
                     "ico": "image/x-icon", "svg": "image/svg+xml"}.get(ext, "text/plain")
            with open(local, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", f"{ctype}; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.send_header("Pragma", "no-cache")
                self.send_header("Expires", "0")
                self.end_headers()
                self.wfile.write(f.read())
        else:
            self.send_error(404, "File not found")

def main():
    print(f"[Hermes Dashboard] Frontend server on port {FRONTEND_PORT}")
    print(f"  Backend proxy: http://127.0.0.1:{BACKEND_PORT}")
    print(f"  Frontend dir: {FRONTEND_DIR}")
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", FRONTEND_PORT), Proxy) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    main()
