import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from computer_artist.memory import atomic_json
from computer_artist.storage import MARKER, managed_run, cleanup, inspect, preserve


class StorageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'runs'

    def run_folder(self, name, created, size=20):
        folder = self.root/name
        folder.mkdir(parents=True)
        atomic_json(folder/MARKER, {'format':1,'created':created,'completed':True})
        (folder/'.active.lock').touch()
        (folder/'data').write_bytes(b'x'*size)
        return folder

    def test_keeps_newest_whole_runs_ignores_unmanaged_and_symlinks(self):
        for i in range(18): self.run_folder(str(i),i)
        unmanaged=self.root/'manual';unmanaged.mkdir();(unmanaged/'keep').write_text('important')
        external=Path(self.temp.name)/'external';external.mkdir()
        (self.root/'linked').symlink_to(external,target_is_directory=True)
        report=cleanup(self.root)
        self.assertEqual(report['removed'],['0','1','2'])
        self.assertTrue(unmanaged.exists());self.assertTrue(external.exists())
        self.assertEqual(len(inspect(self.root)['runs']),15)

    def test_active_and_preserved_runs_survive_size_pressure(self):
        self.run_folder('old',1,10000);preserve(self.root,'old')
        self.run_folder('discard',2,10000)
        self.run_folder('latest',3,10000)
        with managed_run(self.root,'working') as working:
            (working/'data').write_text('still running')
            report=cleanup(self.root,count=1,max_bytes=1)
            self.assertTrue(working.exists());self.assertTrue((self.root/'old').exists())
            self.assertEqual(report['removed'],['discard'])
            self.assertTrue(report['over_budget'])
            self.assertTrue((self.root/'latest').exists())
        self.assertFalse(next(e for e in inspect(self.root)['runs'] if e['id']=='working')['active'])

    def test_dry_run_and_byte_limit(self):
        self.run_folder('old',1,1000);self.run_folder('new',2,1000)
        report=cleanup(self.root,max_bytes=1500,dry_run=True)
        self.assertEqual(report['would_remove'],['old']);self.assertTrue((self.root/'old').exists())
        self.assertEqual(cleanup(self.root,max_bytes=1500)['removed'],['old'])

    def test_failed_run_closes_and_invalid_config_creates_nothing(self):
        with self.assertRaises(RuntimeError):
            with managed_run(self.root,'failed'): raise RuntimeError('task failed')
        self.assertEqual(inspect(self.root)['runs'][0]['status'],'completed')
        with patch.dict(os.environ,CA_RUN_LIMIT='0'):
            with self.assertRaises(ValueError):
                with managed_run(self.root,'invalid'): pass
        self.assertFalse((self.root/'invalid').exists())

    def test_child_lock_survives_supervisor_exit_and_abandoned_run_is_pruned(self):
        folder=self.run_folder('child',1)
        code="from computer_artist.storage import active_run; import sys; " \
             "c=active_run(sys.argv[1]); c.__enter__(); print('ready',flush=True); sys.stdin.read(); c.__exit__(None,None,None)"
        child=subprocess.Popen([sys.executable,'-c',code,str(folder)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(),'ready')
            self.run_folder('new',2)
            self.assertEqual(cleanup(self.root,count=1)['removed'],[])
        finally:
            child.communicate(timeout=5)
        atomic_json(folder/MARKER, {'format':1,'created':1,'completed':False})
        self.assertEqual(cleanup(self.root,count=1)['removed'],['child'])

    def test_finishing_older_active_run_retains_its_returned_record(self):
        with patch.dict(os.environ,CA_RUN_LIMIT='1'):
            with managed_run(self.root,'slow') as folder:
                with managed_run(self.root,'fast'): pass
            self.assertTrue(folder.exists())
            cleanup(self.root)
            self.assertFalse(folder.exists())

    def test_automatic_cleanup_counts_the_just_finished_run(self):
        with patch.dict(os.environ, CA_RUN_LIMIT='2'):
            for i in range(5):
                with managed_run(self.root, str(i)): pass
        self.assertEqual([e['id'] for e in inspect(self.root)['runs']], ['3','4'])

    def test_storage_cli_is_offline_and_preserves_modules(self):
        self.run_folder('old',1)
        module=Path(self.temp.name)/'windows'/'app'/'module.py'
        module.parent.mkdir(parents=True);module.write_text('saved work')
        ca=Path(__file__).resolve().parents[1]/'ca'
        result=subprocess.run([str(ca),'storage','keep','old','--scope','runs',
                               '--memory-dir',self.temp.name,'--socket','/does/not/exist'],
                              capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(inspect(self.root)['runs'][0]['preserved'])
        self.assertEqual(module.read_text(),'saved work')
