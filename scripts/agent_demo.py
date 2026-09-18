#!/usr/bin/env python3
"""Drive only the explicit Computer Artist control socket. No host-input fallback."""
import argparse
import json
import math
import socket
import time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from computer_artist import Client

parser = argparse.ArgumentParser()
parser.add_argument('socket')
parser.add_argument('--rounds', type=int, default=1)
args = parser.parse_args()
with Client(args.socket, deadline=max(60, args.rounds * 12)) as client:
    try:
        window = next(w for w in client.windows() if w['title'] == 'Agent canvas')
        with client.owned(window['id']):
            for iteration in range(args.rounds):
                canvas = next(w for w in client.windows() if w['id'] == window['id'])
                cx = canvas['x'] + canvas['width'] / 2
                cy = canvas['y'] + canvas['height'] * .58
                rx = canvas['width'] * .32
                ry = canvas['height'] * .24
                points = ((cx + rx * math.sin(i / 360 * math.tau),
                           cy + ry * math.sin(i / 360 * math.tau * 2)) for i in range(361))
                client.path(points, interval=.018)
                print(f'Stroke {iteration + 1} dispatched', flush=True)
                time.sleep(1)
        print('Existing canvas returned to human ownership.', flush=True)
    finally:
        client.request('session_close')
