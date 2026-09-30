import fcntl
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_artist.contracts import bind, contract
from computer_artist.files import atomic_json
from computer_artist.fragments import FragmentStore


class FragmentStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = FragmentStore(Path(self.tmp.name) / 'window')

    def test_registration_never_executes_imports_defaults_or_body(self):
        sentinel = Path(self.tmp.name) / 'executed'
        source = f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\ndef run(ctx, radius: float = 2):\n    return radius\n'
        info = self.memory.write('cylinder', source)
        self.assertFalse(sentinel.exists())
        self.assertEqual(bind(info, {}), {'radius': 2})
        with self.assertRaises(ValueError):
            self.memory.write('bad', 'def run(ctx, x: int = print("executed")): pass')

    def test_typed_constraints_and_unknown_arguments(self):
        manifest = contract(
            'CONTRACT={"parameters":{"radius":{"min":0.1,"max":20,"unit":"m"}}}\ndef run(ctx, radius: float, vertices: int = 32, smooth: bool = False): pass'
        )
        self.assertEqual(bind(manifest, {'radius': 2})['vertices'], 32)
        for values in (
            {'radius': 0},
            {'radius': True},
            {'radius': float('nan')},
            {'radius': 2, 'vertices': 3.5},
            {'radius': 2, 'extra': 0},
            {},
        ):
            with self.assertRaises(ValueError):
                bind(manifest, values)

    def test_versions_pin_code_no_silent_overwrite(self):
        original = self.memory.write('step', 'def run(ctx): return 1')
        with self.assertRaises(ValueError):
            self.memory.write('step', 'def run(ctx): return 2')
        updated = self.memory.write('step', 'def run(ctx): return 2', update=True)
        self.assertNotEqual(original['version'], updated['version'])
        self.assertIn('return 1', self.memory.load('step', original['version'])[1])
        self.assertIn('return 2', self.memory.load('step')[1])
        self.assertEqual(len(self.memory.history('step')), 2)
        self.assertTrue((self.memory.folder('step') / 'module.py').is_file())

    def test_shared_library_and_archive(self):
        self.memory.write('step', 'def run(ctx): return 1')
        self.assertEqual(self.memory.folder('step'), self.memory.root / 'api-fragmants' / 'step')
        self.assertEqual(self.memory.list()[0]['name'], 'step')
        result = self.memory.remove('step')
        self.assertEqual(self.memory.list(), [])
        self.assertTrue((Path(result['archived']) / 'module.py').is_file())

    def test_path_traversal_and_tampering_rejected(self):
        for identity in ('../outside', '/tmp/other', '..'):
            with self.assertRaises(ValueError):
                self.memory.folder(identity)
        info = self.memory.write('step', 'def run(ctx): return 1')
        (self.memory.folder('step') / 'versions' / info['version'] / 'module.py').write_text(
            'def run(ctx): return 2'
        )
        with self.assertRaises(ValueError):
            self.memory.load('step')

    def test_existing_files_prevent_publication_without_losing_current_revision(self):
        original = self.memory.write('step', 'def run(ctx): return 1')
        alias = self.memory.folder('step') / 'module.py'
        alias.unlink()
        alias.write_text('user maintained file')
        with self.assertRaises(ValueError):
            self.memory.write('step', 'def run(ctx): return 2', update=True)
        self.assertEqual(self.memory.load('step')[0]['version'], original['version'])
        self.assertEqual(alias.read_text(), 'user maintained file')
        self.assertEqual(len(self.memory.history('step')), 1)

    def test_load_holds_archive_lock_until_source_is_read(self):
        self.memory.write('step', 'def run(ctx): return 1')
        read_text = Path.read_text
        checked = []

        def read_under_lock(path, *args, **kwargs):
            if path.name == 'module.py':
                fd = os.open(self.memory.root / '.lock', os.O_RDWR)
                try:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    checked.append(True)
                finally:
                    os.close(fd)
            return read_text(path, *args, **kwargs)

        with patch.object(Path, 'read_text', read_under_lock):
            self.memory.load('step')
        self.assertEqual(checked, [True])

    def test_revision_manifest_cannot_redirect_source_outside_library(self):
        original = self.memory.write('step', 'def run(ctx): return 1')
        path = self.memory.folder('step') / 'versions' / original['version'] / 'manifest.json'
        original['version'] = '../outside'
        atomic_json(path, original)
        with self.assertRaises(ValueError):
            self.memory.load('step')

    def test_failed_atomic_json_preserves_existing_file_and_leaves_no_temporary(self):
        path = Path(self.tmp.name) / 'record.json'
        atomic_json(path, {'old': True})
        with patch('computer_artist.files.os.replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                atomic_json(path, {'new': True})
        self.assertEqual(json.loads(path.read_text()), {'old': True})
        self.assertEqual(list(path.parent.glob('.record.json.*')), [])

    def test_invalid_contracts_are_rejected_at_registration(self):
        declarations = (
            {'parameters': []},
            {'description': []},
            {'parameters': {'size': {'choices': [True]}}},
            {'parameters': {'size': {'min': 2, 'choices': [1]}}},
            {'window': {'min_width': 10, 'max_width': 5}},
        )
        for declaration in declarations:
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                contract(f'CONTRACT={declaration!r}\ndef run(ctx, size: int = 2): pass')

    def test_reading_missing_library_does_not_create_directories(self):
        self.assertEqual(self.memory.list(), [])
        self.assertEqual(self.memory.history('missing'), [])
        with self.assertRaises(FileNotFoundError):
            self.memory.load('missing')
        self.assertFalse(self.memory.root.exists())


if __name__ == '__main__':
    unittest.main()
