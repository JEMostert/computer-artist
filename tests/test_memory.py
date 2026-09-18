import json
import os
from pathlib import Path
import tempfile
import unittest

from computer_artist.memory import Memory, bind, contract


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = Memory(self.tmp.name)

    def test_registration_never_executes_imports_defaults_or_body(self):
        sentinel = Path(self.tmp.name)/'executed'
        source = f'from pathlib import Path\nPath({str(sentinel)!r}).touch()\ndef run(ctx, radius: float = 2):\n    return radius\n'
        info = self.memory.write('window','cylinder',source)
        self.assertFalse(sentinel.exists())
        self.assertEqual(bind(info,{}),{'radius':2})
        with self.assertRaises(ValueError):
            self.memory.write('window','bad','def run(ctx, x: int = print("executed")): pass')

    def test_typed_constraints_and_unknown_arguments(self):
        manifest = contract('CONTRACT={"parameters":{"radius":{"min":0.1,"max":20,"unit":"m"}}}\ndef run(ctx, radius: float, vertices: int = 32, smooth: bool = False): pass')
        self.assertEqual(bind(manifest,{'radius':2})['vertices'],32)
        for values in ({'radius':0},{'radius':True},{'radius':float('nan')},{'radius':2,'vertices':3.5},{'radius':2,'extra':0},{}):
            with self.assertRaises(ValueError): bind(manifest,values)

    def test_versions_pin_code_no_silent_overwrite(self):
        original=self.memory.write('w','step','def run(ctx): return 1')
        with self.assertRaises(ValueError): self.memory.write('w','step','def run(ctx): return 2')
        updated=self.memory.write('w','step','def run(ctx): return 2',update=True)
        self.assertNotEqual(original['version'],updated['version'])
        self.assertIn('return 1',self.memory.load('w','step',original['version'])[1])
        self.assertIn('return 2',self.memory.load('w','step')[1])
        self.assertEqual(len(self.memory.history('w','step')),2)
        self.assertTrue((self.memory.folder('w','step')/'module.py').is_file())

    def test_attach_promote_and_archive_preserve_source_and_mark_unverified(self):
        self.memory.write('old','step','def run(ctx): return 1')
        self.memory.promote('old','step','blender')
        self.memory.attach('new','blender',from_app=True)
        info,code=self.memory.load('new','step')
        self.assertEqual(info['compatibility'],'unverified')
        self.assertEqual(info['origin']['scope'],'apps')
        with self.assertRaises(ValueError): self.memory.attach('new','old')
        result=self.memory.remove('new','step')
        self.assertEqual(self.memory.list('new'),[])
        self.assertTrue(Path(result['archived']).exists())

    def test_path_traversal_and_tampering_rejected(self):
        for identity in ('../outside','/tmp/other','..'):
            with self.assertRaises(ValueError): self.memory.list(identity)
        info=self.memory.write('w','step','def run(ctx): return 1')
        (self.memory.folder('w','step')/'versions'/info['version']/'module.py').write_text('def run(ctx): return 2')
        with self.assertRaises(ValueError): self.memory.load('w','step')


if __name__ == '__main__': unittest.main()
