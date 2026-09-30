import tempfile
import time
import unittest
from pathlib import Path

from PIL import ImageDraw

from computer_artist.errors import Interrupted
from computer_artist.runtime import Context
from computer_artist.workspace import Workspace
from tests.support import WindowBackend


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = Workspace(Path(self.tmp.name) / 'window')
        self.backend = WindowBackend()
        self.ctx = Context(self.backend, 'w', self.memory)

    def test_layout_survives_output_rotation_without_storing_images(self):
        first = self.ctx.observe()
        self.ctx.target('button', observation=first['id'], rect=[10, 10, 20, 20])
        for _ in range(5):
            self.ctx.observe()
        self.assertFalse(Path(first['full_image']).exists())
        self.ctx.click(target='@button')
        self.assertEqual(self.backend.actions, [(120, 220, 272)])
        self.assertTrue((self.memory.root / 'layout' / 'w' / 'targets' / 'button.json').exists())
        self.assertEqual(list(self.memory.root.rglob('*.png')), [])
        self.assertEqual(len(list(self.memory.output.glob('*/.ca-run.json'))), 5)

    def test_execution_observations_stay_in_one_run(self):
        from computer_artist.storage import inspect, managed_run

        with managed_run(self.memory.output) as folder:
            store = Workspace(self.memory.root, self.memory.output, folder)
            context = Context(self.backend, 'w', store)
            first = context.observe()
            second = context.observe(since=first['id'])
            self.assertEqual(context.output, folder)
            self.assertTrue(Path(first['full_image']).is_relative_to(folder / 'captures'))
            self.assertTrue(Path(second['full_image']).is_relative_to(folder / 'captures'))
            self.assertEqual(len(inspect(store.output)['runs']), 1)

    def test_named_target_checks_pixels_before_input(self):
        observation = self.ctx.observe()
        self.ctx.target('button', observation=observation['id'], rect=[10, 10, 20, 20])
        self.ctx.click(target='@button')
        self.assertEqual(self.backend.actions, [(120, 220, 272)])
        ImageDraw.Draw(self.backend.image).rectangle((20, 20, 30, 30), fill='black')
        with self.assertRaises(Interrupted):
            self.ctx.click(target='@button')
        self.assertEqual(len(self.backend.actions), 1)

    def test_geometry_change_prevents_input_and_human_takeover_is_sticky(self):
        self.backend.window['width'] = 101
        with self.assertRaises(Interrupted):
            self.ctx.click(relative=(0.5, 0.5))
        self.assertEqual(self.backend.actions, [])
        self.backend.window['width'] = 100
        with self.assertRaises(Interrupted):
            self.ctx.click(relative=(0.5, 0.5))

    def test_one_fragment_uses_each_live_windows_layout(self):
        self.memory.fragments.write('select-tool', "def run(ctx): ctx.click(target='@tool')")
        first = self.ctx.observe()
        self.ctx.target('tool', observation=first['id'], rect=[10, 10, 20, 20])
        other = WindowBackend()
        other.window.update(id='other', x=400, y=300)
        second_context = Context(other, 'other', self.memory)
        second = second_context.observe()
        second_context.target('tool', observation=second['id'], rect=[40, 20, 20, 20])
        self.ctx.fragments.call('select-tool')
        second_context.fragments.call('select-tool')
        self.assertEqual(self.backend.actions, [(120, 220, 272)])
        self.assertEqual(other.actions, [(450, 330, 272)])
        self.assertEqual(len(self.memory.fragments.list()), 1)
        self.assertFalse((self.memory.root / 'api-fragmants' / 'windows').exists())

    def test_nested_modules_share_lease_and_budget_and_verify_named_checks(self):
        self.memory.fragments.write(
            'point', 'def run(ctx, x: float):\n    ctx.click(relative=(x, .5))\n    return x'
        )
        self.memory.fragments.write(
            'pair',
            'def run(ctx):\n    a=ctx.fragments.call("point",x=.2)\n    b=ctx.fragments.call("point",x=.8)\n    return [a,b]\ndef verify(ctx,result):\n    return {"check":"two returned coordinates", "passed":result==[.2,.8], "evidence":result}',
        )
        remaining = self.backend.budget
        self.assertEqual(self.ctx.call('pair', {}), [0.2, 0.8])
        self.assertEqual(self.backend.acquire_count, 1)
        self.assertLess(self.backend.budget, remaining)
        self.assertEqual(self.ctx.events[0]['status'], 'verified')
        self.assertEqual(self.ctx.events[1]['status'], 'returned_unverified')

    def test_nested_fragment_call_can_pin_a_previous_revision(self):
        original = self.memory.fragments.write('value', 'def run(ctx): return 1')
        self.memory.fragments.write('value', 'def run(ctx): return 2', update=True)
        self.assertEqual(self.ctx.fragments.call('value', version=original['version']), 1)
        self.assertEqual(self.ctx.events[0]['version'], original['version'])
        self.assertEqual(self.ctx.fragments.call('value'), 2)

    def test_verification_copies_evidence_and_rejects_empty_names(self):
        evidence = {'files': ['saved.png']}
        self.ctx.verify('saved', True, evidence=evidence)
        evidence['files'].append(object())
        self.assertEqual(self.ctx.checks[0]['evidence'], {'files': ['saved.png']})
        for name in ('', ' ', None, 7):
            with self.assertRaises(ValueError):
                self.ctx.verify(name, True)

    def test_rebound_named_layout_requires_explicit_target_revalidation(self):
        self.memory.assign('w', 'paint', [self.backend.window])
        first = self.ctx.observe()
        self.ctx.target('tool', observation=first['id'], rect=[10, 10, 20, 20])
        other = WindowBackend()
        other.window['id'] = 'other'
        self.memory.assign('other', 'paint', [other.window])
        context = Context(other, 'paint', self.memory)
        with self.assertRaisesRegex(Interrupted, 'revalidate'):
            context.click(target='@tool')
        self.assertEqual(other.actions, [])
        observation = context.observe()
        context.observations.revalidate('paint', 'tool', observation['id'])
        context.click(target='@tool')
        self.assertEqual(other.actions, [(120, 220, 272)])

    def test_recursive_or_unsupported_modules_do_not_dispatch(self):
        self.memory.fragments.write(
            'keyboard', 'CONTRACT={"requires":["focus"]}\ndef run(ctx): ctx.click(relative=(.5,.5))'
        )
        with self.assertRaises(ValueError):
            self.ctx.call('keyboard', {})
        self.memory.fragments.write('recursive', 'def run(ctx): ctx.fragments.call("recursive")')
        with self.assertRaises(ValueError):
            self.ctx.call('recursive', {})
        self.assertEqual(self.backend.actions, [])

    def test_wait_branches_and_verification_failure(self):
        observation = self.ctx.observe()
        ImageDraw.Draw(self.backend.image).point((1, 1), fill='black')
        result = self.ctx.wait_for(
            {'changed': {'changed_since': observation['id']}, 'error': {'title_contains': 'Error'}},
            timeout=1,
        )
        self.assertEqual(result['matches'], 'changed')
        with self.assertRaises(Interrupted):
            self.ctx.verify('export exists', False, evidence={'exists': False})
        with self.assertRaises(Interrupted):
            self.ctx.click(relative=(0.5, 0.5))

    def test_feedback_path_stops_on_condition_and_releases_button(self):
        result = self.ctx.path(
            [(10, 10), (20, 20), (30, 30)], until=lambda observation: True, observe_every=1
        )
        self.assertEqual(result['status'], 'condition_observed')
        self.assertEqual(result['points'], 1)
        self.assertEqual(self.backend.actions[-1], ('button', False))
        self.assertNotIn((130, 230), self.backend.actions)

    def test_host_context_and_host_only_module_require_explicit_lane(self):
        with self.assertRaises(PermissionError):
            self.ctx.host.window('w')
        self.memory.fragments.write('host-only', 'CONTRACT={"lane":"host"}\ndef run(ctx): return 1')
        with self.assertRaises(PermissionError):
            self.ctx.call('host-only', {})
        with self.assertRaises(PermissionError):
            self.ctx.focus()
        self.assertEqual(self.backend.acquire_count, 0)

    def test_invalid_path_validates_before_any_input(self):
        with self.assertRaises(ValueError):
            self.ctx.path([(10, 10), (1000, 1000)])
        self.assertEqual(self.backend.acquire_count, 0)
        with self.assertRaises(ValueError):
            self.ctx.type('hello')
        self.assertEqual(self.backend.acquire_count, 0)

    def test_wait_validates_all_conditions_before_observing(self):
        for bad in ({'unknown': True}, {'stable_for': float('nan')}, {'title_contains': 7}):
            with self.assertRaises(ValueError):
                self.ctx.wait_for({'ready': lambda observation: True, 'bad': bad})
        self.assertIsNone(self.ctx.last_observation)

    def test_feedback_exception_cancels_and_preserves_original_error(self):
        def broken(observation):
            raise ValueError('observer failed')

        def button(pressed=True):
            if not pressed:
                raise RuntimeError('release failed')
            self.backend.actions.append(('button', pressed))

        self.backend.button = button
        with self.assertRaisesRegex(ValueError, 'observer failed'):
            self.ctx.path([(10, 10), (20, 20)], until=broken)
        self.assertEqual(self.backend.actions[-1], ('cancel',))

    def test_verification_refuses_cancelled_shared_execution(self):
        from computer_artist.client import ExecutionBudget

        self.backend.shared = ExecutionBudget(10, 100)
        self.backend.shared.cancelled.set()
        with self.assertRaisesRegex(Interrupted, 'cancelled'):
            self.ctx.verify('finished', True)
        self.assertEqual(self.ctx.checks, [])

    def test_svg_validates_later_stroke_before_any_input(self):
        with self.assertRaises(ValueError):
            self.ctx.svg_path('M 10 10 L 20 20 M 10 10 L 200 200')
        self.assertEqual(self.backend.acquire_count, 0)
        self.assertEqual(self.backend.actions, [])

    def test_svg_checks_entire_duration_before_any_input(self):
        self.backend.expires = time.monotonic() + 0.1
        with self.assertRaises(TimeoutError):
            self.ctx.svg_path('M 10 10 L 20 20', interval=1)
        self.assertEqual(self.backend.acquire_count, 0)

    def test_svg_reserves_outcome_observations_before_drawing(self):
        self.backend.budget = 16
        with self.assertRaises(ValueError):
            self.ctx.svg_path('M10 10 L14 10', interval=0, verify_change=True)
        self.assertEqual(self.backend.acquire_count, 0)
        self.assertIsNone(self.ctx.last_observation)


if __name__ == '__main__':
    unittest.main()
