"""Program physical-key input uses advertised independent support without focus."""

import tempfile
import unittest
from pathlib import Path

from computer_artist.client import Client
from computer_artist.runtime import Context
from computer_artist.workspace import Workspace
from tests.support import WindowBackend


class KeyboardBackend(WindowBackend):
    chord = Client.chord

    def __init__(self, *, lane='agent', keyboard=True):
        super().__init__()
        self.lane = lane
        self.keyboard = keyboard
        self.held_keys = set()

    def request(self, operation, **values):
        if operation == 'capabilities':
            return {
                'operations': ['key', 'focus'],
                'keyboard': self.keyboard,
                'lanes': {'agent': {'keyboard': self.keyboard}},
            }
        return super().request(operation, **values)

    def acquire(self, identity):
        super().acquire(identity)
        self.window[self.lane] = True

    def focus(self, identity):
        self.actions.append(('focus', identity))

    def key(self, code, pressed):
        self.budget -= 1
        self.actions.append(('key', code, pressed))
        if pressed:
            self.held_keys.add(code)
        else:
            self.held_keys.discard(code)


class AgentKeyboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Workspace(Path(self.temp.name) / 'window')
        self.backend = KeyboardBackend()
        self.ctx = Context(self.backend, 'w', self.store)

    def test_press_and_held_keys_use_agent_without_focus(self):
        self.ctx.press('Ctrl+S')
        self.ctx.key_down('Shift')
        self.assertEqual(self.backend.held_keys, {42})
        self.ctx.key_up('Shift')
        self.assertEqual(self.backend.held_keys, set())
        self.assertEqual(self.backend.acquire_count, 1)
        self.assertEqual(
            self.backend.actions,
            [
                ('key', 29, True),
                ('key', 31, True),
                ('key', 31, False),
                ('key', 29, False),
                ('key', 42, True),
                ('key', 42, False),
            ],
        )

    def test_legacy_support_is_rejected_before_acquisition(self):
        backend = KeyboardBackend(keyboard=False)
        ctx = Context(backend, 'w', self.store)
        for action in (lambda: ctx.press('S'), lambda: ctx.key_down('S'), lambda: ctx.key_up('S')):
            with self.assertRaisesRegex(ValueError, 'independent agent keyboard'):
                action()
        self.assertEqual(backend.acquire_count, 0)
        self.assertEqual(backend.actions, [])

    def test_clipboard_text_still_requires_explicit_host(self):
        for action in (
            lambda: self.ctx.type('text'),
            lambda: self.ctx.paste('text'),
            lambda: self.ctx.clipboard_get(),
            lambda: self.ctx.clipboard_set('text'),
        ):
            with self.assertRaises(ValueError):
                action()
        self.assertEqual(self.backend.acquire_count, 0)
        self.assertEqual(self.backend.actions, [])

    def test_invalid_duration_or_key_does_not_acquire(self):
        for duration in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.ctx.press('S', duration=duration)
        with self.assertRaises(ValueError):
            self.ctx.key_down('Ctrl+S')
        self.assertEqual(self.backend.acquire_count, 0)
        self.assertEqual(self.backend.actions, [])

    def test_explicit_host_keys_preserve_focus_behavior(self):
        backend = KeyboardBackend(lane='host')
        ctx = Context(backend, 'w', self.store)
        ctx.press('S')
        self.assertEqual(backend.actions, [('focus', 'w'), ('key', 31, True), ('key', 31, False)])


if __name__ == '__main__':
    unittest.main()
