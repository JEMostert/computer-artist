"""Shared window backend for isolated runtime and stream tests."""

import time

from PIL import Image


class WindowBackend:
    def __init__(self):
        self.window = {
            'id': 'w',
            'native': True,
            'visible': True,
            'agent': False,
            'pid': 1,
            'title': 'Canvas',
            'x': 100,
            'y': 200,
            'width': 100,
            'height': 80,
        }
        self.expires = time.monotonic() + 20
        self.budget = 1000
        self.failure = None
        self.lease = ''
        self.image = Image.new('RGB', (200, 160), 'white')
        self.actions = []
        self.acquire_count = 0

    def windows(self):
        self.budget -= 1
        return [dict(self.window)]

    def request(self, op, **kwargs):
        self.budget -= 1
        if op == 'capabilities':
            return {'operations': ['move', 'button', 'capture', 'scroll']}
        if op == 'capture':
            self.image.save(kwargs['path'])
            return {'ok': True}
        raise AssertionError(op)

    def acquire(self, identity):
        self.acquire_count += 1
        self.lease = 'lease'
        self.window['agent'] = True

    def click(self, x, y, button):
        self.actions.append((x, y, button))

    def move(self, x, y):
        self.actions.append((x, y))

    def path(self, points, interval):
        self.actions.extend(points)

    def button(self, code=272, pressed=True):
        self.actions.append(('button', pressed))

    def cancel(self):
        self.actions.append(('cancel',))
