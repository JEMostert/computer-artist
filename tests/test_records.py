import fcntl
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

    def test_active_lease_reported_and_not_changed(self):
        folder = self.run_folder(result=False)
        fd = os.open(folder / '.active.lock', os.O_RDWR)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            self.assertEqual(inspect_run(self.root, 'run')['lifecycle'], 'active')
        finally:
            os.close(fd)

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

    def test_log_tail_and_timeline_are_bounded(self):
        folder = self.run_folder()
        (folder / 'program.log').write_bytes(b'x' * 500 + b'tail')
        atomic_json(folder / 'trace.json', [{'op': 'move', 'time': i} for i in range(300)])
        review = inspect_run(self.root, 'run', log_bytes=20)
        self.assertEqual(len(review['log']), 20)
        self.assertTrue(review['log'].endswith('tail'))
        self.assertTrue(review['log_truncated'])
        self.assertEqual(review['trace_summary']['recorded_events'], 300)
        self.assertEqual(len(review['trace_summary']['events']), 200)

    def test_missing_root_listing_creates_nothing_and_window_filter_works(self):
        missing = self.root / 'missing'
        self.assertEqual(list_runs(missing), {'runs': [], 'issues': []})
        self.assertFalse(missing.exists())
        self.run_folder()
        self.assertEqual(list_runs(self.root, 'other')['runs'], [])
        self.assertEqual(len(list_runs(self.root, 'window-id')['runs']), 1)

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

    def test_inspection_holds_retention_lock_while_reading(self):
        self.run_folder()
        from computer_artist import records

        read = records._read
        checked = []

        def under_lock(*args, **kwargs):
            fd = os.open(self.root / '.retention.lock', os.O_RDWR)
            try:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                checked.append(True)
            finally:
                os.close(fd)
            return read(*args, **kwargs)

        with patch.object(records, '_read', under_lock):
            inspect_run(self.root, 'run')
        self.assertTrue(checked)

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

    def test_nonfinite_records_are_integrity_issues(self):
        folder = self.run_folder()
        (folder / 'result.json').write_text('{"status": NaN}')
        self.assertIsNone(inspect_run(self.root, 'run')['result'])

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

    def test_invalid_result_falls_back_to_valid_partial_checkpoint(self):
        folder = self.run_folder()
        (folder / 'result.json').write_text('{"checks": true}')
        checkpoint = {'checks': [{'passed': True}], 'trace': [{'operation': 'button', 'ok': True}]}
        atomic_json(folder / 'checkpoint.json', checkpoint)
        review = inspect_run(self.root, 'run')
        self.assertIsNone(review['result'])
        self.assertEqual(review['checkpoint'], checkpoint)
        self.assertTrue(review['incomplete'])
        self.assertEqual(review['trace_summary']['successful_input_replies'], 1)
        self.assertEqual(review['status'], 'incomplete')
        (folder / 'checkpoint.json').write_text('{"checks": [{"passed": 1}]}')
        review = inspect_run(self.root, 'run')
        self.assertIsNone(review['checkpoint'])
        self.assertTrue(any(issue['file'] == 'checkpoint.json' for issue in review['issues']))

    def test_huge_marker_integer_is_an_issue_and_partial_old_records_remain_useful(self):
        folder = self.run_folder()
        atomic_json(folder / MARKER, {'format': 1, 'created': 10**400})
        atomic_json(folder / 'result.json', {'status': 'returned_unverified'})
        review = inspect_run(self.root, 'run')
        self.assertEqual(review['lifecycle'], 'invalid')
        self.assertEqual(review['result'], {'status': 'returned_unverified'})
        self.assertTrue(review['issues'])

    def test_non_object_result_reports_integrity_issue(self):
        folder = self.run_folder()
        atomic_json(folder / 'result.json', [])
        review = inspect_run(self.root, 'run')
        self.assertIsNone(review['result'])
        self.assertTrue(review['issues'])
        with self.assertRaises(ValueError):
            inspect_run(self.root, 'run', log_bytes=0)
