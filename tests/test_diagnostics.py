import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

from computer_artist.config import default_output_dir, default_window_dir
from computer_artist.diagnostics import diagnose


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
