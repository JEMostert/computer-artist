"""Capture evidence and guarded regions without program execution or input."""

import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from computer_artist.errors import Interrupted
from computer_artist.observations import Observations
from computer_artist.workspace import Workspace


class CaptureBackend:
    def __init__(self):
        self.window = {
            'id': 'w',
            'native': True,
            'visible': True,
            'pid': 1,
            'title': 'Canvas',
            'x': 100,
            'y': 200,
            'width': 100,
            'height': 80,
        }
        self.image = Image.new('RGB', (200, 160), 'white')

    def windows(self):
        return [dict(self.window)]

    def request(self, operation, **values):
        if operation != 'capture':
            raise AssertionError(operation)
        self.image.save(values['path'])
        return {'ok': True}


class ObservationsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = Workspace(Path(self.tmp.name) / 'window')
        self.backend = CaptureBackend()
        self.observations = Observations(self.memory, self.backend)

    def test_scaled_crop_diff_and_observation_retention(self):
        first = self.observations.capture('w')
        ImageDraw.Draw(self.backend.image).rectangle((20, 20, 39, 39), fill='black')
        second = self.observations.capture('w', since=first['id'])
        self.assertTrue(second['changes']['pixels_changed'])
        self.assertEqual(second['region'], [10, 10, 10, 10])
        with Image.open(second['image']) as im:
            self.assertEqual(im.size, (20, 20))
        self.memory.run_folder = self.memory.output / 'test-run'
        self.observations.limit = 2
        # Per-run pruning is tested below; earlier standalone observations are separate runs.
        first = self.observations.capture('w')
        self.observations.capture('w')
        self.observations.capture('w')
        with self.assertRaises(ValueError):
            self.observations.load('w', first['id'])

    def test_diff_uses_decoded_snapshot_after_concurrent_capture_prunes_source(self):
        from unittest.mock import patch

        from computer_artist.storage import managed_run

        with managed_run(self.memory.output) as folder:
            store = Workspace(self.memory.root, self.memory.output, folder)
            observer = Observations(store, self.backend)
            concurrent = Observations(store, self.backend)
            observer.limit = concurrent.limit = 1
            first = observer.capture('w')
            snapshot = observer._snapshot

            def snapshot_then_prune(window, identity):
                record, image = snapshot(window, identity)
                concurrent.capture(window)
                self.assertFalse(Path(first['full_image']).exists())
                return record, image

            with patch.object(observer, '_snapshot', snapshot_then_prune):
                result = observer.capture('w', since=first['id'])
            self.assertFalse(result['changes']['pixels_changed'])

    def test_missing_snapshot_image_reports_expired_evidence(self):
        first = self.observations.capture('w')
        Path(first['full_image']).unlink()
        with self.assertRaisesRegex(ValueError, 'expired; observe again'):
            self.observations.capture('w', since=first['id'])
        with self.assertRaisesRegex(ValueError, 'expired; observe again'):
            self.observations.target('w', 'button', first['id'], [10, 10, 20, 20])

    def test_capture_hiding_target_does_not_return_stale_success(self):
        original = self.backend.request

        def request(op, **values):
            result = original(op, **values)
            if op == 'capture':
                self.backend.window['visible'] = False
            return result

        self.backend.request = request
        with self.assertRaises(Interrupted):
            self.observations.capture('w')

    def test_revalidation_refuses_changed_pixels_without_mutating_guard(self):
        first = self.observations.capture('w')
        self.observations.target('w', 'button', first['id'], [10, 10, 20, 20])
        path = self.memory.layout('w') / 'targets' / 'button.json'
        original = path.read_bytes()
        ImageDraw.Draw(self.backend.image).rectangle((20, 20, 30, 30), fill='black')
        changed = self.observations.capture('w')
        with self.assertRaisesRegex(Interrupted, 'changed visually'):
            self.observations.revalidate('w', 'button', changed['id'])
        self.assertEqual(path.read_bytes(), original)

    def test_guard_creation_and_revalidation_recheck_live_geometry(self):
        first = self.observations.capture('w')
        self.observations.target('w', 'button', first['id'], [10, 10, 20, 20])
        self.backend.window['width'] = 101
        with self.assertRaisesRegex(Interrupted, 'geometry changed'):
            self.observations.target('w', 'other', first['id'], [10, 10, 20, 20])
        with self.assertRaisesRegex(Interrupted, 'geometry changed'):
            self.observations.revalidate('w', 'button', first['id'])


if __name__ == '__main__':
    unittest.main()
