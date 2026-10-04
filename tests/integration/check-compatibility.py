#!/usr/bin/env python3
"""Exercise real Qt menus in a separate KWin, with human focus elsewhere."""

import json
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from computer_artist.client import Client

s = json.loads(Path(sys.argv[1]).read_text())
assert '/ca-stock-' in s['wayland'] and s['wayland'].endswith('/wayland-test')
out = Path(s['output'])


def human(*args):
    subprocess.run(
        [str(root / 'build/stock-test-input'), s['wayland'], *map(str, args)], check=True
    )


human('move', 200, 250)
human('click')
time.sleep(0.2)
with Client(s['control']) as observer, Client(s['control']) as owner:
    canvas = next(w for w in observer.windows() if w['title'] == 'Agent canvas')
    owner.acquire(canvas['id'])
    owner.move(canvas['x'] + 180, canvas['y'] + 250)
    owner.button(code=273, pressed=True)
    owner.button(code=273, pressed=False)
    time.sleep(0.3)
    windows = observer.windows()
    (out / 'compatibility-windows.json').write_text(json.dumps(windows, indent=2))
    print(json.dumps(windows, indent=2))
    popup = next(w for w in windows if w['role'] == 'popup')
    observer.request('capture', window=canvas['id'], path=str(out / 'menu-parent.png'))
    observer.request('capture', window=popup['id'], path=str(out / 'menu-popup.png'))
    owner.move(popup['x'] + 40, popup['y'] + 12)
    owner.button(pressed=True)
    owner.button(pressed=False)
    time.sleep(0.2)
    assert any(
        json.loads(line)['event'] == 'menu_selected'
        for line in (out / 'agent-events.jsonl').read_text().splitlines()
    )
    print('Menu selected successfully')
