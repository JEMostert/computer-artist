"""Checkpoint evidence stays bounded and never claims final completion."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from computer_artist.files import atomic_json
from computer_artist.journal import Journal, partial_result


class JournalTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.context = SimpleNamespace(
            events=[], checks=[], client=SimpleNamespace(trace=[]), last_observation=None
        )
        self.context.execution = SimpleNamespace(contexts={'root': self.context})
        self.journal = Journal(self.folder, self.context)

    def test_checkpoint_bounds_evidence_and_orders_trace_across_contexts(self):
        self.context.events = [{'module': str(index)} for index in range(250)]
        self.context.checks = [{'name': str(index), 'passed': True} for index in range(250)]
        self.context.client.trace = [{'time': index * 2} for index in range(300)]
        other = SimpleNamespace(
            events=[], checks=[], client=SimpleNamespace(trace=[{'time': 1}, {'time': 1000}])
        )
        self.context.execution.contexts['other'] = other
        self.context.last_observation = {'id': 'frame', 'image': '/evidence/frame.png'}
        self.journal.checkpoint()
        record = json.loads(self.journal.path.read_text())
        self.assertEqual(record['status'], 'in_progress')
        self.assertFalse(record['final_outcome_known'])
        self.assertEqual(len(record['modules']), 200)
        self.assertEqual(record['modules'][0]['module'], '50')
        self.assertEqual(len(record['checks']), 200)
        self.assertLessEqual(len(record['trace']), 512)
        self.assertEqual(record['trace'], sorted(record['trace'], key=lambda item: item['time']))
        self.assertEqual(record['observation']['id'], 'frame')
        partial = partial_result(self.folder)
        self.assertTrue(partial['partial_evidence'])
        self.assertNotIn('status', partial)
        self.assertNotIn('ok', partial)

    def test_periodic_checkpoint_observes_updates_and_close_stops_thread(self):
        self.journal.start()
        try:
            self.context.checks.append({'name': 'saved', 'passed': True})
            end = time.monotonic() + 0.7
            while time.monotonic() < end:
                if partial_result(self.folder).get('checks'):
                    break
                time.sleep(0.005)
            self.assertEqual(partial_result(self.folder)['checks'][0]['name'], 'saved')
        finally:
            self.journal.close()
        self.assertTrue(self.journal.stopped.is_set())
        self.assertFalse(self.journal.thread.is_alive())

    def test_oversized_checkpoint_preserves_previous_evidence(self):
        self.context.checks = [{'name': 'prior', 'passed': True}]
        self.journal.checkpoint()
        previous = self.journal.path.read_bytes()
        self.context.checks = [{'name': 'large', 'evidence': 'x' * 5000}]
        with patch('computer_artist.journal.MAX_BYTES', 1000):
            with self.assertRaises(ValueError):
                self.journal.checkpoint()
        self.assertEqual(self.journal.path.read_bytes(), previous)

    def test_unchanged_evidence_is_not_rewritten_and_mutated_event_is_saved(self):
        event = {'module': 'save', 'status': 'in_progress'}
        self.context.events.append(event)
        with patch('computer_artist.journal.atomic_json', wraps=atomic_json) as write:
            self.journal.checkpoint()
            self.journal.checkpoint()
            self.assertEqual(write.call_count, 1)
            event['status'] = 'verified'
            self.journal.checkpoint()
            self.assertEqual(write.call_count, 2)
        self.assertEqual(partial_result(self.folder)['modules'][0]['status'], 'verified')

    def test_invalid_or_final_checkpoint_never_becomes_partial_success(self):
        for record in (
            {'final_outcome_known': True, 'checks': []},
            {'final_outcome_known': False, 'modules': 'invalid'},
            {'final_outcome_known': False, 'modules': [None]},
            {'final_outcome_known': False, 'checks': [True]},
            {'final_outcome_known': False, 'trace': 'invalid'},
            {'final_outcome_known': False, 'observation': []},
        ):
            with self.subTest(record=record):
                self.journal.path.write_text(json.dumps(record))
                partial = partial_result(self.folder)
                self.assertIn('checkpoint_error', partial)
                self.assertNotIn('ok', partial)

    def test_malformed_symlink_or_missing_checkpoint_is_not_followed(self):
        self.assertEqual(partial_result(self.folder), {})
        self.journal.path.write_text('{invalid')
        self.assertIn('checkpoint_error', partial_result(self.folder))
        self.journal.path.unlink()
        external = self.folder / 'external'
        external.write_text('{"final_outcome_known": false}')
        self.journal.path.symlink_to(external)
        self.assertEqual(partial_result(self.folder), {})

    def test_start_and_close_tolerate_initial_checkpoint_failure(self):
        with patch.object(self.journal, 'checkpoint', side_effect=OSError('disk unavailable')):
            self.journal.start()
            self.journal.close()
        self.assertFalse(self.journal.thread.is_alive())
        self.assertTrue(self.journal.errors)


if __name__ == '__main__':
    unittest.main()
