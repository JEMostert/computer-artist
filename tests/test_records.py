import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_artist.files import atomic_json
from computer_artist.records import inspect_run, list_runs
from computer_artist.storage import MARKER


class RecordsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'runs'
        self.root.mkdir()

    def run_folder(self, name='run', *, managed=True, result=True):
        folder = self.root / name
        folder.mkdir()
        if managed:
            atomic_json(folder / MARKER, {'format': 1, 'created': 1, 'completed': True})
            (folder / '.active.lock').touch()
        atomic_json(folder / 'request.json', {'window': 'window-id', 'kind': 'draw'})
        if result:
            atomic_json(
                folder / 'result.json',
                {'ok': True, 'status': 'dispatched', 'trace': [{'op': 'click'}]},
            )
        return folder

    def test_list_survives_corrupt_records_and_keeps_unfinished_history(self):
        self.run_folder('good')
        bad = self.run_folder('bad')
        (bad / 'result.json').write_text('{broken')
        orphan = self.run_folder('orphan', result=False)
        atomic_json(orphan / MARKER, {'format': 1, 'created': 2, 'completed': False})
        self.run_folder('legacy', managed=False)
        entries = {entry['id']: entry for entry in list_runs(self.root)['runs']}
        self.assertEqual(set(entries), {'good', 'bad', 'orphan', 'legacy'})
        self.assertEqual(entries['good']['status'], 'dispatched')
        self.assertTrue(entries['bad']['issues'])
        self.assertEqual(entries['orphan']['lifecycle'], 'abandoned')
        self.assertFalse(entries['legacy']['managed'])
        self.assertFalse((self.root / 'legacy' / MARKER).exists())
        self.assertFalse((self.root / 'legacy' / '.active.lock').exists())

    def test_refuses_paths_and_symlink_records_without_reading_external_data(self):
        folder = self.run_folder()
        secret = Path(self.temp.name) / 'secret'
        secret.write_text('{"status":"secret"}')
        (folder / 'result.json').unlink()
        (folder / 'result.json').symlink_to(secret)
        (folder / 'frame.png').symlink_to(secret)
        review = inspect_run(self.root, 'run')
        self.assertIsNone(review['result'])
        self.assertEqual(len(review['issues']), 2)
        self.assertEqual(review['evidence'], [])
        (self.root / 'linked').symlink_to(folder, target_is_directory=True)
        with self.assertRaises(ValueError):
            inspect_run(self.root, 'linked')
        with self.assertRaises(ValueError):
            inspect_run(self.root, '../secret')
        self.assertNotIn('linked', [entry['id'] for entry in list_runs(self.root)['runs']])

    def test_oversized_json_and_fifo_are_bounded_review_issues(self):
        folder = self.run_folder()
        (folder / 'result.json').write_text(' ' * 1000)
        with patch('computer_artist.records.JSON_LIMIT', 64):
            review = inspect_run(self.root, 'run')
        self.assertIsNone(review['result'])
        self.assertTrue(any('exceeds' in issue['error'] for issue in review['issues']))
        (folder / 'program.log').unlink(missing_ok=True)
        os.mkfifo(folder / 'program.log')
        self.assertTrue(
            any('regular' in issue['error'] for issue in inspect_run(self.root, 'run')['issues'])
        )

    def test_checkpoint_keeps_partial_evidence_without_promoting_outcome(self):
        folder = self.run_folder(result=False)
        atomic_json(
            folder / 'checkpoint.json',
            {
                'status': 'verified',
                'checks': [{'name': 'save', 'passed': True}],
                'trace': [
                    {'operation': 'button', 'ok': True},
                    {'operation': 'release', 'ok': True},
                ],
            },
        )
        review = inspect_run(self.root, 'run')
        self.assertIsNone(review['result'])
        self.assertTrue(review['incomplete'])
        self.assertEqual(review['status'], 'incomplete')
        self.assertEqual(review['trace_summary']['recorded_events'], 2)
        self.assertEqual(review['trace_summary']['successful_input_replies'], 1)

    def test_malformed_valid_json_cannot_break_history(self):
        self.run_folder('healthy')
        values = (
            '{"status": 1e999}',
            '{"status": ["verified"]}',
            '{"status": "verified", "ok": 1}',
            '{"status": "verified", "checks": true}',
            '{"status": "verified", "checks": [{"passed": "yes"}]}',
            '{"modules": [false]}',
            '{"trace": [1]}',
            '{"interruption": {"code":"stopped", "reason":"stop", "details":[]}}',
            '{"interruption": {"code":"stopped", "reason":"stop", "details":{"candidates":true}}}',
            '{"duration": ' + str(10**400) + '}',
        )
        for index, encoded in enumerate(values):
            with self.subTest(encoded=encoded):
                folder = self.run_folder('invalid-' + str(index))
                (folder / 'result.json').write_text(encoded)
                review = inspect_run(self.root, folder.name)
                self.assertIsNone(review['result'])
                self.assertTrue(review['incomplete'])
                self.assertTrue(review['issues'])
        entries = {entry['id']: entry for entry in list_runs(self.root)['runs']}
        self.assertEqual(entries['healthy']['status'], 'dispatched')
        self.assertEqual(len(entries), len(values) + 1)
