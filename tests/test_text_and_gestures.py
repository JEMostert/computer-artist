import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from computer_artist.client import Client
from computer_artist.input import KEYS, parse_chord
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

    def test_newline_and_tab_are_physical_enter_and_tab(self):
        backend, ctx = self.build()
        ctx.write('\n\t')
        self.assertEqual(backend.chords, [(28,), (15,)])

    def test_crlf_and_lone_cr_are_one_enter(self):
        backend, ctx = self.build()
        ctx.write('a\r\nA')
        self.assertEqual(backend.chords, [(30,), (28,), (42, 30)])
        backend.chords.clear()
        ctx.write('\r')
        self.assertEqual(backend.chords, [(28,)])

    def test_unknown_character_is_refused_before_any_key(self):
        backend, ctx = self.build()
        with self.assertRaises(ValueError) as raised:
            ctx.write('aé')
        self.assertIn('é', str(raised.exception))
        self.assertEqual(backend.chords, [])
        self.assertEqual(backend.acquire_count, 0)

    def test_empty_or_oversized_text_is_refused(self):
        _, ctx = self.build()
        for text in ('', 'a' * 4097):
            with self.subTest(length=len(text)):
                with self.assertRaises(ValueError):
                    ctx.write(text)

    def test_plugin_without_keymap_operation_asks_for_rebuild(self):
        backend, ctx = self.build(operations=['move', 'button', 'capture', 'key'])
        with self.assertRaisesRegex(ValueError, 'rebuild'):
            ctx.write('a')
        self.assertEqual(backend.chords, [])

    def test_agent_type_uses_the_write_path(self):
        _, ctx = self.build()
        with mock.patch.object(ctx, 'write', return_value={'status': 'dispatched'}) as write:
            self.assertEqual(ctx.type('ab'), {'status': 'dispatched'})
        write.assert_called_once_with('ab', interval=0.0)

    def test_host_type_pastes_and_refuses_interval(self):
        backend, ctx = self.build(lane='host')
        with mock.patch.object(ctx, 'paste', return_value={'status': 'pasted'}) as paste:
            self.assertEqual(ctx.type('hello'), {'status': 'pasted'})
            with self.assertRaises(ValueError):
                ctx.type('hello', interval=0.1)
        paste.assert_called_once_with('hello')
        self.assertEqual(backend.chords, [])


class PointerTest(RuntimeCase):
    def test_click_count_two_dispatches_two_clicks_at_one_position(self):
        backend, ctx = self.build()
        result = ctx.click(relative=(0.5, 0.5), count=2, interval=0)
        self.assertEqual(result, {'status': 'dispatched', 'clicks': 2})
        self.assertEqual(backend.actions, [(150, 240, 272), (150, 240, 272)])

    def test_click_rejects_bad_counts_and_intervals(self):
        _, ctx = self.build()
        for kwargs in ({'count': 0}, {'count': 4}, {'count': 2, 'interval': 0.31}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    ctx.click(relative=(0.5, 0.5), **kwargs)

    def test_double_click_is_a_count_of_two(self):
        backend, ctx = self.build()
        ctx.double_click(relative=(0.5, 0.5), interval=0)
        double = list(backend.actions)
        backend.actions.clear()
        ctx.click(relative=(0.5, 0.5), count=2, interval=0)
        self.assertEqual(double, backend.actions)

    def test_drag_uses_evenly_spaced_points_from_start_to_end(self):
        backend, ctx = self.build()
        ctx.drag((10, 10), (30, 10), spacing=5, interval=0)
        points, button = backend.paths[-1]
        self.assertEqual(points, [(110, 210), (115, 210), (120, 210), (125, 210), (130, 210)])
        self.assertEqual(button, 272)

    def test_right_button_drag_passes_code_273(self):
        backend, ctx = self.build()
        ctx.drag((10, 10), (30, 10), button='right', spacing=5, interval=0)
        self.assertEqual(backend.paths[-1][1], 273)

    def test_drag_rejects_non_finite_coordinates(self):
        _, ctx = self.build()
        for start, end in (((float('nan'), 10), (30, 10)), ((10, 10), (float('inf'), 10))):
            with self.subTest(start=start, end=end):
                with self.assertRaises(ValueError):
                    ctx.drag(start, end)

    def test_middle_button_path_without_until_uses_code_274(self):
        backend, ctx = self.build()
        ctx.path([(10, 10), (20, 10)], button='middle', interval=0)
        self.assertEqual(backend.paths[-1][1], 274)

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
    def test_point_resolves_element_center_relative_to_window_origin(self):
        _, ctx = self.build()
        self.assertEqual(ctx._point(element={'center': [5, 6]}), (105, 206))

    def test_element_without_center_is_refused(self):
        _, ctx = self.build()
        with self.assertRaises(ValueError):
            ctx._point(element={'name': 'OK', 'center': None})

    def test_two_point_sources_are_refused(self):
        _, ctx = self.build()
        with self.assertRaises(ValueError):
            ctx._point(relative=(0.1, 0.1), element={'center': [5, 6]})

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

    def test_find_excludes_non_showing_unless_showing_is_false(self):
        _, ctx = self.build()
        ok = element('push button', 'OK', [5, 6])
        hidden = element('push button', 'OK', [9, 9], states=())
        with mock.patch.object(Context, 'accessible', return_value=[ok, hidden]):
            with self.assertRaisesRegex(ValueError, 'matched 2'):
                ctx.find(role='push button', showing=False)

    def test_find_requires_role_or_name(self):
        _, ctx = self.build()
        with self.assertRaises(ValueError):
            ctx.find()


class AccessibleTest(RuntimeCase):
    def test_missing_application_tree_asks_to_enable_accessibility(self):
        _, ctx = self.build()
        reply = {'application': None, 'frame': None, 'frames': [], 'elements': []}
        with mock.patch('computer_artist.accessibility.elements', return_value=reply):
            with self.assertRaisesRegex(ValueError, 'enable'):
                ctx.accessible()

    def test_unmatched_frame_lists_the_available_frames(self):
        _, ctx = self.build()
        reply = {'application': 'app', 'frame': None, 'frames': ['Other'], 'elements': []}
        with mock.patch('computer_artist.accessibility.elements', return_value=reply):
            with self.assertRaisesRegex(ValueError, "'Other'"):
                ctx.accessible()

    def test_passes_window_identity_deadline_and_bus_address(self):
        _, ctx = self.build()
        ok = element('push button', 'OK', [5, 6])
        reply = {'application': 'app', 'frame': 'frame', 'frames': ['Canvas'], 'elements': [ok]}
        bus = 'unix:path=/tmp/bus'
        with mock.patch.dict(os.environ, {'CA_ACCESSIBILITY_BUS': bus}):
            with mock.patch(
                'computer_artist.accessibility.elements', return_value=reply
            ) as elements:
                self.assertEqual(ctx.accessible(role='push button', name='OK'), [ok])
        elements.assert_called_once_with(
            1,
            'Canvas',
            role='push button',
            name='OK',
            max_nodes=2000,
            deadline=ctx.client.expires,
            session_address=bus,
        )


class StopReasonTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.socket_path = str(Path(self.tmp.name) / 'compositor.sock')
        # Built without connecting: no heartbeat thread against the fake server.
        self.client = Client.__new__(Client)
        self.client.socket_path, self.client.lane = self.socket_path, 'agent'
        self.requests = []

    def serve(self, reply):
        server = socket.socket(socket.AF_UNIX)
        self.addCleanup(server.close)
        server.bind(self.socket_path)
        server.listen(1)
        server.settimeout(5)

        def answer():
            connection, _ = server.accept()
            with connection:
                self.requests.append(json.loads(connection.makefile('rb').readline()))
                connection.sendall(reply)

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)

    def test_reports_the_lane_stop_reason(self):
        reply = {'ok': True, 'lanes': {'agent': {'stop_reason': 'watchdog'}}}
        self.serve(json.dumps(reply).encode() + b'\n')
        self.assertEqual(self.client.stop_reason(), 'watchdog')
        self.assertEqual(self.requests, [{'op': 'session_status', 'lane': 'agent'}])

    def test_empty_reason_is_none(self):
        self.serve(b'{"ok": true, "lanes": {"agent": {"stop_reason": ""}}}\n')
        self.assertIsNone(self.client.stop_reason())

    def test_malformed_reply_is_none(self):
        self.serve(b'not json\n')
        self.assertIsNone(self.client.stop_reason())

    def test_missing_compositor_is_none(self):
        self.assertIsNone(self.client.stop_reason())


class ParseChordTest(unittest.TestCase):
    def test_named_chords_resolve_to_linux_codes(self):
        self.assertEqual(parse_chord('Ctrl+F13'), [29, 183])
        self.assertEqual(parse_chord('KP5'), [76])
        self.assertEqual(parse_chord('AltGr+e'), [100, 18])

    def test_every_named_key_is_a_valid_linux_code(self):
        invalid = {name: code for name, code in KEYS.items() if not 1 <= code <= 247}
        self.assertEqual(invalid, {})


if __name__ == '__main__':
    unittest.main()
