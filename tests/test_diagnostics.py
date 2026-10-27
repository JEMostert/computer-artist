import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from computer_artist.diagnostics import diagnose, plugin_source_hash


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

    def test_environment_host_lane_reports_host_readiness_first(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            args = Namespace(
                window_dir=root / 'window',
                output_dir=root / 'output',
                socket=str(root / 'control'),
                deadline=2,
                window='kp',
                build=False,
                lane='host',
                environment_info={'name': 'bench', 'kind': 'kwin', 'lane': 'host'},
            )
            client = MagicMock()
            client.__enter__.return_value = client
            client.request.side_effect = [
                {'protocol': 3, 'operations': ['capture'], 'environment': 'bench'},
                {
                    'windows': [
                        {
                            'id': 'kp',
                            'title': 'Untitled - KolourPaint',
                            'native': True,
                            'visible': True,
                            'human_active': True,
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
            self.assertTrue(selected['passed'])
            window = report['windows'][0]
            self.assertEqual((window['lane'], window['ready']), ('host', True))
            self.assertNotIn('restrictions', window)
            self.assertNotIn('agent_candidate', window)
            self.assertFalse(window['agent_lane']['agent_candidate'])
            self.assertIn('human_pointer_in_application', window['agent_lane']['restriction_codes'])

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
