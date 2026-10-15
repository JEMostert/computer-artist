import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from computer_artist import update

HASH = 'a' * 64


def state(**changes):
    return {
        'checkout': HASH,
        'directory': '/plugins',
        'built': True,
        'loaded_ids': ['computerartist'],
        'running': {'source_sha256': HASH, 'busy': False},
        'versioned': 'computerartist-' + HASH[:12],
        'login_current': True,
        'login_enabled': True,
        'stale': [],
        **changes,
    }


class PlanTest(unittest.TestCase):
    def test_busy_lane_refuses_reload_unless_forced(self):
        current = state(running={'source_sha256': 'old', 'busy': True})
        with self.assertRaisesRegex(ValueError, 'session holds an input lane'):
            update.plan(current)
        self.assertIn('reload', update.plan(current, force=True))

    def test_unknown_lane_state_counts_as_busy(self):
        current = state(running={'source_sha256': None, 'busy': None, 'error': 'timed out'})
        with self.assertRaisesRegex(ValueError, 'timed out'):
            update.plan(current)

    def test_unloaded_plugin_is_never_activated(self):
        current = state(
            loaded_ids=[], running={'source_sha256': None, 'busy': None, 'error': 'no socket'}
        )
        self.assertEqual(update.plan(current), [])


class FilesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def test_prune_keeps_loaded_new_and_login_copies(self):
        for name in ('computerartist.so', 'computerartist-old.so', 'computerartist-live.so'):
            (self.directory / name).write_bytes(b'')
        current = state(directory=str(self.directory), versioned='computerartist-new')
        (self.directory / 'computerartist-new.so').write_bytes(b'')
        with (
            mock.patch.object(update, 'loaded_plugins', return_value=['computerartist-live']),
            mock.patch.object(update, 'run') as run,
        ):
            update.apply(Path('/checkout'), current, ['prune'])
        # A writable directory needs no sudo.
        run.assert_called_once_with('rm', '-f', str(self.directory / 'computerartist-old.so'))


class ReloadTest(unittest.TestCase):
    def test_failed_load_restores_previous_plugin(self):
        calls = []

        def kwin(method, *args):
            calls.append((method, *args))
            return 'false' if args == ('computerartist-' + HASH[:12],) else 'true'

        with (
            mock.patch.object(update, 'kwin', side_effect=kwin),
            mock.patch.object(
                update, 'backend', return_value={'source_sha256': 'old', 'busy': False}
            ),
            mock.patch.object(update, 'loaded_plugins', return_value=[]),
        ):
            with self.assertRaisesRegex(RuntimeError, r'running plugins now: \[.computerartist.\]'):
                update.reload(state())
        self.assertEqual(calls[-1], ('LoadPlugin', 'computerartist'))

    def test_session_started_during_build_stops_reload(self):
        busy = {'source_sha256': 'old', 'busy': True}
        with (
            mock.patch.object(update, 'kwin') as kwin,
            mock.patch.object(update, 'backend', return_value=busy),
        ):
            with self.assertRaisesRegex(ValueError, 'holds an input lane'):
                update.reload(state())
        kwin.assert_not_called()


class PullTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        base = Path(temp.name)
        self.origin, self.clone = base / 'origin', base / 'clone'

        def git(cwd, *args):
            subprocess.run(['git', '-C', str(cwd), *args], check=True, capture_output=True)

        self.git = git
        self.origin.mkdir()
        git(self.origin, 'init', '-q', '-b', 'main')
        git(self.origin, 'config', 'user.email', 'test@example.invalid')
        git(self.origin, 'config', 'user.name', 'Test')
        (self.origin / 'pyproject.toml').write_text('[project]\n')
        git(self.origin, 'add', '.')
        git(self.origin, 'commit', '-qm', 'first')
        git(base, 'clone', '-q', str(self.origin), str(self.clone))

    def test_fast_forwards_and_detects_dependency_change(self):
        (self.origin / 'pyproject.toml').write_text('[project]\nname = "x"\n')
        self.git(self.origin, 'commit', '-qam', 'second')
        pulled = update.pull(self.clone)
        self.assertNotEqual(pulled['from'], pulled['to'])
        self.assertEqual(pulled['branch'], 'main')
        with (
            mock.patch.object(update.sys, 'prefix', '/venv'),
            mock.patch.object(update.sys, 'base_prefix', '/usr'),
            mock.patch.object(update, 'run') as run,
        ):
            self.assertEqual(update.reinstall(self.clone, pulled), 'reinstalled')
        self.assertIn('pip', run.call_args.args)

    def test_system_python_is_never_pip_installed(self):
        (self.origin / 'pyproject.toml').write_text('[project]\nname = "x"\n')
        self.git(self.origin, 'commit', '-qam', 'second')
        pulled = update.pull(self.clone)
        with (
            mock.patch.object(update.sys, 'prefix', '/usr'),
            mock.patch.object(update.sys, 'base_prefix', '/usr'),
        ):
            self.assertIn('pip install -e', update.reinstall(self.clone, pulled))

    def test_refuses_uncommitted_changes(self):
        (self.clone / 'pyproject.toml').write_text('local edit\n')
        with self.assertRaisesRegex(ValueError, 'uncommitted changes'):
            update.pull(self.clone)


if __name__ == '__main__':
    unittest.main()
