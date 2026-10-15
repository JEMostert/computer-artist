import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_artist import containers, environments
from computer_artist.containers import Runtime, choose


def which(*present):
    return lambda name: f'/usr/bin/{name}' if name in present else None


class ChooseTest(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('CA_CONTAINER_RUNTIME', None)

    def test_podman_is_preferred_then_docker(self):
        with patch.object(containers.shutil, 'which', which('podman', 'docker')):
            self.assertEqual(choose().name, 'podman')
        with patch.object(containers.shutil, 'which', which('docker')):
            self.assertEqual(choose().name, 'docker')


class DockerCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = patch.dict(
            os.environ,
            CA_ENVIRONMENTS_DIR=str(self.root / 'environments'),
            XDG_RUNTIME_DIR=str(self.root / 'runtime'),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def command(self, engine, **values):
        definition = environments.normalize({'image': 'alpine', **values}, self.root)
        state, runtime = environments.locations('web')
        return environments.container_command('web', definition, 'alpine', state, runtime, engine)

    @unittest.skipIf(os.name == 'nt', 'Linux user mapping')
    def test_rootful_docker_runs_as_the_invoking_user(self):
        engine = Runtime('docker')
        engine._rootless = False
        command = self.command(engine)
        self.assertEqual(command[:2], ['docker', 'run'])
        self.assertNotIn('--userns=keep-id', command)
        self.assertNotIn('--replace', command)
        user = command[command.index('--user') + 1]
        self.assertEqual(user, f'{os.getuid()}:{os.getgid()}')
        self.assertEqual(command[-4:], ['--entrypoint', 'sleep', 'alpine', '2147483647'])

    def test_rootless_docker_keeps_container_root_as_the_user(self):
        engine = Runtime('docker')
        engine._rootless = True
        self.assertNotIn('--user', self.command(engine))

    def test_podman_keeps_its_user_namespace_and_replacement(self):
        command = self.command(Runtime('podman'))
        self.assertIn('--userns=keep-id', command)
        self.assertIn('--replace', command)

    def test_docker_requests_nvidia_gpus_with_its_own_flag(self):
        engine = Runtime('docker')
        engine._rootless = True
        with patch.object(environments, 'gpu_devices', return_value=['nvidia.com/gpu=all']):
            command = self.command(engine, gpu=True)
        self.assertEqual(command[command.index('--gpus') + 1], 'all')
        self.assertNotIn('nvidia.com/gpu=all', command)

    def test_started_environment_keeps_its_recorded_runtime(self):
        state, _ = environments.locations('web')
        state.mkdir(parents=True)
        (state / 'environment.json').write_text('{"container_runtime": "docker"}')
        self.assertEqual(environments.engine_for('web').name, 'docker')


if __name__ == '__main__':
    unittest.main()
