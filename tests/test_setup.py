import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.env = {**os.environ, 'HOME': str(self.home), 'CA_SOCKET': '/no-socket',
                    'XDG_RUNTIME_DIR': '', 'CA_WINDOW_DIR': str(self.home/'fragments')}

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, str(ROOT/'ca'), 'setup', *args],
                                env=self.env, cwd=self.home, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stderr)
        return json.loads(result.stdout if ok else result.stderr)

    def test_offline_default_and_idempotency(self):
        result = self.cli('--codex')
        target = self.home/'.agents/skills/computer-artist'
        self.assertEqual(result['destination'], str(target))
        self.assertTrue((target/'SKILL.md').is_file())
        self.assertTrue((target/'references/programs.md').is_file())
        self.assertEqual(target.resolve(), ROOT/'skills/computer-artist')
        self.assertEqual(self.cli('--codex')['action'], 'unchanged')
        self.assertFalse((self.home/'fragments').exists())

    def test_dry_run_and_custom_destination(self):
        root = self.home/'custom skills'
        result = self.cli('--skills-dir', str(root), '--dry-run')
        self.assertEqual(result['action'], 'install')
        self.assertFalse(root.exists())
        self.cli('--skills-dir', str(root))
        self.assertTrue((root/'computer-artist').is_symlink())

    def test_conflicts_never_overwrite_even_on_remove(self):
        root = self.home/'skills'; root.mkdir()
        target = root/'computer-artist'; target.mkdir()
        (target/'SKILL.md').write_text('user skill')
        for options in ((), ('--remove',), ('--dry-run',)):
            self.cli('--skills-dir', str(root), *options, ok=False)
        self.assertEqual((target/'SKILL.md').read_text(), 'user skill')
        (target/'SKILL.md').unlink(); target.rmdir()
        target.symlink_to(self.home/'missing')
        self.cli('--skills-dir', str(root), ok=False)
        self.assertTrue(target.is_symlink())

    def test_remove_only_link_and_preview(self):
        self.cli('--codex')
        target = self.home/'.agents/skills/computer-artist'
        self.assertEqual(self.cli('--codex', '--remove', '--dry-run')['action'], 'remove')
        self.assertTrue(target.is_symlink())
        self.assertEqual(self.cli('--codex', '--remove')['action'], 'remove')
        self.assertFalse(target.is_symlink())
        self.assertTrue((ROOT/'skills/computer-artist/SKILL.md').is_file())
        self.assertEqual(self.cli('--codex', '--remove')['action'], 'absent')
