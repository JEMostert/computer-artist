"""Export verification checks real files without dispatching desktop input."""

import hashlib
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from computer_artist.artifacts import inspect_file, snapshot_file, wait_for_file
from computer_artist.errors import Interrupted


class ArtifactsTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / 'export'

    def wait(self, **options):
        return wait_for_file(self.path, timeout=0.06, stable_for=0.01, interval=0.002, **options)

    def test_missing_baseline_then_asynchronous_atomic_export(self):
        baseline = snapshot_file(self.path)
        self.assertFalse(baseline['exists'])

        def export():
            time.sleep(0.006)
            self.path.write_text('{"partial":', encoding='utf-8')
            time.sleep(0.008)
            staging = self.root / 'staging'
            staging.write_text('{"finished": true}', encoding='utf-8')
            staging.replace(self.path)

        writer = threading.Thread(target=export)
        writer.start()
        try:
            result = self.wait(kind='json', after=baseline, expected_json={'finished': True})
        finally:
            writer.join()
        self.assertTrue(result['passed'], result)
        self.assertTrue(result['evidence']['fresh'])
        self.assertEqual(result['evidence']['json_type'], 'dict')

    def test_unchanged_existing_file_is_not_a_fresh_export(self):
        self.path.write_text('already there', encoding='utf-8')
        baseline = snapshot_file(self.path)
        result = self.wait(kind='text', after=baseline, expected_text='already there')
        self.assertFalse(result['passed'])
        self.assertFalse(result['evidence']['fresh'])
        self.assertIn('before-action', result['evidence']['reason'])

    def test_replaced_file_with_same_bytes_is_a_new_generation(self):
        self.path.write_bytes(b'same content')
        baseline = snapshot_file(self.path)
        staging = self.root / 'staging'
        staging.write_bytes(b'same content')
        staging.replace(self.path)
        result = self.wait(after=baseline)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['evidence']['sha256'], baseline['sha256'])
        self.assertNotEqual(result['evidence']['identity'], baseline['identity'])

    def test_exact_utf8_preserves_unicode_and_newlines(self):
        expected = 'Héllo 🎨\r\nSecond line\n'
        self.path.write_bytes(expected.encode('utf-8'))
        result = self.wait(kind='text', expected_text=expected)
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['evidence']['characters'], len(expected))
        result = self.wait(kind='text', expected_text=expected.replace('\r\n', '\n'))
        self.assertFalse(result['passed'])
        self.assertIn('exact expected UTF-8', result['evidence']['reason'])

    def test_invalid_utf8_and_malformed_json_never_pass(self):
        for kind, content in (
            ('text', b'\xff'),
            ('json', b'{"unfinished":'),
            ('json', b'NaN'),
            ('json', b'{"number": Infinity}'),
            ('json', b'{"number": 1e999}'),
            ('json', b'{"duplicate": 1, "duplicate": 2}'),
        ):
            with self.subTest(kind=kind, content=content):
                self.path.write_bytes(content)
                self.assertFalse(self.wait(kind=kind)['passed'])
                with self.assertRaises((ValueError, UnicodeError)):
                    inspect_file(self.path, kind=kind)

    def test_image_decode_dimensions_and_hash_are_independent_checks(self):
        Image.new('RGB', (13, 7), 'blue').save(self.path, format='PNG')
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        result = self.wait(kind='image', image_size=(13, 7), sha256=digest.upper())
        self.assertTrue(result['passed'], result)
        self.assertEqual(result['evidence']['image_format'], 'PNG')
        self.assertFalse(self.wait(kind='image', image_size=(7, 13))['passed'])
        self.assertFalse(self.wait(kind='image', sha256='0' * 64)['passed'])
        self.path.write_bytes(b'not an image')
        self.assertFalse(self.wait(kind='image')['passed'])
        with self.assertRaises(OSError):
            inspect_file(self.path, kind='image')

    def test_json_expectation_preserves_boolean_type_and_ignores_key_order(self):
        self.path.write_text('{"saved": true, "count": 1}', encoding='utf-8')
        self.assertTrue(self.wait(kind='json', expected_json={'count': 1, 'saved': True})['passed'])
        self.assertFalse(self.wait(kind='json', expected_json={'count': 1, 'saved': 1})['passed'])

    def test_link_fifo_and_oversized_files_are_refused_without_blocking(self):
        destination = self.root / 'real-file'
        destination.write_bytes(b'content')
        self.path.symlink_to(destination)
        with self.assertRaises((OSError, ValueError)):
            snapshot_file(self.path)
        self.assertFalse(self.wait()['passed'])
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(ValueError):
            inspect_file(self.path)
        self.assertFalse(self.wait()['passed'])
        self.path.unlink()
        self.path.write_bytes(b'12345')
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            snapshot_file(self.path, max_bytes=4)
        self.assertFalse(self.wait(max_bytes=4)['passed'])

    def test_cancellation_callbacks_propagate_typed_failures(self):
        self.path.write_text('saved', encoding='utf-8')
        for error in (Interrupted('human takeover'), TimeoutError('execution deadline')):
            with self.subTest(error=type(error).__name__):
                calls = []

                def cancel_during_read():
                    calls.append(True)
                    if len(calls) >= 2:
                        raise error

                with self.assertRaises(type(error)):
                    self.wait(check=cancel_during_read)

    def test_snapshot_refuses_file_that_changes_during_read(self):
        self.path.write_bytes(b'first generation')
        calls = []

        def change_after_first_chunk():
            calls.append(True)
            if len(calls) == 2:
                self.path.write_bytes(b'later generation')

        with self.assertRaisesRegex(ValueError, 'changed while being read'):
            snapshot_file(self.path, check=change_after_first_chunk)

    def test_decoded_image_and_animation_stay_within_pixel_budget(self):
        Image.new('RGB', (10, 10)).save(self.path, format='PNG')
        with patch('computer_artist.artifacts.MAX_PIXELS', 50):
            with self.assertRaisesRegex(ValueError, 'pixels'):
                inspect_file(self.path, kind='image')
        Image.new('RGB', (6, 6), 'red').save(
            self.path,
            format='GIF',
            save_all=True,
            append_images=[Image.new('RGB', (6, 6), 'blue')],
        )
        with patch('computer_artist.artifacts.MAX_PIXELS', 50):
            with self.assertRaisesRegex(ValueError, 'pixel budget'):
                inspect_file(self.path, kind='image')


if __name__ == '__main__':
    unittest.main()
