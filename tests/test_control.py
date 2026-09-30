"""Managed, targeted cancellation refuses stale work and unsafe request files."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from computer_artist.control import check_stop, read_stop, request_stop
from computer_artist.errors import Interrupted
from computer_artist.files import atomic_json
from computer_artist.storage import managed_run


class ControlTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / 'output'

    def test_active_stop_is_idempotent_and_contains_no_pid(self):
        with managed_run(self.root) as folder:
            atomic_json(folder / 'request.json', {'source': 'def run(ctx): pass'})
            first = request_stop(self.root, folder.name, reason='Inspect the export')
            second = request_stop(self.root, folder.name)
            self.assertEqual(first, second)
            self.assertNotIn('pid', first['request'])
            with self.assertRaises(Interrupted) as caught:
                check_stop(folder)
            self.assertEqual(caught.exception.code, 'user_cancelled')
            self.assertEqual(str(caught.exception), 'Inspect the export')
        with self.assertRaisesRegex(ValueError, 'no longer active'):
            request_stop(self.root, folder.name)

    def test_unsupported_active_work_and_unmanaged_folders_are_refused(self):
        with managed_run(self.root) as folder:
            atomic_json(folder / 'request.json', {'kind': 'integration'})
            with self.assertRaisesRegex(ValueError, 'does not support'):
                request_stop(self.root, folder.name)
            self.assertFalse((folder / '.cancel.json').exists())
        (self.root / 'ordinary').mkdir()
        with self.assertRaisesRegex(ValueError, 'Not a managed'):
            request_stop(self.root, 'ordinary')
        with self.assertRaises(ValueError):
            request_stop(self.root, '../outside')

    def test_stop_reader_is_bounded_and_refuses_nonregular_files(self):
        self.root.mkdir()
        self.assertIsNone(read_stop(self.root))
        stop = self.root / '.cancel.json'
        stop.write_text('x' * 4097)
        with self.assertRaises(ValueError):
            read_stop(self.root)
        stop.unlink()
        stop.symlink_to(self.root / 'missing')
        with self.assertRaises(ValueError):
            read_stop(self.root)
        stop.unlink()
        os.mkfifo(stop)
        with self.assertRaises(ValueError):
            read_stop(self.root)

    def test_invalid_json_fields_never_become_cancellation(self):
        self.root.mkdir()
        for value in (
            {},
            {'format': True, 'requested_at': 1, 'reason': 'stop'},
            {'format': 1, 'requested_at': float('nan'), 'reason': 'stop'},
            {'format': 1, 'requested_at': 1, 'reason': ''},
            {'format': 1, 'requested_at': 1, 'reason': 'x' * 513},
        ):
            (self.root / '.cancel.json').write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                check_stop(self.root)
