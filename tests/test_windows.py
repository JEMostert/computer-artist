"""Windows portability, native leases and isolation checks without desktop input."""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from computer_artist import locking
from computer_artist.artifacts import inspect_file, snapshot_file, wait_for_file
from computer_artist.config import socket_path
from computer_artist.platform_io import open_no_follow
from computer_artist.setup import install
from computer_artist.storage import cleanup, inspect, managed_run
from computer_artist.windows import Input, WindowsBackend, key_input
from computer_artist.windows_agent import MessageLane
from computer_artist.windows_monitors import contains


class GeometryAndKeysTest(unittest.TestCase):
    def test_session_status_reports_the_actual_connection_lane(self):
        for lane in ('agent', 'host'):
            backend = WindowsBackend.__new__(WindowsBackend)
            backend.lane, backend.lease, backend.reason = lane, 'owned', 'test_reason'
            status = backend.request('session_status')
            self.assertTrue(status['lanes'][lane]['busy'])
            self.assertFalse(status['lanes']['host' if lane == 'agent' else 'agent']['busy'])
            self.assertEqual(status['lanes'][lane]['stop_reason'], 'test_reason')
            backend.lease = ''
            self.assertFalse(backend.request('session_status')['lanes'][lane]['busy'])

    def test_independent_lane_accepts_only_push_buttons_and_never_sends_richedit_pointers(self):
        styles = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 7, 7: 0x800}  # 0/1 are push buttons.
        api = SimpleNamespace(
            user=SimpleNamespace(
                GetWindowLongW=lambda hwnd, _index: styles[hwnd],
                IsWindowEnabled=lambda _hwnd: True,
            )
        )
        lane = MessageLane.__new__(MessageLane)
        lane.api, lane.position, sent = api, (10, 10), []
        lane.class_name = lambda hwnd: 'button' if hwnd < 7 else 'richeditd2dpt'
        lane.message = lambda *values: sent.append(values) or 0
        self.assertEqual([hwnd for hwnd in range(1, 8) if lane.supported(hwnd)], [1, 2, 7])
        lane.control = 7
        lane.button(272, True)  # Selection only: keeps the lease, moves no caret.
        self.assertEqual(sent, [])

    def test_windows_replace_retries_a_briefly_held_target(self):
        from computer_artist import files

        real, calls = files.os.replace, []

        def busy_once(source, destination):
            calls.append(source)
            if len(calls) == 1:
                raise PermissionError(5, 'Access is denied')
            real(source, destination)

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'windows-input.json'
            with patch.object(files, 'WINDOWS', True), patch.object(files.os, 'replace', busy_once):
                files.atomic_json(target, {'keys': []})
            self.assertEqual(json.loads(target.read_text()), {'keys': []})
            self.assertEqual(len(calls), 2)

    def test_monitor_bounds_include_negative_origins_and_reject_spanning(self):
        display = {'bounds': [-1920, -200, 0, 880]}
        self.assertTrue(contains(display, (-1800, -100, 800, 600)))
        self.assertFalse(contains(display, (-10, 0, 800, 600)))
        self.assertFalse(contains(display, (-2000, 0, 800, 600)))

    def test_scan_codes_preserve_extended_keys_and_keyup(self):
        letter = key_input(30, True)
        arrow = key_input(105, False)
        self.assertEqual((letter.type, letter.ki.wScan, letter.ki.dwFlags), (1, 30, 8))
        self.assertEqual((arrow.ki.wScan, arrow.ki.dwFlags), (0x4B, 11))
        for invalid in (0, True, 248, 120):
            with self.assertRaises(ValueError):
                key_input(invalid, True)


@unittest.skipUnless(os.name == 'nt', 'Native Windows ABI and filesystem behavior')
class WindowsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_native_input_abi_is_correct_for_pointer_width(self):
        self.assertEqual(ctypes.sizeof(Input), 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)

    def test_default_backend_and_explicit_socket_are_distinct(self):
        with patch.dict(os.environ, {'CA_SOCKET': ''}):
            self.assertEqual(socket_path(), 'win32')
            self.assertEqual(socket_path('custom'), 'custom')

    def test_shared_readonly_leases_block_exclusive_and_release_on_close(self):
        path = self.root / 'lease'
        path.touch()
        first = open_no_follow(path, os.O_RDONLY)
        second = open_no_follow(path, os.O_RDONLY)
        probe = open_no_follow(path, os.O_RDWR)
        try:
            locking.flock(first, locking.LOCK_SH)
            locking.flock(second, locking.LOCK_SH)
            with self.assertRaises(BlockingIOError):
                locking.flock(probe, locking.LOCK_EX | locking.LOCK_NB)
            os.close(first)
            first = None
            with self.assertRaises(BlockingIOError):
                locking.flock(probe, locking.LOCK_EX | locking.LOCK_NB)
            os.close(second)
            second = None
            locking.flock(probe, locking.LOCK_EX | locking.LOCK_NB)
        finally:
            for fd in (first, second, probe):
                if fd is not None:
                    os.close(fd)

    def test_file_read_after_rewrite_uses_consistent_creation_time(self):
        path = self.root / 'export.txt'
        path.write_text('old', encoding='utf-8')
        before = snapshot_file(path)
        path.write_text('caf\u00e9 \u20ac \u2713', encoding='utf-8')
        self.assertTrue(inspect_file(path, kind='text')['exists'])
        result = wait_for_file(
            path,
            kind='text',
            expected_text='caf\u00e9 \u20ac \u2713',
            after=before,
            timeout=0.5,
            stable_for=0.02,
            interval=0.01,
        )
        self.assertTrue(result['passed'], result)
        self.assertTrue(result['evidence']['fresh'])

    def test_native_open_refuses_directories_and_reparse_points(self):
        with self.assertRaises((OSError, ValueError)):
            open_no_follow(self.root, os.O_RDONLY)
        target = self.root / 'target'
        target.write_text('preserve')
        alias = self.root / 'alias'
        try:
            alias.symlink_to(target)
        except OSError:
            self.skipTest('This user lacks symlink privileges')
        with self.assertRaises((OSError, ValueError)):
            open_no_follow(alias, os.O_RDONLY)

    def test_retention_keeps_active_work_and_bounds_completed_runs(self):
        root = self.root / 'output'
        with managed_run(root) as active:
            for _ in range(7):
                with managed_run(root):
                    pass
            self.assertTrue(active.exists())
            entries = inspect(root)['runs']
            self.assertTrue(
                next(entry for entry in entries if entry['id'] == active.name)['active']
            )
        cleanup(root, count=2)
        self.assertEqual(len(inspect(root)['runs']), 2)

    def test_skill_copy_updates_owned_content_and_preserves_user_edits(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'SKILL.md').write_text('first')
        args = argparse.Namespace(skills_dir=self.root / 'skills', dry_run=False, remove=False)
        with patch('computer_artist.config.skill_source', return_value=source):
            self.assertEqual(install(args)['action'], 'install')
            self.assertEqual(install(args)['action'], 'unchanged')
            (source / 'SKILL.md').write_text('second')
            self.assertEqual(install(args)['action'], 'update')
            destination = args.skills_dir / 'computer-artist'
            self.assertEqual((destination / 'SKILL.md').read_text(), 'second')
            (destination / 'SKILL.md').write_text('user modification')
            with self.assertRaises(ValueError):
                install(args)
            self.assertEqual((destination / 'SKILL.md').read_text(), 'user modification')

    def test_owned_skill_copy_remove_does_not_remove_its_source(self):
        source = self.root / 'source'
        source.mkdir()
        (source / 'SKILL.md').write_text('skill')
        args = argparse.Namespace(skills_dir=self.root / 'skills', dry_run=False, remove=False)
        with patch('computer_artist.config.skill_source', return_value=source):
            install(args)
            args.remove = True
            self.assertEqual(install(args)['action'], 'remove')
            self.assertTrue((source / 'SKILL.md').exists())

    def test_job_closes_live_worker_and_descendant(self):
        from computer_artist.windows_process import ProcessJob

        heartbeat = self.root / 'heartbeat'
        child = (
            'import time; from pathlib import Path; p=Path(%r);\nwhile True: p.write_text(str(time.time())); time.sleep(.02)'
            % str(heartbeat)
        )
        parent = f'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(10)'
        process = subprocess.Popen(
            [sys.executable, '-c', parent], creationflags=subprocess.CREATE_NO_WINDOW
        )
        job = ProcessJob(process)
        try:
            until = time.monotonic() + 2
            while not heartbeat.exists() and time.monotonic() < until:
                time.sleep(0.02)
            self.assertTrue(heartbeat.exists())
            job.close()
            process.wait(timeout=2)
            before = heartbeat.stat().st_mtime_ns
            time.sleep(0.1)
            self.assertEqual(heartbeat.stat().st_mtime_ns, before)
        finally:
            job.close()
            if process.poll() is None:
                process.kill()
                process.wait()
