"""Portable macOS ownership and event-routing regressions; no desktop input."""

import ctypes
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from computer_artist.macos import MacOSBackend, cleanup_input
from computer_artist.macos_native import Native


class MacOSLeaseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.transport = Mock()
        self.transport.process_identity.return_value = '42-launch-one'
        self.accessibility = Mock()
        self.accessibility.focused_window.return_value = 7
        for target, value in (
            ('computer_artist.macos_native.Native', self.transport),
            ('computer_artist.macos_accessibility.Accessibility', self.accessibility),
        ):
            replacement = patch(target, return_value=value)
            replacement.start()
            self.addCleanup(replacement.stop)
        environment = patch.dict(
            os.environ, {'CA_MACOS_CLEANUP_FILE': str(self.root / 'input.json')}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def backend(self, lane='agent'):
        backend = MacOSBackend(lane)
        backend.root = self.root
        backend.stop_file = self.root / 'stop.json'
        return backend

    def owned(self):
        backend = self.backend()
        backend.lease, backend.pid, backend.target = 'original-owner', 42, 7
        backend.identity, backend.baseline = 'target', (10, 20, 100, 80)
        backend.pointer, backend.keys, backend.buttons = (30, 40), {42}, {272}
        backend.resolve = Mock(
            return_value={
                'kCGWindowBounds': {'X': 10, 'Y': 20, 'Width': 100, 'Height': 80},
            }
        )
        backend._lock('app-42.lock')
        return backend

    def test_connections_keep_separate_held_input_for_crash_recovery(self):
        first, second = self.backend(), self.backend()
        first.keys, second.buttons = {42}, {272}
        first.save_cleanup()
        second.save_cleanup()
        self.assertNotEqual(first.cleanup_file, second.cleanup_file)
        self.assertEqual(json.loads(Path(first.cleanup_file).read_text())['keys'], [42])
        self.assertEqual(json.loads(Path(second.cleanup_file).read_text())['buttons'], [272])

    def test_recycled_window_and_pid_cannot_reuse_old_binding(self):
        backend = self.backend()
        info = {'kCGWindowOwnerPID': 42, 'kCGWindowNumber': 7}
        old_identity = backend._identity(info)
        self.transport.process_identity.return_value = '42-launch-two'
        backend._infos = Mock(return_value=[info])
        with self.assertRaisesRegex(ValueError, 'Exact live window'):
            backend.resolve(old_identity)

    def test_stale_connection_cannot_use_or_revoke_another_lease(self):
        backend = self.owned()
        try:
            with self.assertRaises(PermissionError):
                backend.guard({'lease': 'stale-owner'})
            self.assertEqual(backend.lease, 'original-owner')
            self.transport.key.assert_not_called()
            self.transport.mouse.assert_not_called()
        finally:
            backend.close()

    def test_geometry_change_releases_held_input_and_application_lock(self):
        backend = self.owned()
        backend.resolve.return_value['kCGWindowBounds']['Width'] = 101
        with self.assertRaisesRegex(ValueError, 'geometry changed'):
            backend.guard({'lease': backend.lease})
        self.assertEqual(backend.reason, 'geometry_changed')
        self.assertFalse(backend.lease)
        self.transport.key.assert_called_once_with(42, 42, False, 0, host=False)
        self.assertEqual(self.transport.mouse.call_args.kwargs['host'], False)
        successor = self.backend()
        successor.lease = 'next-owner'
        successor._lock('app-42.lock')
        successor.close()

    def test_crash_cleanup_refuses_busy_or_replaced_owner_without_input(self):
        for newer_owner_still_running in (True, False):
            with self.subTest(newer_owner_still_running=newer_owner_still_running):
                original = self.owned()
                original.save_cleanup()
                path = Path(original.cleanup_file)
                # Simulate death: kernel releases locks, but held-state evidence remains.
                for fd in original.locks:
                    os.close(fd)
                original.locks.clear()
                original.keys.clear()
                original.buttons.clear()
                successor = self.backend()
                successor.lease = 'next-owner'
                successor._lock('app-42.lock')
                if not newer_owner_still_running:
                    successor.close()  # The lock token still records the newer owner.
                self.transport.reset_mock()
                with patch(
                    'computer_artist.macos.MacOSBackend',
                    side_effect=lambda lane: self.backend(lane),
                ):
                    with self.assertRaises(OSError):
                        cleanup_input(self.root / 'input.json')
                self.transport.key.assert_not_called()
                self.transport.mouse.assert_not_called()
                successor.close()
                path.unlink()

    def borrowing(self):
        backend = self.owned()
        backend.host_drags, backend.keys, backend.buttons = True, set(), set()
        self.transport.buttons_held.return_value = False
        self.transport.cursor.return_value = (500, 500)
        self.transport.front_pid.return_value = 99
        self.transport.front_process.side_effect = [b'human', b'human', b'target']
        self.transport.windows.return_value = [
            {
                'kCGWindowNumber': 3,
                'kCGWindowBounds': {'X': 60, 'Y': 20, 'Width': 50, 'Height': 80},
            },
            {
                'kCGWindowNumber': 7,
                'kCGWindowBounds': {'X': 10, 'Y': 20, 'Width': 100, 'Height': 80},
            },
        ]
        self.addCleanup(backend.close)
        return backend

    def kinds(self):
        return [
            (c.args[4], c.kwargs.get('host', False)) for c in self.transport.mouse.call_args_list
        ]

    def test_borrowed_drag_presses_on_motion_and_returns_cursor_on_release(self):
        backend = self.borrowing()
        lease = {'lease': backend.lease}
        backend.request('button', code=272, pressed=True, **lease)
        self.transport.mouse.assert_not_called()
        backend.request('move', x=40, y=50, **lease)
        backend.request('button', code=272, pressed=False, **lease)
        self.assertEqual(self.kinds(), [('down', True), ('drag', True), ('up', True)])
        self.assertEqual(
            [c.args[0] for c in self.transport.associate.call_args_list], [False, True]
        )
        self.assertEqual(self.transport.warp.call_args.args[0], (500, 500))
        self.transport.restore_front.assert_called_once_with(b'human')
        self.assertIsNone(backend.borrow)

    def test_click_with_host_drags_stays_on_agent_lane(self):
        backend = self.borrowing()
        lease = {'lease': backend.lease}
        backend.request('button', code=272, pressed=True, **lease)
        backend.request('button', code=272, pressed=False, **lease)
        self.assertEqual(self.kinds(), [('down', False), ('up', False)])
        self.transport.associate.assert_not_called()
        self.transport.warp.assert_not_called()

    def test_covered_drag_point_releases_and_rejoins_the_mouse(self):
        backend = self.borrowing()
        lease = {'lease': backend.lease}
        backend.request('button', code=272, pressed=True, **lease)
        with self.assertRaisesRegex(PermissionError, 'covers'):
            backend.request('move', x=70, y=50, **lease)
        backend.request('cancel')
        self.assertEqual(self.kinds(), [('down', True), ('up', True)])
        self.assertEqual(self.transport.associate.call_args.args[0], True)
        self.assertEqual(self.transport.warp.call_args.args[0], (500, 500))

    def test_host_drags_require_the_agent_lane(self):
        with self.assertRaisesRegex(ValueError, 'agent lane'):
            MacOSBackend('host', host_drags=True)


class MacOSNativeSafetyTest(unittest.TestCase):
    def test_agent_mouse_scroll_and_keys_never_use_global_host_posting(self):
        native = Native.__new__(Native)
        event = SimpleNamespace(__c_void_p__=lambda: ctypes.c_void_p(123))
        q = Mock()
        q.CGEventCreateMouseEvent.return_value = event
        q.CGEventCreateScrollWheelEvent.return_value = event
        q.CGEventCreateKeyboardEvent.return_value = event
        native.q, native.source, native.group = q, object(), 99
        native._post, native._field, native._location = Mock(), Mock(), Mock()
        native.mouse(42, 7, (10, 20), (30, 40), 'down', clicks=1, flags=0)
        native.scroll(42, 7, (10, 20), (30, 40), 15, 'vertical')
        native.key(42, 30, True, 0)
        q.CGEventPost.assert_not_called()
        self.assertEqual(native._post.call_count, 2)
        q.CGEventPostToPid.assert_called_once_with(42, event)
        self.assertEqual(native._location.call_args.args, (123, 20, 20))
        self.assertEqual(q.CGEventSetFlags.call_args.args, (event, 0))

    def test_console_status_uses_exported_quartz_dictionary_key_and_fails_closed(self):
        native = Native.__new__(Native)
        native.q = SimpleNamespace(
            kCGSessionOnConsoleKey='kCGSSessionOnConsoleKey',
            kCGSessionLoginDoneKey='kCGSessionLoginDoneKey',
            CGSessionCopyCurrentDictionary=Mock(),
        )
        native.q.CGSessionCopyCurrentDictionary.return_value = {
            'kCGSSessionOnConsoleKey': True,
            'kCGSessionLoginDoneKey': True,
        }
        self.assertTrue(native.session_ready())
        for session in (
            None,
            {},
            {'kCGSSessionOnConsoleKey': False},
            {
                'kCGSSessionOnConsoleKey': True,
                'kCGSessionLoginDoneKey': True,
                'CGSSessionScreenIsLocked': True,
            },
        ):
            with self.subTest(session=session):
                native.q.CGSessionCopyCurrentDictionary.return_value = session
                with self.assertRaises(PermissionError):
                    native.session_ready()


if __name__ == '__main__':
    unittest.main()
