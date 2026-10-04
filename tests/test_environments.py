import io
import json
import os
import socket
import tempfile
import time
import unittest
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from computer_artist import environment_apps, environments
from computer_artist.cli import main, parser


def flags(**values):
    names = ('recipe', 'image', 'containerfile', 'context', 'project', 'workdir', 'cpus', 'memory')
    names += ('width', 'height', 'network', 'xwayland', 'gpu', 'mount', 'env')
    return Namespace(**{name: values.get(name) for name in names})


class EnvironmentDefinitionTest(unittest.TestCase):
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
        os.environ.pop('CA_HOST_RUNTIME_DIR', None)

    def test_names_are_strict(self):
        for name in ('', '-x', 'a/b', '../x', 'x' * 49, 'a b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                environments.check_name(name)
        self.assertEqual(environments.container_name('web_1'), 'ca-env-web_1')

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

    def test_host_environments_refuse_container_settings(self):
        with self.assertRaisesRegex(ValueError, 'require image'):
            environments.define('plain', flags(cpus=2.0))

    def test_first_prototype_environments_are_migrated(self):
        state = self.root / 'environments' / 'old'
        state.mkdir(parents=True)
        (state / 'environment.json').write_text(
            json.dumps({'width': 1280, 'height': 800, 'xwayland': True})
        )
        definition = environments.stored('old')
        self.assertEqual(
            (definition['width'], definition['xwayland'], environments.kind(definition)),
            (1280, True, 'host'),
        )

    def test_container_sees_only_the_shared_sockets_and_declared_folders(self):
        project = self.root / 'project'
        project.mkdir()
        definition = environments.normalize(
            {'image': 'alpine', 'project': str(project), 'cpus': 2, 'network': 'host'}, self.root
        )
        state, runtime = environments.locations('web')
        command = environments.container_command('web', definition, 'alpine', state, runtime)
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

    def test_bus_configuration_escapes_paths(self):
        config = environments.bus_config(Path('/run/a&b/bus'))
        self.assertIn('unix:path=/run/a&amp;b/bus', config)

    def test_remove_requires_confirmation_and_a_stopped_environment(self):
        environments.define('gone', flags())
        with self.assertRaisesRegex(ValueError, '--yes'):
            environments.remove('gone', False)
        with patch.object(environments, 'run'):
            environments.remove('gone', True)
        self.assertIsNone(environments.stored('gone'))


class EnvironmentCommandTest(unittest.TestCase):
    def test_exec_keeps_application_options_after_the_separator(self):
        args = parser().parse_args(
            ['env', 'exec', 'web', '--name', 'term', '--', 'foot', '--title', 'x']
        )
        self.assertEqual((args.window_name, args.argv), ('term', ['foot', '--title', 'x']))

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


class BrowserProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_live_profiles_are_detected(self):
        profile = self.root / 'chromium'
        profile.mkdir()
        lock = profile / 'SingletonLock'
        self.assertFalse(environment_apps.profile_in_use(profile, 'chromium'))
        lock.symlink_to(f'{socket.gethostname()}-{os.getpid()}')
        self.assertTrue(environment_apps.profile_in_use(profile, 'chromium'))
        lock.unlink()
        lock.symlink_to(f'{socket.gethostname()}-999999999')
        self.assertFalse(environment_apps.profile_in_use(profile, 'chromium'))
        lock.unlink()
        lock.symlink_to(f'another-host-{os.getpid()}')
        self.assertTrue(environment_apps.profile_in_use(profile, 'chromium'))
        firefox = self.root / 'firefox'
        firefox.mkdir()
        (firefox / 'lock').symlink_to(f'127.0.0.1:+{os.getpid()}')
        self.assertTrue(environment_apps.profile_in_use(firefox, 'firefox'))

    def test_copy_skips_caches_and_locks_and_refuses_live_profiles(self):
        source = self.root / 'source'
        (source / 'Default/Cache').mkdir(parents=True)
        (source / 'Default/Cache/data').write_text('cache')
        (source / 'Default/Cookies').write_text('cookies')
        (source / 'Local State').write_text('{}')
        (source / 'SingletonLock').symlink_to(f'{socket.gethostname()}-999999999')
        result = environment_apps.copy_profile(source, self.root / 'copy', 'chromium')
        self.assertEqual(result['files'], 2)
        self.assertTrue((self.root / 'copy/Default/Cookies').is_file())
        self.assertFalse((self.root / 'copy/Default/Cache').exists())
        self.assertFalse((self.root / 'copy/SingletonLock').is_symlink())
        (source / 'SingletonLock').unlink()
        (source / 'SingletonLock').symlink_to(f'{socket.gethostname()}-{os.getpid()}')
        with self.assertRaisesRegex(ValueError, 'in use'):
            environment_apps.copy_profile(source, self.root / 'copy2', 'chromium')
        self.assertFalse((self.root / 'copy2').exists())


class ManagerTest(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
