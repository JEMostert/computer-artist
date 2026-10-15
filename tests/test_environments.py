import io
import os
import subprocess
import tempfile
import time
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from computer_artist import environments
from computer_artist.cli import main
from computer_artist.containers import Runtime


def flags(**values):
    names = ('recipe', 'image', 'containerfile', 'context', 'project', 'workdir', 'cpus', 'memory')
    names += ('width', 'height', 'network', 'xwayland', 'gpu', 'mount', 'env')
    return Namespace(**{name: values.get(name) for name in names})


class EnvironmentDefinitionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()  # macOS temp folders are symlinks.
        patcher = patch.dict(
            os.environ,
            CA_ENVIRONMENTS_DIR=str(self.root / 'environments'),
            XDG_RUNTIME_DIR=str(self.root / 'runtime'),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop('CA_HOST_RUNTIME_DIR', None)

    def test_project_mounts_at_the_same_path_and_sets_workdir(self):
        project = self.root / 'project'
        (project / 'assets').mkdir(parents=True)
        definition = environments.normalize(
            {'image': 'alpine', 'project': '.', 'mounts': ['assets:/data:ro'], 'memory': '2g'},
            project,
        )
        self.assertEqual(
            definition['mounts'],
            [
                {'source': str(project), 'target': str(project), 'mode': 'rw'},
                {'source': str(project / 'assets'), 'target': '/data', 'mode': 'ro'},
            ],
        )
        self.assertEqual(definition['workdir'], str(project))

    def test_invalid_definitions_are_refused(self):
        cases = [
            {'unknown': 1},
            {'image': 'a', 'containerfile': 'Containerfile'},
            {'mounts': ['.']},
            {'image': 'a', 'mounts': ['missing']},
            {'image': 'a', 'mounts': ['.:relative']},
            {'image': 'a', 'memory': 'lots'},
            {'image': 'a', 'network': 'bridge'},
            {'width': 100},
            {'apps': [{'command': []}]},
            {'apps': [{'command': 'x', 'surprise': True}]},
            {'env': {'BAD KEY': 'x'}},
        ]
        for data in cases:
            with self.subTest(data=data), self.assertRaises(ValueError):
                environments.normalize(data, self.root)

    def test_recipe_paths_follow_the_recipe_and_flags_update_it(self):
        folder = self.root / 'recipes'
        folder.mkdir()
        (folder / 'Containerfile').write_text('FROM alpine\n')
        (folder / 'env.toml').write_text(
            'containerfile = "Containerfile"\n'
            'project = "."\n'
            'cpus = 2\n'
            '[env]\nMODE = "test"\n'
            '[[apps]]\ncommand = "foot --title term"\nwait_window = true\nname = "term"\n'
        )
        definition = environments.define('web', flags(recipe=folder / 'env.toml', memory='1g'))
        self.assertEqual(definition['containerfile'], str(folder / 'Containerfile'))
        self.assertEqual(definition['context'], str(folder))
        self.assertEqual(definition['env'], {'MODE': 'test'})
        self.assertEqual(definition['memory'], '1g')
        self.assertEqual(definition['apps'][0]['command'], ['foot', '--title', 'term'])
        self.assertEqual(definition['apps'][0]['wait_window'], '')
        self.assertEqual(definition['recipe'], str(folder / 'env.toml'))
        # Flags alone update the stored definition and keep its provenance.
        updated = environments.define('web', flags(cpus=4.0))
        self.assertEqual((updated['cpus'], updated['memory']), (4.0, '1g'))
        self.assertEqual(updated['recipe'], str(folder / 'env.toml'))
        self.assertEqual(environments.stored('web'), updated)

    def test_container_sees_only_the_shared_sockets_and_declared_folders(self):
        project = self.root / 'project'
        project.mkdir()
        definition = environments.normalize(
            {'image': 'alpine', 'project': str(project), 'cpus': 2, 'network': 'host'}, self.root
        )
        state, runtime = environments.locations('web')
        engine = Runtime('podman')
        command = environments.container_command(
            'web', definition, 'alpine', state, runtime, engine
        )
        volumes = [command[i + 1] for i, arg in enumerate(command) if arg == '--volume']
        self.assertIn(f'{runtime / "shared"}:{runtime / "shared"}', volumes)
        self.assertIn(f'{project}:{project}:rw', volumes)
        self.assertFalse(any(v.startswith(f'{runtime}:') for v in volumes))
        for expected in ('--userns=keep-id', '--init', '--network=host'):
            self.assertIn(expected, command)
        self.assertEqual(command[command.index('--cpus') + 1], '2')
        env = [command[i + 1] for i, arg in enumerate(command) if arg == '--env']
        self.assertIn(f'WAYLAND_DISPLAY={runtime / "shared/wayland"}', env)
        self.assertNotIn('CA_SOCKET', ' '.join(env))
        self.assertEqual(command[-4:], ['--entrypoint', 'sleep', 'alpine', '2147483647'])

    def test_remove_requires_confirmation_and_a_stopped_environment(self):
        environments.define('gone', flags())
        with patch.object(environments, 'run'):  # No systemctl on every test host.
            with self.assertRaisesRegex(ValueError, '--yes'):
                environments.remove('gone', False)
            environments.remove('gone', True)
        self.assertIsNone(environments.stored('gone'))

    def test_remove_refuses_unless_the_manager_is_known_stopped(self):
        environments.define('live', flags())
        state, _ = environments.locations('live')
        with patch.object(environments, 'exchange', side_effect=TimeoutError()):
            with self.assertRaisesRegex(ValueError, 'not responding'):
                environments.remove('live', True)
        active = MagicMock(stdout='active\n')
        with patch.object(environments, 'run', return_value=active) as run:
            with self.assertRaisesRegex(ValueError, 'still active'):
                environments.remove('live', True)
        self.assertIn('is-active', run.call_args.args[0])
        with (
            patch.object(environments, 'exchange', return_value={'running': True}),
            self.assertRaisesRegex(ValueError, 'running'),
        ):
            environments.remove('live', True)
        self.assertTrue(state.exists())
        with patch.object(environments, 'run', return_value=MagicMock(stdout='inactive\n')):
            environments.remove('live', True)
        self.assertFalse(state.exists())

    def test_start_refuses_when_the_manager_is_not_responding(self):
        environments.define('slow', flags())
        with (
            patch.object(environments, 'exchange', side_effect=TimeoutError()),
            self.assertRaisesRegex(ValueError, 'not responding'),
        ):
            environments.start(Namespace(name='slow', **vars(flags())))


class EnvironmentCommandTest(unittest.TestCase):
    def setUp(self):
        # These cover the Linux backend; macOS and Windows route to boxes (test_box).
        patcher = patch('computer_artist.box.enabled', return_value=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_environment_selects_its_private_host_lane_and_shares_fragments(self):
        info = {
            'name': 'web',
            'kind': 'container',
            'socket': '/private/control',
            'window_dir': '/private/window',
            'output_dir': '/private/output',
        }
        for argv, lane in ((['windows'], 'host'), (['--agent', 'windows'], 'agent')):
            client = MagicMock()
            client.__enter__.return_value = client
            client.request.return_value = {'ok': True, 'windows': []}
            client.window_store.display_windows.side_effect = lambda windows: windows
            with (
                self.subTest(argv=argv),
                patch.dict(os.environ),
                patch('computer_artist.environments.route', return_value=info),
                patch('computer_artist.cli.Client', return_value=client) as factory,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(main(['--environment', 'web', *argv]), 0)
                self.assertEqual(factory.call_args.args[0], '/private/control')
                self.assertEqual(factory.call_args.kwargs['lane'], lane)
                self.assertEqual(factory.call_args.kwargs['window_dir'], '/private/window')
                self.assertNotEqual(os.environ['CA_FRAGMENT_ROOT'], '/private/window')

    def test_environment_never_combines_with_an_explicit_socket(self):
        with (
            patch('computer_artist.environments.route') as route,
            redirect_stdout(io.StringIO()),
            patch('sys.stderr', io.StringIO()) as error,
        ):
            self.assertEqual(main(['--environment', 'web', '--socket', '/x', 'windows']), 1)
        route.assert_not_called()
        self.assertIn('cannot be combined', error.getvalue())

    def test_stopped_environment_is_an_error_not_a_fallback(self):
        with (
            patch(
                'computer_artist.environments.status',
                return_value={'ok': True, 'running': False},
            ),
            patch('computer_artist.cli.Client') as client,
            patch('sys.stderr', io.StringIO()) as error,
        ):
            self.assertEqual(main(['--environment', 'web', 'windows']), 1)
        client.assert_not_called()
        self.assertIn('not running', error.getvalue())


class ManagerTest(unittest.TestCase):
    def setUp(self):
        # Never start the host's real AT-SPI registry from these tests.
        patcher = patch('computer_artist.environment_service.REGISTRY_PATHS', ())
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_host_applications_report_exit_status_and_logs(self):
        from computer_artist.environment_service import Manager

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'state/logs/apps').mkdir(parents=True)
            (root / 'runtime/shared').mkdir(parents=True)
            config = {
                'name': 'web',
                'runtime': str(root / 'runtime'),
                'state': str(root / 'state'),
                'width': 800,
                'height': 600,
                'xwayland': False,
                'container': None,
            }
            with (
                patch.dict(os.environ, {'XAUTHORITY': ''}),
                patch('computer_artist.environment_service.subprocess.run'),
            ):
                manager = Manager(config)
            app = manager.handle(
                {'op': 'exec', 'argv': ['sh', '-c', 'echo ready; exit 3'], 'cwd': folder}
            )['app']
            self.assertEqual((app['id'], app['where']), ('a1', 'host'))
            until = time.monotonic() + 5
            while manager.handle({'op': 'app', 'app': 'a1'})['app']['alive']:
                self.assertLess(time.monotonic(), until)
                time.sleep(0.02)
            record = manager.handle({'op': 'status'})['applications'][0]
            self.assertEqual(record['returncode'], 3)
            self.assertIn('ready', Path(record['log']).read_text())
            with self.assertRaises(ValueError):
                manager.handle({'op': 'exec', 'argv': ['bad\0arg']})


class AccessibilityTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        (self.root / 'state/logs/apps').mkdir(parents=True)
        (self.root / 'runtime/shared').mkdir(parents=True)
        self.config = {
            'name': 'web',
            'runtime': str(self.root / 'runtime'),
            'state': str(self.root / 'state'),
            'width': 800,
            'height': 600,
            'xwayland': False,
            'container': None,
        }
        self.binary = self.root / 'at-spi2-registryd'
        self.binary.write_text('')

    def start(self, paths, popen=None):
        from computer_artist.environment_service import Manager

        popen = popen or MagicMock()
        with (
            patch.dict(os.environ, {'XAUTHORITY': ''}),
            patch('computer_artist.environment_service.REGISTRY_PATHS', paths),
            patch('computer_artist.environment_service.subprocess.run'),
            patch('computer_artist.environment_service.subprocess.Popen', popen),
        ):
            return Manager(self.config), popen

    def test_registry_starts_with_the_application_environment(self):
        manager, popen = self.start((str(self.binary),))
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], [str(self.binary)])
        self.assertIs(popen.call_args.kwargs['env'], manager.env)
        self.assertEqual(manager.env['QT_LINUX_ACCESSIBILITY_ALWAYS_ON'], '1')
        self.assertIs(manager.registry, popen.return_value)
        self.assertTrue(manager.info['accessibility'])
        self.assertTrue((self.root / 'state/logs/at-spi-registryd.log').exists())

    def test_close_terminates_the_registry_and_kills_it_if_it_ignores_terminate(self):
        manager, popen = self.start((str(self.binary),))
        process = popen.return_value
        manager.close()
        process.terminate.assert_called_once()
        process.wait.assert_called_once_with(2)
        process.kill.assert_not_called()
        process.wait.side_effect = subprocess.TimeoutExpired('at-spi2-registryd', 2)
        manager.close()
        process.kill.assert_called_once()


if __name__ == '__main__':
    unittest.main()
