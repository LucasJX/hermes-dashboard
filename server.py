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
    def do_GET(self):
        if self.path.startswith("/api/"):
            # Proxy to backend
            url = f"http://127.0.0.1:{BACKEND_PORT}{self.path}"
            try:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
            except urllib.error.URLError as e:
                self.send_error(502, f"Backend error: {e}")
        elif self.path == "/" or self.path == "/index.html":
            self.serve_index()
        elif self.path.startswith("/css/") or self.path.startswith("/js/"):
            # Static files
            self.serve_static(self.path)
        else:
            # SPA fallback
            self.serve_index()

    def do_POST(self):
        if self.path.startswith("/api/"):
            url = f"http://127.0.0.1:{BACKEND_PORT}{self.path}"
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len) if content_len else b""
            try:
                req = urllib.request.Request(url, data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST")
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                    self.send_response(resp.status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
            except urllib.error.URLError as e:
                self.send_error(502, f"Backend error: {e}")
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
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
