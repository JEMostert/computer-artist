import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_explicit_and_environment_choice_win(self):
        with patch.object(containers.shutil, 'which', which('podman', 'docker')):
            self.assertEqual(choose('docker').name, 'docker')
            os.environ['CA_CONTAINER_RUNTIME'] = 'docker'
            self.assertEqual(choose().name, 'docker')
            self.assertEqual(choose('podman').name, 'podman')

    def test_missing_runtimes_tell_the_agent_to_inform_the_user(self):
        with (
            patch.object(containers.shutil, 'which', which()),
            self.assertRaisesRegex(RuntimeError, 'Tell the user to install one'),
        ):
            choose()
        with (
            patch.object(containers.shutil, 'which', which('podman')),
            self.assertRaisesRegex(RuntimeError, 'docker was selected but is not installed'),
        ):
            choose('docker')
        with self.assertRaisesRegex(ValueError, 'must be one of'):
            choose('lxc')

    def test_unreachable_docker_daemon_is_reported(self):
        engine = Runtime('docker')
        failed = MagicMock(returncode=1, stderr='Cannot connect to the Docker daemon', stdout='')
        with (
            patch.object(containers.subprocess, 'run', return_value=failed),
            self.assertRaisesRegex(RuntimeError, 'start Docker Desktop or the docker service'),
        ):
            engine.check()


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

    def test_exec_and_cleanup_words_differ_per_runtime(self):
        docker, podman = Runtime('docker'), Runtime('podman')
        self.assertEqual(
            docker.exec_command('c', ['app'], {'A': '1'}, '/w'),
            ['docker', 'exec', '--env', 'A=1', '--workdir', '/w', 'c', 'app'],
        )
        self.assertIn('--interactive=false', podman.exec_command('c', ['app']))
        self.assertEqual(docker.remove_words('c'), ['rm', '--force', 'c'])
        self.assertIn('--ignore', podman.remove_words('c'))
        self.assertEqual(
            docker.build_command('t', 'F', 'ctx', pull=True),
            ['docker', 'build', '--tag', 't', '--file', 'F', '--pull', 'ctx'],
        )

    def test_runtime_is_validated_and_needs_a_container(self):
        definition = environments.normalize({'image': 'alpine', 'runtime': 'docker'}, self.root)
        self.assertEqual(definition['runtime'], 'docker')
        with self.assertRaisesRegex(ValueError, 'runtime must be one of'):
            environments.normalize({'image': 'alpine', 'runtime': 'lxc'}, self.root)
        with self.assertRaisesRegex(ValueError, 'require image or'):
            environments.normalize({'runtime': 'docker'}, self.root)

    def test_started_environment_keeps_its_recorded_runtime(self):
        state, _ = environments.locations('web')
        state.mkdir(parents=True)
        (state / 'environment.json').write_text('{"container_runtime": "docker"}')
        self.assertEqual(environments.engine_for('web').name, 'docker')


if __name__ == '__main__':
    unittest.main()
