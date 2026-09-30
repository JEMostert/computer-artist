"""Inline and versioned programs use the same preflight and outcome rules."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from computer_artist.worker import main
from computer_artist.workspace import Workspace


class ReadOnlyClient:
    def __init__(self, socket, *, deadline, action_budget, lane='agent', window_dir=None):
        self.lane = lane
        self.expires = time.monotonic() + deadline
        self.budget = action_budget
        self.failure = None
        self.trace = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, operation):
        if operation != 'capabilities':
            raise AssertionError(operation)
        return {'operations': ['capture']}

    def windows(self):
        self.budget -= 1
        return [
            {
                'id': 'w',
                'title': 'Canvas',
                'width': 100,
                'height': 80,
                'x': 0,
                'y': 0,
                'native': True,
                'visible': True,
                'pid': 1,
            }
        ]

    def release(self):
        pass

    def close(self):
        pass


class ProgramsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root / 'window', self.root / 'output')

    def execute(self, source, *, stored):
        folder = self.root / ('stored' if stored else 'inline')
        folder.mkdir(exist_ok=True)
        spec = {
            'socket': 'unused',
            'deadline': 20,
            'budget': 1000,
            'window': 'w',
            'window_root': str(self.workspace.root),
            'output_root': str(self.workspace.output),
        }
        if stored:
            manifest = self.workspace.fragments.write('program', source)
            spec.update(module='program', version=manifest['version'], arguments={})
        else:
            spec['source'] = source
        (folder / 'request.json').write_text(json.dumps(spec))
        with (
            patch('computer_artist.worker.Client', ReadOnlyClient),
            patch(
                'computer_artist.observations.Observations.capture',
                side_effect=ValueError('no image'),
            ),
        ):
            main(folder)
        return json.loads((folder / 'result.json').read_text())

    def assert_parity(self, source, *, status, error_type=None):
        inline = self.execute(source, stored=False)
        stored = self.execute(source, stored=True)
        for result in (inline, stored):
            self.assertEqual(result['status'], status)
            self.assertEqual(result.get('error_type'), error_type)
        self.assertEqual(inline['ok'], stored['ok'])
        self.assertEqual(inline.get('result'), stored.get('result'))
        self.assertEqual(inline['checks'], stored['checks'])
        return inline, stored

    def test_verified_results_and_evidence_match(self):
        self.assert_parity(
            'def run(ctx, count: int = 2): return count\n'
            'def verify(ctx, result):\n'
            '    return {"check": "count", "passed": result == 2, "evidence": result}',
            status='verified',
        )

    def test_preconditions_fail_before_program_runs(self):
        sources = [
            (
                'CONTRACT={"lane":"host"}\ndef run(ctx): raise AssertionError("ran")',
                'failed',
                'PermissionError',
            ),
            (
                'CONTRACT={"requires":["key"]}\ndef run(ctx): raise AssertionError("ran")',
                'failed',
                'ValueError',
            ),
            (
                'CONTRACT={"window":{"min_width":101}}\ndef run(ctx): raise AssertionError("ran")',
                'interrupted',
                'Interrupted',
            ),
        ]
        for source, status, kind in sources:
            with self.subTest(kind=kind):
                # Each case creates the same program name in a fresh workspace.
                with tempfile.TemporaryDirectory() as directory:
                    self.root = Path(directory)
                    self.workspace = Workspace(self.root / 'window', self.root / 'output')
                    self.assert_parity(source, status=status, error_type=kind)

    def test_false_verification_preserves_failed_evidence(self):
        inline, _ = self.assert_parity(
            'def run(ctx): return 2\n'
            'def verify(ctx, result):\n'
            '    return {"check":"saved", "passed":False, "evidence":{"file":"missing"}}',
            status='interrupted',
            error_type='Interrupted',
        )
        self.assertEqual(inline['checks'][0]['evidence'], {'file': 'missing'})

    def test_verifier_requires_explicit_boolean(self):
        self.assert_parity(
            'def run(ctx): return 2\ndef verify(ctx, result): return {"check":"saved", "passed":1}',
            status='failed',
            error_type='ValueError',
        )

    def test_nonfinite_json_results_fail(self):
        self.assert_parity(
            'def run(ctx): return float("nan")', status='failed', error_type='ValueError'
        )

    def test_budget_is_checked_after_run(self):
        self.assert_parity(
            'def run(ctx):\n    ctx.client.budget = 0\n    return "finished"',
            status='interrupted',
            error_type='TimeoutError',
        )

    def test_nested_checks_are_scoped_to_each_invocation(self):
        self.workspace.fragments.write('child', 'def run(ctx): return "child"')
        _, stored = self.assert_parity(
            'def run(ctx):\n'
            '    ctx.verify("parent", True, evidence="before child")\n'
            '    return ctx.fragments.call("child")',
            status='verified',
        )
        self.assertEqual(stored['modules'][0]['status'], 'verified')
        self.assertEqual(stored['modules'][1]['status'], 'returned_unverified')
        self.assertEqual(stored['modules'][1]['checks'], [])

    def test_source_filenames_remain_useful(self):
        source = 'def run(ctx): return run.__code__.co_filename'
        inline = self.execute(source, stored=False)
        stored = self.execute(source, stored=True)
        self.assertEqual(inline['result'], '<ca execute>')
        revision = stored['modules'][0]['version']
        self.assertEqual(
            stored['result'],
            str(self.workspace.fragments.folder('program') / 'versions' / revision / 'module.py'),
        )

    def test_verified_child_does_not_verify_parent_outcome(self):
        self.workspace.fragments.write(
            'child', 'def run(ctx):\n    ctx.verify("child only", True)\n    return "child"'
        )
        inline, stored = self.assert_parity(
            'def run(ctx):\n    ctx.fragments.call("child")\n    return "unrelated parent outcome"',
            status='returned_unverified',
        )
        self.assertEqual(inline['checks'][0]['name'], 'child only')
        self.assertEqual(stored['modules'][0]['checks'], [])
        self.assertEqual(stored['modules'][1]['status'], 'verified')

    def test_bounded_history_preserves_outer_outcome_after_many_nested_calls(self):
        self.workspace.fragments.write('child', 'def run(ctx):\n    ctx.verify("child", True)')
        for parent_check, status in (
            ('', 'returned_unverified'),
            ('    ctx.verify("parent", True)\n', 'verified'),
        ):
            with self.subTest(status=status):
                result = self.execute(
                    'def run(ctx):\n'
                    + parent_check
                    + '    for _ in range(210): ctx.fragments.call("child")',
                    stored=True,
                )
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['status'], status)
                self.assertEqual(len(result['modules']), 200)
                self.assertEqual(len(result['checks']), 200)
                self.assertTrue(all(event['module'] == 'child' for event in result['modules']))
                self.assertEqual(result['evidence_limits']['checks'], 200)
                self.workspace.fragments.remove('program')

    def test_invalid_evidence_still_produces_readable_failure_record(self):
        for evidence in ('object()', 'float("nan")', '{"bad": float("inf")}'):
            with self.subTest(evidence=evidence):
                result = self.execute(
                    'def run(ctx):\n'
                    '    ctx.verify("previous check", True, evidence={"saved": True})\n'
                    f'    ctx.verify("bad evidence", True, evidence={evidence})',
                    stored=False,
                )
                self.assertEqual(result['status'], 'failed')
                self.assertEqual(result['error_type'], 'ValueError')
                self.assertEqual(len(result['checks']), 1)

    def test_failed_module_keeps_its_own_check_evidence(self):
        result = self.execute(
            'def run(ctx):\n    ctx.verify("saved", False, evidence={"exists": False})',
            stored=True,
        )
        self.assertEqual(result['modules'][0]['checks'], result['checks'])

    def test_file_outcome_check_verifies_fresh_exact_export(self):
        artifact = self.root / 'saved.txt'
        source = (
            'from pathlib import Path\n'
            'def run(ctx):\n'
            f'    path = {str(artifact)!r}\n'
            '    before = ctx.snapshot_file(path)\n'
            '    Path(path).write_text("hello 🎨", encoding="utf-8")\n'
            '    ctx.verify_file("saved text", path, kind="text", after=before, '
            'expected_text="hello 🎨", timeout=.06, stable_for=.01, interval=.002)\n'
            '    return "saved"'
        )
        for stored in (False, True):
            with self.subTest(stored=stored):
                artifact.unlink(missing_ok=True)
                result = self.execute(source, stored=stored)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['status'], 'verified')
                self.assertTrue(result['final_outcome_known'])
                self.assertTrue(result['checks'][0]['evidence']['fresh'])

    def test_file_outcome_failure_preserves_reason_and_failed_module_check(self):
        artifact = self.root / 'saved.txt'
        artifact.write_text('wrong content', encoding='utf-8')
        result = self.execute(
            'def run(ctx):\n'
            f'    ctx.verify_file("saved text", {str(artifact)!r}, kind="text", '
            'expected_text="expected", timeout=.06, stable_for=.01, interval=.002)',
            stored=True,
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'interrupted')
        self.assertFalse(result['final_outcome_known'])
        self.assertFalse(result['checks'][0]['passed'])
        self.assertIn('exact expected UTF-8', result['checks'][0]['evidence']['reason'])
        self.assertEqual(result['modules'][0]['checks'], result['checks'])


if __name__ == '__main__':
    unittest.main()
