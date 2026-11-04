"""Localhost notes app for browser bridge checks; records persist to a JSON file.

Usage: python browser_fixture.py STORE.json  (prints the URL, serves until killed)
"""

import json
import sys
import threading
import time
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
<label>Colour <select id="colour"><option value="red">Red</option><option value="blue">Blue</option><option value="disabled" disabled>Disabled</option><optgroup label="Unavailable" disabled><option value="grouped">Grouped</option></optgroup><option value="duplicate">One</option><option value="duplicate">Two</option></select></label>
<select id="disabled-select" disabled><option value="x">X</option></select>
<select id="multiple" multiple><option value="x">X</option></select>
<p id="select-events"></p>
<p id="state">Loading</p>
<ul id="notes"></ul>
<p id="late">Ready for review</p>
<a href="/other">Other page</a>
<iframe id="frame" src="/other" width="320" height="120"></iframe>
<script>
const list = document.getElementById('notes');
const events = [];
for (const name of ['input', 'change']) document.getElementById('colour').addEventListener(name, () => {
  events.push(name); document.getElementById('select-events').textContent = events.join(',');
});
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

# A drawing page like an embedded structure editor: a hover-gated toolbar and a
# canvas inside a same-origin iframe that the page scales, below a tall header,
# next to a frame from another origin (localhost instead of 127.0.0.1).
DRAW = """<!doctype html><html><head><meta charset="utf-8"><title>Draw</title>
<style>body{margin:0;font:14px sans-serif} #spacer{height:900px;background:#eee}
#stage{transform:scale(1.5);transform-origin:0 0;width:420px;height:300px;margin:20px}
#pad{width:400px;height:280px;border:4px solid #888;padding:6px}
#foreign{width:300px;height:120px;margin-top:200px}</style></head><body>
<div id="panel" style="height:80px;overflow:auto;border:1px solid #999"><div style="height:200px">Panel</div></div>
<div id="contained" style="height:60px;overflow:auto;overscroll-behavior:contain"><div style="height:100px">Contained</div></div>
<fieldset disabled style="border:0;padding:0;margin:0"><button id="off" onclick="document.title = 'clicked'">Off</button>
<a id="help" href="#helped">Help</a></fieldset>
<div id="spacer">Header</div>
<div id="stage"><iframe id="pad" src="/pad"></iframe></div>
<p id="page-events"></p>
<iframe id="foreign"></iframe>
<div style="height:1500px"></div>
<script>document.getElementById('foreign').src = 'http://localhost:' + location.port + '/other';</script>
</body></html>
"""

PAD = """<!doctype html><html><head><meta charset="utf-8"><title>Pad</title>
<style>body{margin:0;font:12px sans-serif} .tool{display:inline-block;padding:4px 8px;border:1px solid #999}
canvas{display:block;width:300px;height:200px;border:0;margin:4px 0 0 10px}</style></head><body>
<div><span class="tool" title="Line tool"><span>Line</span></span>
<span class="tool" title="Dot tool"><span>Dot</span></span> <b id="tool">none</b>
<input id="label" value="label"><button id="bold" onpointerdown="event.preventDefault()"
onclick="document.getElementById('log').textContent += 'bold:' + document.activeElement.id + ' '">B</button></div>
<canvas width="600" height="400"></canvas>
<ol id="strokes"></ol><p id="log"></p>
<script>
// Like GWT toggle buttons, a tool activates only after hover, press and release.
for (const tool of document.querySelectorAll('.tool')) {
  let hovering = false, pressed = false;
  tool.addEventListener('mouseover', () => { hovering = true; });
  tool.addEventListener('mouseout', (e) => { if (!tool.contains(e.relatedTarget)) hovering = false; });
  tool.addEventListener('mousedown', () => { pressed = hovering; });
  tool.addEventListener('mouseup', () => {
    if (pressed && hovering) document.getElementById('tool').textContent = tool.textContent.trim().toLowerCase();
    pressed = false;
  });
}
const canvas = document.querySelector('canvas');
const ctx = canvas.getContext('2d');
ctx.fillStyle = '#ff00aa'; ctx.fillRect(0, 0, 40, 40);  // a mark that only a real render shows
const strokes = document.getElementById('strokes');
const log = (text) => { document.getElementById('log').textContent += text + ' '; };
// Canvas pixels, as editors map them: client point to backing-store scale.
const at = (e) => {
  const r = canvas.getBoundingClientRect();
  return [Math.round((e.clientX - r.left) * canvas.width / r.width / 2),
          Math.round((e.clientY - r.top) * canvas.height / r.height / 2)];
};
let start = null, moves = 0;
canvas.addEventListener('mousedown', (e) => {
  if (e.button !== 0) return;
  start = at(e); moves = 0;
  if (e.shiftKey) log('shift');
});
canvas.addEventListener('mousemove', (e) => { if (start && e.buttons === 1) moves++; });
canvas.addEventListener('mouseup', (e) => {
  if (!start || e.button !== 0) return;
  const end = at(e);
  const tool = document.getElementById('tool').textContent;
  const li = document.createElement('li');
  li.textContent = tool + ' ' + start.join(',') + ' ' + end.join(',') + ' moves=' + moves;
  strokes.append(li);
  ctx.beginPath(); ctx.moveTo(start[0] * 2, start[1] * 2); ctx.lineTo(end[0] * 2, end[1] * 2); ctx.stroke();
  start = null;
});
canvas.addEventListener('dblclick', (e) => log('dblclick@' + at(e).join(',')));
canvas.addEventListener('contextmenu', (e) => { e.preventDefault(); log('contextmenu'); });
canvas.addEventListener('pointercancel', () => { log('cancel'); start = null; });
canvas.addEventListener('wheel', (e) => { e.preventDefault(); log('wheel' + e.deltaY); }, {passive: false});
</script></body></html>
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
            elif self.path == '/slow':
                time.sleep(2)  # leaves time to switch tabs while ca opens it
                self.send(200, OTHER, 'text/html; charset=utf-8')
            elif self.path == '/loading':
                self.send(
                    200,
                    '<html><body><input autofocus><img src="/slow-resource"></body></html>',
                    'text/html',
                )
            elif self.path == '/slow-resource':
                time.sleep(4)
                self.send(200, '', 'image/png')
            elif self.path == '/draw':
                self.send(200, DRAW, 'text/html; charset=utf-8')
            elif self.path == '/pad':
                self.send(200, PAD, 'text/html; charset=utf-8')
            elif self.path == '/redirect':
                self.send_response(302)
                self.send_header('location', '/other?redirected')
                self.send_header('content-length', '0')
                self.end_headers()
            elif self.path.startswith('/other'):
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
