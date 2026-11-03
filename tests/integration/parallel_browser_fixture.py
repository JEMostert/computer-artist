"""Disposable three-agent form. Run with a JSONL store path; prints its URL."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import time
from urllib.parse import urlparse

PAGE = """<!doctype html><meta charset="utf-8"><title>CA parallel AGENT</title>
<style>body{font:20px system-ui;max-width:720px;margin:40px auto}label{display:block;margin:20px}input,textarea,select,button{font:inherit}pre{white-space:pre-wrap}</style>
<h1>Watch CA agent AGENT</h1><p>Switching tabs is watching. Page clicks or keys take over this tab.</p>
<form><label>Name <input name="name" required></label>
<label>Note <textarea name="note" required></textarea></label>
<label><input name="agree" type="checkbox" required> Agree</label>
<label>Colour <select name="colour" required><option value="">Choose</option>
<option value="red">Red</option><option value="green">Green</option><option value="blue">Blue</option></select></label>
<button>Save submission</button></form><pre id="result">Waiting for submission</pre>
<script>
const events=[];
for(const type of ['input','change']) document.querySelector('select').addEventListener(type,e=>events.push({type:e.type,value:e.target.value,trusted:e.isTrusted}));
document.querySelector('form').addEventListener('submit',async e=>{
e.preventDefault(); const data=Object.fromEntries(new FormData(e.target));
data.agent='AGENT'; data.events=events;
const r=await fetch('/save',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
document.querySelector('#result').textContent=r.ok?'Saved '+JSON.stringify(data):'Failed';
});
</script>"""


def serve(store):
    store = Path(store)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, code, body, kind='text/html'):
            body = body.encode()
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            agent = urlparse(self.path).path.strip('/')
            if agent not in ('agent-a', 'agent-b', 'agent-c'):
                return self.reply(404, 'Missing')
            self.reply(200, PAGE.replace('AGENT', agent))

        def do_POST(self):
            size = int(self.headers.get('Content-Length', 0))
            if self.path != '/save' or not 0 < size <= 10000:
                return self.reply(400, 'Invalid request')
            data = json.loads(self.rfile.read(size))
            data['saved_at'] = time()
            with lock, store.open('a') as stream:
                stream.write(json.dumps(data) + '\n')
                stream.flush()
            self.reply(201, '{}', 'application/json')

    return ThreadingHTTPServer(('127.0.0.1', 0), Handler)


if __name__ == '__main__':
    server = serve(sys.argv[1])
    print(f'http://127.0.0.1:{server.server_port}', flush=True)
    server.serve_forever()
