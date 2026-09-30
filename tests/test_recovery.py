"""Interruption evidence and explicit handoff never resume or escalate input."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_artist.errors import Interrupted, YieldToAgent
from computer_artist.runtime import Context
from computer_artist.worker import interruption_evidence
from computer_artist.workspace import Workspace
from tests.support import WindowBackend


class RecoveryBackend(WindowBackend):
    def __init__(self):
        super().__init__()
        self.extra = []
        self.releases = 0

    def windows(self):
        return super().windows() + [dict(window) for window in self.extra]

    def release(self):
        self.releases += 1
        self.lease = ''
        self.window['agent'] = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = RecoveryBackend()
        self.ctx = Context(self.backend, 'w', Workspace(Path(self.temp.name) / 'window'))

    def candidate(self, identity='dialog', **values):
        return {**self.backend.window, 'id': identity, 'title': 'Save', **values}

    def test_new_window_stops_input_with_exact_candidates_and_prior_geometry(self):
        before = self.ctx.observe()
        self.backend.extra = [self.candidate()]
        with self.assertRaises(Interrupted) as stopped:
            self.ctx.click(relative=(0.5, 0.5))
        info = stopped.exception.as_dict()
        self.assertEqual(info['code'], 'new_app_window')
        self.assertEqual(info['details']['previous_geometry'], [100, 200, 100, 80])
        self.assertEqual(info['details']['last_observation']['id'], before['id'])
        candidate = info['details']['candidates'][0]
        self.assertEqual(candidate['id'], 'dialog')
        self.assertEqual(candidate['relation'], 'same_process_candidate')
        self.assertEqual(self.backend.actions, [])
        self.backend.extra = []
        with self.assertRaises(Interrupted) as repeated:
            self.ctx.click(relative=(0.5, 0.5))
        self.assertIs(repeated.exception, stopped.exception)

    def test_related_discovery_is_fresh_read_only_even_after_interruption(self):
        self.backend.window['width'] = 110
        with self.assertRaises(Interrupted):
            self.ctx.refresh()
        self.backend.extra = [self.candidate('later')]
        self.assertEqual(self.ctx.related_windows()[0]['id'], 'later')
        self.assertEqual(self.backend.acquire_count, 0)
        with self.assertRaises(Interrupted):
            self.ctx.click(relative=(0.5, 0.5))

    def test_geometry_details_preserve_baseline(self):
        self.backend.window.update(width=110, x=120)
        with self.assertRaises(Interrupted) as stopped:
            self.ctx.refresh()
        self.assertEqual(stopped.exception.code, 'geometry_changed')
        self.assertEqual(stopped.exception.details['previous_window']['width'], 100)
        self.assertEqual(stopped.exception.details['current_geometry'], [120, 200, 110, 80])

    def test_unrelated_hidden_and_unknown_pid_windows_are_not_candidates(self):
        self.backend.extra = [self.candidate(pid=2), self.candidate('hidden', visible=False)]
        self.ctx.refresh()
        self.assertEqual(self.ctx.related_windows(), [])
        for pid in (None, 0, -1):
            with self.subTest(pid=pid):
                self.backend.window['pid'] = pid
                self.backend.extra = [self.candidate(pid=pid)]
                context = Context(self.backend, 'w', self.ctx.store)
                self.assertEqual(context.related_windows(), [])
                context.refresh()

    def test_visible_xwayland_candidate_is_reported_without_input_support(self):
        self.backend.extra = [self.candidate(native=False)]
        with self.assertRaises(Interrupted) as stopped:
            self.ctx.refresh()
        self.assertFalse(stopped.exception.details['candidates'][0]['native'])
        self.assertEqual(self.backend.actions, [])

    def test_handoff_releases_lease_then_stops_and_requires_fresh_context(self):
        self.ctx.click(relative=(0.5, 0.5))
        self.backend.extra = [self.candidate()]
        with self.assertRaises(YieldToAgent) as stopped:
            self.ctx.handoff('Please finish this save')
        self.assertEqual(stopped.exception.code, 'handoff_requested')
        self.assertEqual(self.backend.releases, 1)
        self.assertFalse(self.ctx.owned)
        self.assertFalse(self.backend.lease)
        self.assertEqual(stopped.exception.details['candidates'][0]['id'], 'dialog')
        with self.assertRaises(Interrupted):
            self.ctx.click(relative=(0.5, 0.5))
        fresh = Context(self.backend, 'dialog', self.ctx.store)
        self.assertEqual(fresh.window_id, 'dialog')
        self.assertFalse(fresh.owned)

    def test_handoff_still_releases_when_observation_budget_is_exhausted(self):
        self.ctx.click(relative=(0.5, 0.5))
        self.backend.budget = 0
        with self.assertRaises(YieldToAgent) as stopped:
            self.ctx.handoff('Budget ran out')
        self.assertFalse(self.backend.lease)
        self.assertIn('discovery failed', str(stopped.exception))

    def test_handoff_release_failure_is_explicit_and_sticky(self):
        self.ctx.click(relative=(0.5, 0.5))
        with patch.object(self.backend, 'release', side_effect=OSError('offline')):
            with self.assertRaises(YieldToAgent) as stopped:
                self.ctx.handoff('Return control')
        self.assertEqual(stopped.exception.code, 'handoff_release_failed')
        with self.assertRaises(Interrupted):
            self.ctx.click(relative=(0.5, 0.5))

    def test_exception_evidence_is_copied_and_portable(self):
        original = {'geometry': [1, 2, 3, 4]}
        error = Interrupted('Stopped', code='test', details=original)
        original['geometry'].append(object())
        self.assertEqual(error.details['geometry'], [1, 2, 3, 4])
        json.dumps(error.as_dict(), allow_nan=False)

    def test_worker_evidence_uses_fresh_candidates_bounded_to_three_native_windows(self):
        self.backend.extra = [
            self.candidate('xwayland', native=False),
            *(self.candidate(f'dialog-{index}') for index in range(5)),
            self.candidate('other-app', pid=2),
        ]
        result = {'interruption': Interrupted('Stopped').as_dict()}
        spec = {'socket': 'unused'}
        captured = []

        def capture(identity):
            captured.append(identity)
            if identity == 'w':
                raise Interrupted('Window unavailable')
            return {'id': identity + '-image', 'window': {'id': identity}}

        with (
            patch('computer_artist.worker.Client', return_value=self.backend) as client,
            patch('computer_artist.observations.Observations.capture', side_effect=capture),
        ):
            interruption_evidence(self.ctx, spec, result)
        self.assertEqual(captured, ['w', 'dialog-0', 'dialog-1', 'dialog-2'])
        self.assertEqual(len(result['related_observations']), 3)
        self.assertEqual(len(result['interruption']['details']['fresh_candidates']), 6)
        self.assertIn('Window unavailable', result['observation_error'])
        client.assert_called_once_with('unused', deadline=2, action_budget=20)
        self.assertEqual(self.backend.actions, [])


if __name__ == '__main__':
    unittest.main()
