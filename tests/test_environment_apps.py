import os
import socket
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from computer_artist import environment_apps
from computer_artist.files import key


class BrowserCopyProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.profile = self.state / 'browsers' / key('work')
        self.profile.mkdir(parents=True)
        (self.profile / 'old').write_text('previous session')
        self.source = self.root / 'source'
        (self.source / 'Default').mkdir(parents=True)
        (self.source / 'Default/Cookies').write_text('cookies')
        patches = [
            patch.object(environment_apps, 'route', return_value={}),
            patch.object(environment_apps, 'locations', return_value=(self.state, self.root)),
            patch.object(environment_apps, 'stored', return_value=None),
            patch.object(environment_apps, 'kind', return_value='container'),
            patch.object(environment_apps, 'launch', return_value={'ok': True}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def args(self, source):
        return Namespace(
            name='dev',
            on_host=False,
            executable='chromium',
            url='https://example.test',
            profile='work',
            copy_profile=str(source),
            replace_profile=True,
            family='chromium',
            browser_args=None,
            window_name=None,
            timeout=5,
        )

    def test_live_source_refused_before_existing_profile_is_touched(self):
        lock = self.source / 'SingletonLock'
        lock.symlink_to(f'{socket.gethostname()}-{os.getpid()}')
        with self.assertRaisesRegex(ValueError, 'in use'):
            environment_apps.browser(self.args(self.source))
        self.assertEqual((self.profile / 'old').read_text(), 'previous session')

    def test_failed_copy_keeps_existing_profile_and_leaves_no_partial_copy(self):
        def partial_copy(src, dst, **kwargs):
            Path(dst).mkdir()
            (Path(dst) / 'Cookies').write_text('partial')
            raise OSError('disk full')

        with patch('computer_artist.environment_apps.shutil.copytree', side_effect=partial_copy):
            with self.assertRaisesRegex(OSError, 'disk full'):
                environment_apps.browser(self.args(self.source))
        self.assertEqual((self.profile / 'old').read_text(), 'previous session')
        self.assertEqual([p.name for p in self.profile.parent.iterdir()], ['work'])

    def test_replace_swaps_in_the_complete_copy(self):
        result = environment_apps.browser(self.args(self.source))
        self.assertFalse((self.profile / 'old').exists())
        self.assertEqual((self.profile / 'Default/Cookies').read_text(), 'cookies')
        self.assertEqual([p.name for p in self.profile.parent.iterdir()], ['work'])
        self.assertEqual(result['copied_profile']['skipped_symlinks'], 0)


class CopyProfileSymlinkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_links_leaving_the_source_are_skipped_and_counted(self):
        external = self.root / 'external'
        external.mkdir()
        (external / 'secret').write_text('host data')
        source = self.root / 'source'
        (source / 'Default').mkdir(parents=True)
        (source / 'Default/Cookies').write_text('cookies')
        (source / 'Shared').symlink_to('Default/Cookies')
        (source / 'Outside').symlink_to(external / 'secret')
        (source / 'Escape').symlink_to('../external/secret')
        (source / 'Mirror').symlink_to(source / 'Default/Cookies')

        result = environment_apps.copy_profile(source, self.root / 'copy', 'chromium')

        copy = self.root / 'copy'
        self.assertEqual(result['skipped_symlinks'], 3)
        self.assertEqual(os.readlink(copy / 'Shared'), 'Default/Cookies')
        for name in ('Outside', 'Escape', 'Mirror'):
            self.assertFalse(os.path.lexists(copy / name), name)
        self.assertEqual((copy / 'Default/Cookies').read_text(), 'cookies')


if __name__ == '__main__':
    unittest.main()
