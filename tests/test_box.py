import json
import os
import sys
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest.mock import patch

from computer_artist import box
from computer_artist.cli import parser


class BoxDefinitionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / 'project').mkdir()
        (self.root / 'assets').mkdir()
        patcher = patch.dict(
            os.environ,
            CA_ENVIRONMENTS_DIR=str(self.root / 'environments'),
            CA_WINDOW_DIR=str(self.root / 'window'),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_project_and_mounts_get_box_targets(self):
        definition = box.normalize(
            {'project': 'project', 'mounts': ['assets:/data:ro', 'assets']}, self.root
        )
        self.assertIn(
            {'source': str(self.root / 'project'), 'target': '/project', 'mode': 'rw'},
            definition['mounts'],
        )
        self.assertIn(
            {'source': str(self.root / 'assets'), 'target': '/mnt/assets', 'mode': 'rw'},
            definition['mounts'],
        )
        self.assertIn(
            {'source': str(self.root / 'assets'), 'target': '/data', 'mode': 'ro'},
            definition['mounts'],
        )
        self.assertEqual(definition['workdir'], '/project')

    def test_drive_letters_stay_in_the_source(self):
        with patch.object(box.Path, 'exists', return_value=True):
            mount = box.parse_mount(r'C:\Users\me\data:/data:ro', self.root)
        self.assertEqual((mount['target'], mount['mode']), ('/data', 'ro'))
        self.assertTrue(mount['source'].endswith(r'C:\Users\me\data'))

    def test_images_and_gpus_are_explained(self):
        for data in ({'image': 'alpine'}, {'gpu': True}):
            with (
                self.subTest(data=data),
                self.assertRaisesRegex(ValueError, 'FROM \\$\\{BOX_IMAGE'),
            ):
                box.normalize(data, self.root)
        with self.assertRaisesRegex(ValueError, 'absolute path inside the box'):
            box.normalize({'apps': [{'command': 'kwrite', 'cwd': 'relative'}]}, self.root)

    def test_run_command_mounts_state_window_and_project(self):
        definition = box.normalize({'project': 'project', 'width': 1280}, self.root)
        engine = box.Runtime('docker')
        engine._rootless = True
        command = box.run_command('web', definition, 'img', engine)
        mounts = [command[i + 1] for i, a in enumerate(command) if a == '--mount']
        self.assertIn(
            f'type=bind,source={self.root / "environments"},target=/ca/environments', mounts
        )
        self.assertIn(f'type=bind,source={self.root / "project"},target=/project', mounts)
        self.assertEqual(command[command.index('img') :], ['img', 'web', '--width', '1280',
                                                            '--height', '1000'])  # fmt: skip
        self.assertEqual(command[command.index('--platform') + 1], 'linux/amd64')
        if os.name == 'nt' or sys.platform == 'darwin':
            # Docker Desktop shares Windows and macOS folders with the image's own user.
            self.assertNotIn('--user', command)
            return
        self.assertEqual(command[command.index('--user') + 1], '0:0')
        engine._rootless = False
        command = box.run_command('web', definition, 'img', engine)
        self.assertEqual(command[command.index('--user') + 1], f'{os.getuid()}:{os.getgid()}')
        self.assertIn('--userns=keep-id:uid=1000,gid=1000', box.user_args(box.Runtime('podman')))

    def test_builds_pin_the_amd64_base(self):
        log = self.root / 'build.log'
        command = box.Runtime('docker').build_command('tag', 'Containerfile', '.')
        with patch.object(box.subprocess, 'run') as run:
            run.return_value.returncode = 0
            box.build(box.Runtime('docker'), command, log)
        self.assertEqual(
            run.call_args.args[0][:4], ['docker', 'build', '--platform', 'linux/amd64']
        )

    def test_macos_uses_boxes_and_has_no_desktop_socket(self):
        from computer_artist.config import socket_path

        with patch.object(box.sys, 'platform', 'darwin'), patch.dict(os.environ, {}, clear=True):
            self.assertTrue(box.enabled())
            self.assertEqual(socket_path('/tmp/explicit'), '/tmp/explicit')
            if os.name == 'nt':
                return  # Windows has its own desktop backend.
            self.assertEqual(box.user_args(box.Runtime('docker')), [])
            with self.assertRaisesRegex(ValueError, 'ca env start NAME'):
                socket_path()


class BoxPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.pairs = [
            (self.root / 'environments', PurePosixPath('/ca/environments')),
            (self.root / 'project', PurePosixPath('/project')),
        ]

    def test_host_paths_map_into_the_box_and_back(self):
        inside = box.to_box(str(self.root / 'project/out/a.png'), self.pairs)
        self.assertEqual(inside, '/project/out/a.png')
        self.assertEqual(box.to_host(inside, self.pairs), str(self.root / 'project/out/a.png'))
        self.assertEqual(
            box.to_box('out.png', self.pairs, cwd=self.root / 'project'), '/project/out.png'
        )

    def test_unshared_paths_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'not shared with the environment'):
            box.to_box('/etc/passwd', self.pairs)

    def test_json_output_lines_are_translated(self):
        line = json.dumps({'path': '/ca/environments/web/output/x.png', 'text': '/project'}) + '\n'
        value = json.loads(box.translate_line(line, self.pairs))
        self.assertEqual(value['path'], str(self.root / 'environments/web/output/x.png'))
        self.assertEqual(value['text'], str(self.root / 'project'))
        self.assertEqual(box.translate_line('"/elsewhere"', self.pairs), '"/elsewhere"')

    def test_only_path_arguments_are_rewritten(self):
        target = str(self.root / 'project/shot.png')
        raw = ['--environment', 'web', 'capture', '--window', 'app', target]
        args = parser().parse_known_args(raw)[0]
        self.assertEqual(box.translate_args(raw, args, self.pairs)[-1], '/project/shot.png')
        raw = ['--environment', 'web', 'type', '--window', 'app', target]
        args = parser().parse_known_args(raw)[0]
        self.assertEqual(box.translate_args(raw, args, self.pairs), raw)

    def test_stopped_box_is_an_error_not_a_fallback(self):
        with (
            patch.object(box, 'stored', return_value=box.DEFAULTS),
            patch.object(box, 'engine_for', return_value=box.Runtime('docker')),
            patch.object(box, 'running', return_value=False),
            self.assertRaisesRegex(ValueError, 'is not running; use ca env start web'),
        ):
            box.proxy('web', ['--environment', 'web', 'windows'], None)


if __name__ == '__main__':
    unittest.main()
