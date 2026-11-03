"""Localhost notes app for browser bridge checks; records persist to a JSON file.

Usage: python browser_fixture.py STORE.json  (prints the URL, serves until killed)
"""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>CA Notes</title>
<style>body{font:16px sans-serif;margin:24px} li{margin:4px 0} #late{display:none}</style>
</head><body>
<h1>CA Notes</h1>
<form id="add">
  <label>Title <input id="title" name="title" autocomplete="off"></label>
  <label>Body <textarea id="body" name="body"></textarea></label>
  <button type="submit">Add note</button>
</form>
<p id="state">Loading</p>
<ul id="notes"></ul>
<p id="late">Ready for review</p>
<a href="/other">Other page</a>
<iframe id="frame" src="/other" width="320" height="120"></iframe>
<script>
const list = document.getElementById('notes');
async function load() {
  const notes = await (await fetch('/api/notes')).json();
  list.replaceChildren(...notes.map((n) => {
    const li = document.createElement('li');
    li.textContent = n.title + ': ' + n.body;
    return li;
  }));
  document.getElementById('state').textContent = notes.length + ' notes';
}
document.getElementById('add').addEventListener('submit', async (event) => {
  event.preventDefault();
  const title = document.getElementById('title').value;
  const body = document.getElementById('body').value;
  await fetch('/api/notes', {method: 'POST', headers: {'content-type': 'application/json'},
    body: JSON.stringify({title, body})});
  event.target.reset();
  await load();
  setTimeout(() => { document.getElementById('late').style.display = 'block'; }, 700);
});
load();
</script></body></html>
"""

OTHER = """<!doctype html><html><head><meta charset="utf-8"><title>Other page</title></head>
<body><h1>Other page</h1><button onclick="this.textContent='Pressed'">Press me</button></body></html>
"""


def serve(store):
    store = Path(store)
    lock = threading.Lock()
    if not store.exists():
        store.write_text('[]')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def send(self, status, body, kind):
            data = body.encode()
            self.send_response(status)
            self.send_header('content-type', kind)
            self.send_header('content-length', str(len(data)))
            self.send_header('cache-control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/':
                self.send(200, PAGE, 'text/html; charset=utf-8')
            elif self.path == '/other':
                self.send(200, OTHER, 'text/html; charset=utf-8')
            elif self.path == '/api/notes':
                with lock:
                    self.send(200, store.read_text(), 'application/json')
            else:
                self.send(404, 'missing', 'text/plain')

        def do_POST(self):
            if self.path != '/api/notes':
                return self.send(404, 'missing', 'text/plain')
            size = int(self.headers.get('content-length', 0))
            if size > 10000:
                return self.send(413, 'too large', 'text/plain')
            note = json.loads(self.rfile.read(size))
            with lock:
                notes = json.loads(store.read_text())
                notes.append({'title': str(note['title']), 'body': str(note['body'])})
                store.write_text(json.dumps(notes))
            self.send(201, '{}', 'application/json')

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    return server


if __name__ == '__main__':
    server = serve(sys.argv[1])
    print(f'http://127.0.0.1:{server.server_address[1]}/', flush=True)
    server.serve_forever()
