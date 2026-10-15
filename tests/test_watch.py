import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

from PIL import ImageDraw

from computer_artist.control import request_stop
from computer_artist.errors import Interrupted
from computer_artist.watch import watch
from tests.support import WindowBackend


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.backend = WindowBackend()
        self.args = Namespace(
            window_dir=self.root / 'window',
            output_dir=self.root / 'output',
            window='w',
            duration=0.2,
            interval=0.05,
            deadline=20,
            region=None,
            changes_only=True,
        )
        self.events = []

    def test_changes_only_stream_and_record_never_acquire(self):
        def emit(event):
            self.events.append(event)
            if event.get('sequence') == 1:
                ImageDraw.Draw(self.backend.image).point((10, 10), fill='black')

        result = watch(self.backend, self.args, emit)
        self.assertTrue(result['ok'])
        self.assertGreater(result['frames'], 2)
        self.assertEqual(result['emitted'], 2)
        self.assertEqual(self.backend.acquire_count, 0)
        self.assertEqual(self.backend.actions, [])
        self.assertEqual(self.events[-1]['event'], 'watch_finished')
        record = json.loads(Path(result['record']).read_text())
        self.assertEqual(record['frames'], result['frames'])
        self.assertEqual(len(Path(result['events']).read_text().splitlines()), result['frames'])

    def test_cancellation_records_evidence_and_returns_no_input_lease(self):
        def emit(event):
            self.events.append(event)
            if event.get('sequence') == 1:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            watch(self.backend, self.args, emit)
        finished = self.events[-1]
        self.assertEqual(finished['status'], 'interrupted')
        self.assertEqual(finished['frames'], 1)
        self.assertTrue(Path(finished['record']).exists())
        self.assertEqual(self.backend.acquire_count, 0)

    def test_targeted_watch_stop_records_acknowledgement_without_input(self):
        self.args.duration = 5

        def emit(event):
            self.events.append(event)
            if event.get('sequence') == 1:
                request_stop(self.args.output_dir, event['run_id'])

        with self.assertRaises(Interrupted):
            watch(self.backend, self.args, emit)
        finished = self.events[-1]
        self.assertEqual(finished['status'], 'interrupted')
        self.assertEqual(finished['interruption']['code'], 'user_cancelled')
        self.assertEqual(finished['frames'], 1)
        self.assertLess(finished['duration'], 1)
        self.assertEqual(self.backend.acquire_count, 0)
