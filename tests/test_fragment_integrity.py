"""Corrupt reusable procedures remain inspectable and recoverable without execution."""

import json
import tempfile
import unittest
from pathlib import Path

from computer_artist.fragments import FragmentStore


class FragmentIntegrityTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = FragmentStore(self.root / 'window')

    def revision(self, name, manifest):
        return self.store.folder(name) / 'versions' / manifest['version']

    def test_current_link_cannot_load_a_manifest_from_outside_its_revision(self):
        manifest = self.store.write('step', 'def run(ctx): return 1')
        external = self.root / 'external'
        external.mkdir()
        (external / 'manifest.json').write_text(json.dumps(manifest))
        current = self.store.folder('step') / 'current'
        current.unlink()
        current.symlink_to(external)
        with self.assertRaises(ValueError):
            self.store.load('step')
        self.assertEqual(
            self.store.load('step', manifest['version'])[0]['version'], manifest['version']
        )

    def test_symlink_revision_and_files_are_rejected(self):
        for component in ('revision', 'module.py', 'manifest.json'):
            with self.subTest(component=component):
                name = 'step-' + component.replace('.', '-')
                manifest = self.store.write(name, 'def run(ctx): return 1')
                revision = self.revision(name, manifest)
                if component == 'revision':
                    external = self.root / name
                    revision.rename(external)
                    revision.symlink_to(external, target_is_directory=True)
                else:
                    target = revision / component
                    external = self.root / (name + '-external')
                    target.rename(external)
                    target.symlink_to(external)
                with self.assertRaises(ValueError):
                    self.store.load(name, manifest['version'])

    def test_manifest_contract_cannot_weaken_source_preconditions(self):
        manifest = self.store.write(
            'host-procedure',
            'CONTRACT={"lane":"host", "requires":["focus"], '
            '"window":{"min_width":50}}\ndef run(ctx, size: int = 3): return size',
        )
        path = self.revision('host-procedure', manifest) / 'manifest.json'
        manifest.update(lane='any', requires=[], window={}, parameters={})
        path.write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            self.store.load('host-procedure')

    def test_restore_valid_history_after_current_manifest_corruption(self):
        original = self.store.write('step', 'def run(ctx): return 1')
        broken = self.store.write('step', 'def run(ctx): return 2', update=True)
        path = self.revision('step', broken) / 'manifest.json'
        path.write_text('{invalid')
        broken_bytes = path.read_bytes()
        restored = self.store.restore('step', original['version'])
        self.assertEqual(restored['name'], 'step')
        self.assertEqual(restored['version'], original['version'])
        self.assertEqual(restored['previous_version'], broken['version'])
        self.assertIn('return 1', self.store.load('step')[1])
        self.assertEqual(path.read_bytes(), broken_bytes)
        self.assertEqual(len(list((self.store.folder('step') / 'versions').iterdir())), 2)

    def test_restore_refuses_invalid_destination_without_changing_current(self):
        broken = self.store.write('step', 'def run(ctx): return 1')
        good = self.store.write('step', 'def run(ctx): return 2', update=True)
        path = self.revision('step', broken) / 'module.py'
        path.write_text('tampered source')
        with self.assertRaises(ValueError):
            self.store.restore('step', broken['version'])
        self.assertEqual(self.store.load('step')[0]['version'], good['version'])
        self.assertEqual(path.read_text(), 'tampered source')

    def test_archive_refuses_symlink_destination_and_preserves_fragment(self):
        manifest = self.store.write('step', 'def run(ctx): return 1')
        outside = self.root / 'outside'
        outside.mkdir()
        trash = self.store.root / 'api-fragmants' / 'trash'
        trash.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.remove('step')
        self.assertEqual(self.store.load('step')[0]['version'], manifest['version'])
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
