import fcntl
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_artist import storage
from computer_artist.files import atomic_json
from computer_artist.storage import MARKER, active_run, cleanup, inspect, managed_run, preserve

# Linux lists open descriptors in /proc; macOS in /dev/fd.
OPEN_FDS = '/proc/self/fd' if os.path.isdir('/proc/self/fd') else '/dev/fd'


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'runs'

    def run_folder(self, name, created, size=20):
        folder = self.root / name
        folder.mkdir(parents=True)
        atomic_json(folder / MARKER, {'format': 1, 'created': created, 'completed': True})
        (folder / '.active.lock').touch()
        (folder / 'data').write_bytes(b'x' * size)
        return folder

    def test_keeps_newest_whole_runs_ignores_unmanaged_and_symlinks(self):
        for i in range(18):
            self.run_folder(str(i), i)
        unmanaged = self.root / 'manual'
        unmanaged.mkdir()
        (unmanaged / 'keep').write_text('important')
        external = Path(self.temp.name) / 'external'
        external.mkdir()
        (self.root / 'linked').symlink_to(external, target_is_directory=True)
        report = cleanup(self.root)
        self.assertEqual(report['removed'], [str(i) for i in range(13)])
        self.assertTrue(unmanaged.exists())
        self.assertTrue(external.exists())
        self.assertEqual(len(inspect(self.root)['runs']), 5)

    def test_active_and_preserved_runs_survive_size_pressure(self):
        self.run_folder('old', 1, 10000)
        preserve(self.root, 'old')
        self.run_folder('discard', 2, 10000)
        self.run_folder('latest', 3, 10000)
        with managed_run(self.root, 'working') as working:
            (working / 'data').write_text('still running')
            report = cleanup(self.root, count=1, max_bytes=1)
            self.assertTrue(working.exists())
            self.assertTrue((self.root / 'old').exists())
            self.assertEqual(report['removed'], ['discard'])
            self.assertTrue(report['over_budget'])
            self.assertTrue((self.root / 'latest').exists())
        self.assertFalse(
            next(e for e in inspect(self.root)['runs'] if e['id'] == 'working')['active']
        )

    def test_dry_run_and_byte_limit(self):
        self.run_folder('old', 1, 1000)
        self.run_folder('new', 2, 1000)
        report = cleanup(self.root, max_bytes=1500, dry_run=True)
        self.assertEqual(report['would_remove'], ['old'])
        self.assertTrue((self.root / 'old').exists())
        self.assertEqual(cleanup(self.root, max_bytes=1500)['removed'], ['old'])

    def test_child_lock_survives_supervisor_exit_and_abandoned_run_is_pruned(self):
        folder = self.run_folder('child', 1)
        code = (
            'from computer_artist.storage import active_run; import sys; '
            "c=active_run(sys.argv[1]); c.__enter__(); print('ready',flush=True); sys.stdin.read(); c.__exit__(None,None,None)"
        )
        child = subprocess.Popen(
            [sys.executable, '-c', code, str(folder)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            self.assertEqual(child.stdout.readline().strip(), 'ready')
            self.run_folder('new', 2)
            self.assertEqual(cleanup(self.root, count=1)['removed'], [])
        finally:
            child.communicate(timeout=5)
        atomic_json(folder / MARKER, {'format': 1, 'created': 1, 'completed': False})
        self.assertEqual(cleanup(self.root, count=1)['removed'], ['child'])

    def test_finishing_older_active_run_retains_its_returned_record(self):
        with patch.dict(os.environ, CA_RUN_LIMIT='1'):
            with managed_run(self.root, 'slow') as folder:
                with managed_run(self.root, 'fast'):
                    pass
            self.assertTrue(folder.exists())
            cleanup(self.root)
            self.assertFalse(folder.exists())

    def test_malformed_markers_and_lock_symlinks_are_never_adopted(self):
        for index, marker in enumerate(
            (
                [],
                None,
                {'format': 1, 'created': True},
                {'format': 1, 'created': float('nan')},
                {'format': 1, 'created': 0, 'preserved': 'false'},
            )
        ):
            folder = self.root / ('invalid-' + str(index))
            folder.mkdir(parents=True)
            (folder / MARKER).write_text(json.dumps(marker))
        target = Path(self.temp.name) / 'external-lock'
        target.write_text('user data')
        linked = self.run_folder('linked-lock', 1)
        (linked / '.active.lock').unlink()
        (linked / '.active.lock').symlink_to(target)
        self.run_folder('old', 2)
        self.run_folder('new', 3)
        self.assertEqual(cleanup(self.root, count=1)['removed'], ['old'])
        self.assertEqual([e['id'] for e in inspect(self.root)['runs']], ['new'])
        self.assertEqual(target.read_text(), 'user data')
        self.assertTrue(all((self.root / ('invalid-' + str(i))).exists() for i in range(5)))

    def test_worker_refuses_unmanaged_folders_and_preserve_validates_marker(self):
        folder = self.root / 'manual'
        folder.mkdir(parents=True)
        (folder / '.active.lock').touch()
        with self.assertRaises(ValueError):
            with active_run(folder):
                self.fail('Unmanaged worker acquired a lease')
        atomic_json(folder / MARKER, {'format': 1})
        with self.assertRaises(ValueError):
            preserve(self.root, 'manual')
        good = self.run_folder('good', 1)
        with self.assertRaises(ValueError):
            preserve(self.root, 'good', 'false')
        self.assertNotIn('preserved', json.loads((good / MARKER).read_text()))

    def test_cleanup_rechecks_lease_after_retention_snapshot(self):
        old = self.run_folder('old', 1)
        self.run_folder('new', 2)
        fd = os.open(old / '.active.lock', os.O_RDWR)
        entries = storage._entries

        def snapshot_then_worker_lease(root):
            snapshot = list(entries(root))
            fcntl.flock(fd, fcntl.LOCK_SH)
            yield from snapshot

        try:
            with patch.object(storage, '_entries', snapshot_then_worker_lease):
                self.assertEqual(cleanup(self.root, count=1)['removed'], [])
            self.assertTrue(old.exists())
        finally:
            os.close(fd)
        self.assertEqual(cleanup(self.root, count=1)['removed'], ['old'])

    def test_failed_run_initialization_releases_lease(self):
        open_before = len(os.listdir(OPEN_FDS))
        with patch.object(storage, 'atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                with managed_run(self.root, 'failed'):
                    self.fail('Run started without a marker')
        self.assertEqual(len(os.listdir(OPEN_FDS)), open_before)
        self.assertFalse((self.root / 'failed').exists())
        self.assertEqual(inspect(self.root)['runs'], [])

    def test_cleanup_failure_before_work_still_completes_run_and_releases_lease(self):
        open_before = len(os.listdir(OPEN_FDS))
        with patch.object(storage, 'cleanup', side_effect=OSError('disk busy')):
            with self.assertRaises(OSError):
                with managed_run(self.root, 'rotating'):
                    self.fail('Run body executed after cleanup failed')
        self.assertEqual(len(os.listdir(OPEN_FDS)), open_before)
        info = json.loads((self.root / 'rotating' / MARKER).read_text())
        self.assertTrue(info['completed'])
        self.assertEqual(inspect(self.root)['runs'][0]['status'], 'completed')
