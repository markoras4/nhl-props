"""Local server for testing the paywall: serves the site built by tools/paywall/localsite.py and swaps the
real Supabase client for tools/paywall/mock-supabase.js, so each plan can be tried without real accounts.

    py tools/paywall/devserver.py .local-test/site 8787
    http://localhost:8787/app/?as=visitor   (or free, pro, creator, media)

Pro files are served from <site>/../pro/ under /__dev/pro/ only to requests the mock makes for Pro plans;
the real protection is the pro-data edge function (supabase/functions/pro-data).
"""
from __future__ import annotations

import http.server
import sys
from functools import partial
from pathlib import Path

HERE = Path(__file__).resolve().parent
CDN = '<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.js"></script>'


class Handler(http.server.SimpleHTTPRequestHandler):
    site: Path
    pro: Path

    def log_message(self, *a):  # quiet
        pass

    def _send(self, body: bytes, ctype: str, code: int = 200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].split("#")[0]
        if path == "/__dev/mock-supabase.js":
            return self._send((HERE / "mock-supabase.js").read_bytes(), "application/javascript")
        if path.startswith("/__dev/pro/"):
            rel = path[len("/__dev/pro/"):]
            f = (self.pro / rel).resolve()
            if self.pro.resolve() in f.parents and f.exists() and f.suffix == ".json":
                return self._send(f.read_bytes(), "application/json")
            return self._send(b'{"error":"not found"}', "application/json", 404)
        if path in ("/app", "/app/", "/app/index.html"):
            html = (self.site / "app" / "index.html").read_text(encoding="utf-8")
            html = html.replace(CDN, '<script src="/__dev/mock-supabase.js"></script>')
            return self._send(html.encode("utf-8"), "text/html; charset=utf-8")
        # clean URLs like Vercel (cleanUrls: true)
        if "." not in path.rsplit("/", 1)[-1] and not path.endswith("/") and (self.site / (path.lstrip("/") + ".html")).exists():
            self.path = path + ".html"
        return super().do_GET()


def main(site: str, port: int = 8787):
    s = Path(site).resolve()
    Handler.site, Handler.pro = s, s.parent / "pro"
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(s)))
    print(f"Paywall test server: http://localhost:{port}/app/?as=visitor|free|pro|creator|media")
    srv.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 8787)
