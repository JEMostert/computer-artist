"""Frame server for the reel renderer: serves docs/ (the page is reel/index.html, the
paintings come from assets/gallery), and writes POSTed PNG frames.

python server.py PORT OUTDIR
POST /frame?i=N  body=PNG  -> OUTDIR/NNNN.png
POST /log        body=text -> stdout
POST /done                 -> exit"""

import http.server
import os
import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
PORT = int(sys.argv[1])
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def log_message(self, *a):
        pass

    def do_POST(self):
        url = urlparse(self.path)
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        if url.path == '/frame':
            i = int(parse_qs(url.query)['i'][0])
            (OUT / f'{i:04d}.png').write_bytes(body)
        elif url.path == '/log':
            print(body.decode(errors='replace'), flush=True)
        elif url.path == '/done':
            (OUT / 'DONE').write_text('ok')
            threading.Thread(target=self.server.shutdown).start()
        self.send_response(204)
        self.end_headers()


http.server.ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
os._exit(0)
