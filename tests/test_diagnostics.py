import hashlib
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from computer_artist.config import default_output_dir, default_window_dir
from computer_artist.diagnostics import diagnose, plugin_source_hash


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _write_plugin(root, files):
    directory = root / 'plugin'
    directory.mkdir(parents=True)
    for name, data in files.items():
        (directory / name).write_bytes(data)
    return directory


def _plugin_report(root, build, checkout=None):
    args = Namespace(
        window_dir=root / 'window',
        output_dir=root / 'output',
        socket=str(root / 'control'),
        deadline=2,
        window=None,
        build=False,
    )
    backend = {'protocol': 3, 'operations': ['capture']}
    if build is not None:
        backend['build'] = build
    client = MagicMock()
    client.__enter__.return_value = client
    client.request.side_effect = [backend, {'windows': []}, {'clipboard': True}]
    with (
        patch('computer_artist.diagnostics.Client', return_value=client),
        patch('computer_artist.diagnostics.checkout_root', return_value=checkout),
    ):
        return diagnose(args)


def _plugin_check(report):
    return next(c for c in report['checks'] if c['name'] == 'plugin_build')


class DiagnosticsTest(unittest.TestCase):
    def test_missing_backend_is_actionable_and_creates_no_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = Namespace(
                window_dir=root / 'window',
                output_dir=root / 'output',
                socket=str(root / 'missing'),
                deadline=2,
                window=None,
                build=False,
            )
            report = diagnose(args)
            self.assertFalse(report['ok'])
            check = next(c for c in report['checks'] if c['name'] == 'compositor')
            self.assertIn('remedy', check)
            self.assertFalse((root / 'window').exists())
            self.assertFalse((root / 'output').exists())

    def test_installed_defaults_and_explicit_window_root(self):
        with (
            patch('computer_artist.config.checkout_root', return_value=None),
            patch.dict(
                'os.environ',
                {
                    'XDG_DATA_HOME': '/tmp/ca-data',
                    'XDG_STATE_HOME': '/tmp/ca-state',
                    'CA_WINDOW_DIR': '',
                    'CA_OUTPUT_DIR': '',
                },
            ),
        ):
            self.assertEqual(default_window_dir(), Path('/tmp/ca-data/computer-artist/window'))
            self.assertEqual(
                default_output_dir(default_window_dir()),
                Path('/tmp/ca-state/computer-artist/output'),
            )
            self.assertEqual(
                default_output_dir('/tmp/personal/window'), Path('/tmp/personal/output')
            )

    def test_connection_focus_restrictions_are_reported_for_inactive_sibling(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = Namespace(
                window_dir=root / 'window',
                output_dir=root / 'output',
                socket=str(root / 'control'),
                deadline=2,
                window='sibling',
                build=False,
            )
            client = MagicMock()
            client.__enter__.return_value = client
            client.request.side_effect = [
                {'protocol': 3, 'operations': ['capture']},
                {
                    'windows': [
                        {
                            'id': 'sibling',
                            'title': 'Inactive document',
                            'native': True,
                            'visible': True,
                            'human_active': False,
                            'acquirable': False,
                            'agent_restrictions': [
                                'human_pointer_in_application',
                                'human_keyboard_in_application',
                            ],
                            'host_acquirable': True,
                            'host_restrictions': [],
                        }
                    ]
                },
                {'clipboard': True},
            ]
            with patch('computer_artist.diagnostics.Client', return_value=client):
                report = diagnose(args)
            selected = next(c for c in report['checks'] if c['name'] == 'selected_window')
            self.assertFalse(selected['passed'])
            window = report['windows'][0]
            self.assertFalse(window['agent_candidate'])
            self.assertTrue(window['host_candidate'])
            self.assertIn('human_pointer_in_application', window['restriction_codes'])
            self.assertFalse((root / 'window').exists())

    def test_private_environment_judges_the_host_lane_it_will_use(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = Namespace(
                window_dir=root / 'window',
                output_dir=root / 'output',
                socket=str(root / 'control'),
                deadline=2,
                window='app',
                build=False,
                lane='host',
                environment_info={'name': 'web', 'kind': 'container', 'lane': 'host'},
            )
            client = MagicMock()
            client.__enter__.return_value = client
            client.request.side_effect = [
                {
                    'protocol': 3,
                    'operations': ['capture'],
                    'environment': 'web',
                    'build': {
                        'source_sha256': 'loaded',
                        'kwin_headers': '6.4.0',
                        'kwin_running': '6.4.0',
                    },
                },
                {
                    'windows': [
                        {
                            'id': 'app',
                            'title': 'App',
                            'native': True,
                            'visible': True,
                            'human_active': True,
                            'acquirable': False,
                            'agent_restrictions': ['human_keyboard_in_application'],
                            'host_acquirable': True,
                            'host_restrictions': [],
                        }
                    ]
                },
                {'clipboard': True},
            ]
            with (
                patch('computer_artist.diagnostics.Client', return_value=client),
                patch('computer_artist.diagnostics.checkout_root', return_value=None),
            ):
                report = diagnose(args)
            self.assertTrue(report['ok'])
            self.assertEqual((report['lane'], report['environment']['name']), ('host', 'web'))

    def test_plugin_source_hash_follows_the_build_manifest_rules(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(plugin_source_hash(root))
            directory = _write_plugin(
                root,
                {
                    'CMakeLists.txt': b'y',
                    'a.cpp': b'x',
                    'b.h': b'z',
                    'metadata.json': b'{}',
                    'notes.txt': b'ignored',
                },
            )
            (directory / 'nested').mkdir()
            (directory / 'nested' / 'c.cpp').write_bytes(b'ignored')
            # Name order, one line per tracked file; untracked files and subfolders are excluded.
            manifest = (
                f'CMakeLists.txt:{_sha(b"y")}\n'
                f'a.cpp:{_sha(b"x")}\n'
                f'b.h:{_sha(b"z")}\n'
                f'metadata.json:{_sha(b"{}")}\n'
            )
            self.assertEqual(plugin_source_hash(root), _sha(manifest.encode()))

    def test_plugin_build_matches_checkout_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write_plugin(root, {'CMakeLists.txt': b'y', 'a.cpp': b'x'})
            checkout = plugin_source_hash(root)
            report = _plugin_report(
                root,
                {'source_sha256': checkout, 'kwin_headers': '6.4.0', 'kwin_running': '6.4.0'},
                checkout=root,
            )
            check = _plugin_check(report)
            self.assertTrue(check['passed'])
            self.assertEqual(check['detail'], {'loaded': checkout, 'checkout': checkout})
            self.assertNotIn('remedy', check)
            self.assertFalse([w for w in report['warnings'] if w['code'] == 'plugin_kwin_mismatch'])

    def test_plugin_build_detects_a_stale_loaded_plugin(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write_plugin(root, {'CMakeLists.txt': b'y', 'a.cpp': b'x'})
            checkout = plugin_source_hash(root)
            report = _plugin_report(
                root,
                {'source_sha256': 'old', 'kwin_headers': '6.4.0', 'kwin_running': '6.4.0'},
                checkout=root,
            )
            self.assertFalse(report['ok'])
            check = _plugin_check(report)
            self.assertFalse(check['passed'])
            self.assertEqual(check['detail'], {'loaded': 'old', 'checkout': checkout})
            self.assertIn('different sources', check['remedy'])
            self.assertIn('never overwrite a loaded library', check['remedy'])

    def test_plugin_build_without_checkout_sources_is_skipped_not_failed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = _plugin_report(
                root,
                {'source_sha256': 'loaded', 'kwin_headers': '6.4.0', 'kwin_running': '6.4.0'},
                checkout=None,
            )
            check = _plugin_check(report)
            self.assertTrue(check['passed'])
            self.assertIsNone(check['detail']['checkout'])
            self.assertIn('skipped', check['detail']['note'])

    def test_loaded_plugin_without_build_identity_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write_plugin(root, {'CMakeLists.txt': b'y'})
            report = _plugin_report(root, None, checkout=root)
            self.assertFalse(report['ok'])
            check = _plugin_check(report)
            self.assertFalse(check['passed'])
            self.assertEqual(check['detail'], 'Loaded plugin predates build identity')
            self.assertIn('./scripts/build-plugin.sh', check['remedy'])
            self.assertFalse([w for w in report['warnings'] if w['code'] == 'plugin_kwin_mismatch'])

    def test_plugin_built_for_another_kwin_is_a_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            _write_plugin(root, {'CMakeLists.txt': b'y'})
            checkout = plugin_source_hash(root)
            report = _plugin_report(
                root,
                {'source_sha256': checkout, 'kwin_headers': '6.3.4', 'kwin_running': '6.4.0'},
                checkout=root,
            )
            self.assertTrue(_plugin_check(report)['passed'])
            self.assertEqual(
                report['warnings'],
                [
                    {
                        'code': 'plugin_kwin_mismatch',
                        'built_for': '6.3.4',
                        'running': '6.4.0',
                        'remedy': 'Rebuild the plugin against the running KWin and reload it.',
                    }
                ],
            )
