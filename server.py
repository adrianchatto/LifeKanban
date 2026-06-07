#!/usr/bin/env python3
"""
server.py - tiny stdlib HTTP server for the Kanban board.

Serves:
  GET  /                -> index.html (the board UI)
  GET  /api/board       -> board.json
  POST /api/board       -> overwrite board.json (full board)
  GET  /results/<file>  -> result files written by the Claude worker

No third-party dependencies. Runs on localhost only.
"""
import json
import os
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
BOARD = os.path.join(ROOT, "board.json")
RESULTS = os.path.join(ROOT, "results")
HOST = "127.0.0.1"
PORT = int(os.environ.get("KANBAN_PORT", "8787"))

MIME = {".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8",
        ".html": "text/html; charset=utf-8", ".json": "application/json",
        ".pdf": "application/pdf", ".png": "image/png", ".csv": "text/csv",
        ".webmanifest": "application/manifest+json", ".ico": "image/x-icon"}

# Static files served from the project root (for PWA install)
STATIC = ("manifest.webmanifest", "icon-192.png", "icon-512.png",
          "icon-maskable-512.png", "favicon.ico")


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass  # quiet

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            try:
                with open(os.path.join(ROOT, "index.html"), "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "index.html missing", "text/plain")
            return
        if path == "/api/board":
            try:
                with open(BOARD, "rb") as f:
                    self._send(200, f.read(), "application/json")
            except FileNotFoundError:
                self._send(200, json.dumps({"cards": []}), "application/json")
            return
        if path.startswith("/results/"):
            self._serve_result(path[len("/results/"):])
            return
        name = path.lstrip("/")
        if name in STATIC:
            fp = os.path.join(ROOT, name)
            if os.path.exists(fp):
                ext = os.path.splitext(fp)[1].lower()
                with open(fp, "rb") as f:
                    self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))
                return
            self._send(404, "not found", "text/plain")
            return
        self._send(404, "not found", "text/plain")

    def _serve_result(self, name):
        # prevent path traversal
        safe = os.path.normpath(name).lstrip("/")
        if safe.startswith("..") or "/" in safe and safe.split("/")[0] == "..":
            self._send(403, "forbidden", "text/plain")
            return
        fp = os.path.join(RESULTS, safe)
        if not os.path.abspath(fp).startswith(os.path.abspath(RESULTS)):
            self._send(403, "forbidden", "text/plain")
            return
        if not os.path.exists(fp):
            self._send(404, "result not found", "text/plain")
            return
        ext = os.path.splitext(fp)[1].lower()
        ctype = MIME.get(ext, "application/octet-stream")
        with open(fp, "rb") as f:
            data = f.read()
        if ext == ".md":
            # wrap markdown in a minimal HTML viewer
            html = MD_VIEWER.replace("__BODY__", json.dumps(data.decode("utf-8")))
            self._send(200, html, "text/html; charset=utf-8")
        else:
            self._send(200, data, ctype)

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/board":
            self._send(404, "not found", "text/plain")
            return
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
            assert isinstance(data.get("cards"), list)
        except Exception as e:
            self._send(400, json.dumps({"error": str(e)}))
            return
        tmp = BOARD + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, BOARD)
        self._send(200, json.dumps({"ok": True}))


MD_VIEWER = """<!doctype html><html><head><meta charset="utf-8">
<title>Result</title>
<style>
body{font:16px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
max-width:760px;margin:40px auto;padding:0 20px;color:#1d2330;background:#f7f8fb}
pre{white-space:pre-wrap;word-wrap:break-word}
a.back{display:inline-block;margin-bottom:20px;color:#3b6cf6;text-decoration:none}
.card{background:#fff;border:1px solid #e4e7ef;border-radius:12px;padding:28px 32px;
box-shadow:0 1px 3px rgba(20,30,60,.06)}
h1,h2,h3{line-height:1.3}
code{background:#eef1f7;padding:1px 5px;border-radius:4px}
</style></head><body>
<a class="back" href="/">&larr; Back to board</a>
<div class="card"><div id="c"></div></div>
<script>
var src = __BODY__;
// extremely small markdown -> html (headings, bold, italics, lists, code, links)
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function md(t){
  var lines = t.split(/\\n/), out=[], inList=false;
  for(var i=0;i<lines.length;i++){
    var l=lines[i];
    var h=l.match(/^(#{1,4})\\s+(.*)/);
    if(h){ if(inList){out.push('</ul>');inList=false;}
      out.push('<h'+h[1].length+'>'+inline(h[2])+'</h'+h[1].length+'>'); continue;}
    if(/^\\s*[-*]\\s+/.test(l)){ if(!inList){out.push('<ul>');inList=true;}
      out.push('<li>'+inline(l.replace(/^\\s*[-*]\\s+/,''))+'</li>'); continue;}
    if(inList){out.push('</ul>');inList=false;}
    if(l.trim()==='') out.push('<br>'); else out.push('<p>'+inline(l)+'</p>');
  }
  if(inList)out.push('</ul>');
  return out.join('\\n');
}
function inline(s){ s=esc(s);
  s=s.replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>');
  s=s.replace(/\\*([^*]+)\\*/g,'<em>$1</em>');
  s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
  s=s.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g,'<a href="$2">$1</a>');
  return s;
}
document.getElementById('c').innerHTML = md(src);
</script></body></html>"""


def main():
    os.makedirs(RESULTS, exist_ok=True)
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    url = "http://%s:%d/" % (HOST, PORT)
    print("Kanban board running at " + url)
    if "--no-browser" not in sys.argv:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
