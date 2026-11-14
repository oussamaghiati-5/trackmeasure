"""Fixture server: routes by Host header. news.test = publisher, cdn.tracker.test = FP tracker,
ads.test = consent-respecting tracker. Run Chromium with --host-resolver-rules='MAP *.test 127.0.0.1:8765'."""
import http.server, sys
from pathlib import Path
ROOT = Path(__file__).parent / "fixture"
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        path = self.path.split("?")[0]
        if host == "news.test":
            f = ROOT / ("index.html" if path in ("/",) else "page.html")
            ctype = "text/html"
        elif host == "cdn.tracker.test" and path == "/fp.js":
            f, ctype = ROOT / "fp.js", "application/javascript"
        elif host == "ads.test" and path == "/ads.js":
            f, ctype = ROOT / "ads.js", "application/javascript"
        else:
            self.send_response(200); self.send_header("Content-Type", "image/gif"); self.end_headers()
            self.wfile.write(b"GIF89a"); return
        body = f.read_bytes()
        self.send_response(200); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8765), H).serve_forever()
