import tempfile
import unittest
from pathlib import Path
from unittest import mock

from computer_artist.runtime import Context
from computer_artist.workspace import Workspace
from tests.support import WindowBackend

KEYMAP = {'a': [30], 'A': [42, 30], '!': [42, 2]}
OPERATIONS = ['move', 'button', 'capture', 'scroll', 'key', 'keymap', 'focus', 'clipboard_set']


class KeyboardBackend(WindowBackend):
    """Window fake that also records chords, gesture paths and button codes."""

    def __init__(self, operations=OPERATIONS, lane='agent'):
        super().__init__()
        self.lane = lane
        self.capability_reply = {'operations': list(operations), 'keyboard': True}
        self.chords = []
        self.paths = []
        self.buttons = []

    def request(self, op, **kwargs):
        if op == 'capabilities':
            self.budget -= 1
            return dict(self.capability_reply)
        return super().request(op, **kwargs)

    def keymap(self):
        return {'characters': dict(KEYMAP), 'layout': 0, 'layout_name': 'English (US)'}

    def chord(self, *codes, duration=0):
        self.chords.append(codes)

    def focus(self, window_id):
        self.actions.append(('focus', window_id))

    def path(self, points, interval=0.016, button=272):
        self.paths.append((list(points), button))

    def button(self, code=272, pressed=True):
        self.buttons.append((code, pressed))


def element(role, name, center, states=('showing', 'enabled')):
    return {'role': role, 'name': name, 'center': center, 'states': list(states)}


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = Workspace(Path(self.tmp.name) / 'window')

    def build(self, *, operations=OPERATIONS, lane='agent'):
        backend = KeyboardBackend(operations=operations, lane=lane)
        return backend, Context(backend, 'w', self.memory)


class WriteTest(RuntimeCase):
    def test_chords_dispatch_in_order_and_reply_reports_layout(self):
        backend, ctx = self.build()
        reply = ctx.write('aA!')
        self.assertEqual(backend.chords, [(30,), (42, 30), (42, 2)])
        self.assertEqual(reply['characters'], 3)
        self.assertEqual(reply['layout'], 'English (US)')

    def test_unknown_character_is_refused_before_any_key(self):
        backend, ctx = self.build()
        with self.assertRaises(ValueError) as raised:
            ctx.write('aé')
        self.assertIn('é', str(raised.exception))
        self.assertEqual(backend.chords, [])
        self.assertEqual(backend.acquire_count, 0)


class PointerTest(RuntimeCase):
    def test_click_count_two_dispatches_two_clicks_at_one_position(self):
        backend, ctx = self.build()
        result = ctx.click(relative=(0.5, 0.5), count=2, interval=0)
        self.assertEqual(result, {'status': 'dispatched', 'clicks': 2})
        self.assertEqual(backend.actions, [(150, 240, 272), (150, 240, 272)])

    def test_drag_uses_evenly_spaced_points_from_start_to_end(self):
        backend, ctx = self.build()
        ctx.drag((10, 10), (30, 10), spacing=5, interval=0)
        points, button = backend.paths[-1]
        self.assertEqual(points, [(110, 210), (115, 210), (120, 210), (125, 210), (130, 210)])
        self.assertEqual(button, 272)

    def test_middle_button_until_path_presses_and_releases_with_code_274(self):
        backend, ctx = self.build()
        points = [(10, 10), (20, 10)]
        result = ctx.path(points, button='middle', interval=0, until=lambda _: False)
        self.assertEqual(result['status'], 'dispatched')
        self.assertEqual(backend.buttons, [(274, True), (274, False)])
        observed = ctx.path(points, button='middle', interval=0, until=lambda _: True)
        self.assertEqual(observed['status'], 'condition_observed')
        self.assertEqual(backend.buttons[-2:], [(274, True), (274, False)])


class ElementTest(RuntimeCase):
    def test_find_returns_the_single_showing_match(self):
        _, ctx = self.build()
        ok = element('push button', 'OK', [5, 6])
        hidden = element('push button', 'OK', [9, 9], states=())
        with mock.patch.object(Context, 'accessible', return_value=[ok, hidden]):
            self.assertIs(ctx.find(role='push button', name='OK'), ok)

    def test_find_refuses_zero_or_several_matches(self):
        _, ctx = self.build()
        with mock.patch.object(Context, 'accessible', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'matched 0'):
                ctx.find(role='push button')
        two = [element('push button', 'OK', [1, 1]), element('push button', 'OK', [2, 2])]
        with mock.patch.object(Context, 'accessible', return_value=two):
            with self.assertRaisesRegex(ValueError, 'matched 2'):
                ctx.find(role='push button')


if __name__ == '__main__':
    unittest.main()
