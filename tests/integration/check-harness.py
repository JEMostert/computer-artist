#!/usr/bin/env python3
"""End-to-end Window layouts and fragment checks against the separate packaged KWin fixture."""

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

root = Path(__file__).resolve().parents[2]
session = json.loads(Path(sys.argv[1]).read_text())
assert session['compositor'] == '/usr/bin/kwin_wayland'
assert '/ca-stock-' in session['wayland'] and session['wayland'].endswith('/wayland-test')
out = Path(session['output'])
env = dict(
    os.environ,
    CA_SOCKET=session['control'],
    CA_WINDOW_DIR=str(out / 'window'),
    CA_OUTPUT_DIR=str(out / 'output'),
)


def ca(*args, source=None, success=True):
    result = subprocess.run(
        [str(root / 'ca'), *args], input=source, text=True, capture_output=True, env=env, timeout=20
    )
    if success and result.returncode:
        raise AssertionError(result.stderr + '\n' + result.stdout)
    if not success:
        assert result.returncode != 0, result.stdout
    return json.loads(result.stdout or result.stderr)


def release_count():
    return sum(
        json.loads(line)['event'] == 'release'
        for line in (out / 'agent-events.jsonl').read_text().splitlines()
    )


window_id = next(w['id'] for w in ca('windows')['windows'] if w['title'] == 'Agent canvas')
identity = 'agent-canvas'
report = {}
before_name = ca('observe', '--window', window_id)['observation']
ca(
    'target',
    window_id,
    'blank',
    '--observation',
    before_name['id'],
    '--rect',
    '40',
    '40',
    '30',
    '30',
)
named = ca('set', '--name', identity, '--title', 'Agent canvas')
assert named['window'] == window_id
assert named['title'] == 'Agent canvas'
assert (out / 'window' / 'layout' / identity / 'targets' / 'blank.json').is_file()
assert not (out / 'window' / 'layout' / window_id).exists()
assert next(w['name'] for w in ca('windows')['windows'] if w['id'] == window_id) == identity
after_name = ca('observe', '--window', identity, '--since', before_name['id'])['observation']
assert after_name['window']['id'] == window_id
assert identity in Path(after_name['full_image']).parts
assert ca('capture', '--window', identity, str(out / 'named-capture.png'))['ok']
report['named_window_resolves_observation_and_legacy_capture'] = True
assert not ca('session', 'status')['session']
doctor = ca('doctor', '--window', identity, '--build')
assert doctor['ok'] and doctor['selected_window']['id'] == window_id, doctor
assert doctor['backend']['keyboard'] is True and doctor['backend']['host_xwayland'] is False
assert not ca('session', 'status')['session']
report['doctor_reports_native_restrictions_without_opening_session'] = True
watched = subprocess.run(
    [
        str(root / 'ca'),
        'watch',
        '--window',
        identity,
        '--duration',
        '.3',
        '--interval',
        '.1',
        '--changes-only',
    ],
    text=True,
    capture_output=True,
    env=env,
    timeout=10,
)
assert watched.returncode == 0, watched.stderr
stream = [json.loads(line) for line in watched.stdout.splitlines()]
assert stream[0]['event'] == 'observation' and stream[-1]['event'] == 'watch_finished', stream
assert stream[-1]['frames'] >= 2 and stream[-1]['emitted'] == 1, stream
assert not ca('session', 'status')['session']
report['watch_changes_only_streams_observation_and_completion'] = True
# Run long enough to exceed the retained-frame limit, then cancel through SIGTERM.
watcher = subprocess.Popen(
    [
        str(root / 'ca'),
        'watch',
        '--window',
        identity,
        '--duration',
        '30',
        '--deadline',
        '40',
        '--interval',
        '.05',
    ],
    text=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    env=env,
    bufsize=1,
)
watch_events = []
try:
    end = time.monotonic() + 15
    while len(watch_events) < 18:
        assert time.monotonic() < end, 'watch stopped producing frames'
        if select.select([watcher.stdout], [], [], 0.2)[0]:
            line = watcher.stdout.readline()
            assert line, 'watch exited before retention test'
            watch_events.append(json.loads(line))
    cancelled = time.monotonic()
    watcher.terminate()
    remaining, errors = watcher.communicate(timeout=3)
    assert time.monotonic() - cancelled < 3
    watch_events.extend(json.loads(line) for line in remaining.splitlines())
    finished = watch_events[-1]
    assert (
        watcher.returncode == 130
        and finished['event'] == 'watch_finished'
        and finished['status'] == 'interrupted'
    ), (finished, errors)
    watch_folder = out / 'output' / finished['run_id']
    assert (
        len(list((watch_folder / 'captures' / identity).glob('*.json')))
        <= finished['retained_frame_limit']
    )
    assert not ca('session', 'status')['session']
    report['watch_cancellation_retention_and_closed_session'] = True
finally:
    if watcher.poll() is None:
        watcher.kill()
        watcher.communicate()
source = """CONTRACT = {"requires": ["move", "button", "capture"], "parameters": {"y": {"min": 0.1, "max": 0.9, "unit": "fraction"}}}
def run(ctx, y: float):
    before = ctx.observe()
    ctx.path([(0.6+i/1000, y) for i in range(100)], relative=True, interval=.002)
    changed = ctx.wait_for({"drawing_changed": {"changed_since": before["id"]}}, timeout=2)
    return {"before": before["id"], "after": changed["observation"]["id"], "condition": changed["matches"]}
def verify(ctx, result):
    return {"check": "canvas pixels changed after stroke", "passed": result["condition"] == "drawing_changed", "evidence": result}
"""
created = ca('fragments', 'create', 'line', source=source)['result']
assert ca('fragments', 'list')['result'][0]['parameters']['y']['type'] == 'float'
ca('fragments', 'create', 'line', source=source, success=False)
report['stdin_registration_and_no_overwrite'] = True
try:
    first = ca('fragments', 'run', 'line', '--window', identity, '--y', '.78')
    run_folder = out / 'output' / first['run_id']
    assert (run_folder / 'trace.json').is_file()
    assert (run_folder / 'result.json').is_file()
    assert list((run_folder / 'captures' / identity).glob('*.png'))
    assert not list((out / 'window').rglob('*.png'))
    assert (out / 'window' / 'api-fragmants' / 'line' / 'module.py').is_file()
    assert (out / 'window' / 'layout' / identity / 'window.json').is_file()
    report['layout_fragments_and_run_output_separated'] = True
    assert first['status'] == 'verified', first
    second = ca('fragments', 'run', 'line', '--window', identity, '--args', '{"y":0.85}')
    assert second['status'] == 'verified', second
    report['reused_module_with_typed_inputs_and_pixel_verification'] = True
    assert ca('session', 'status')['session']
    assert not any(w['agent'] for w in ca('windows')['windows'])
    report['returns_app_preserves_session'] = True
    invalid = ca('fragments', 'run', 'line', '--window', identity, '--y', '2', success=False)
    assert 'range' in invalid['error']
    ca(
        'fragments',
        'create',
        'nested',
        source='def run(ctx):\n    return ctx.fragments.call("line", y=.7)',
    )
    nested = ca('fragments', 'run', 'nested', '--window', identity)
    assert len(nested['modules']) == 2, nested
    assert (
        nested['status'] == 'returned_unverified' and nested['modules'][1]['status'] == 'verified'
    ), nested
    report['child_checks_do_not_verify_unchecked_parent'] = True
    assert 'trace' not in nested
    stored = ca('runs', 'show', nested['run_id'])['result']
    assert stored['trace'] and stored['modules'][1]['arguments'] == {'y': 0.7}
    assert ca('runs', 'list', '--window', identity)['result']
    report['compact_feedback_and_full_record_inspection'] = True
    report['nested_calls'] = True
    observed = ca('observe', '--window', identity)['observation']
    ca(
        'target',
        identity,
        'blank',
        '--observation',
        observed['id'],
        '--rect',
        '40',
        '40',
        '30',
        '30',
    )
    inline = ca(
        'execute',
        '--window',
        identity,
        source='def run(ctx):\n    ctx.move(target="@blank")\n    return ctx.window["id"]',
    )
    assert inline['status'] == 'returned_unverified'
    assert inline['result'] == window_id
    target_path = out / 'window' / 'layout' / identity / 'targets' / 'blank.json'
    target_record = json.loads(target_path.read_text())
    target_record.pop('window_id')
    target_path.write_text(json.dumps(target_record))
    rejected = ca(
        'execute',
        '--window',
        identity,
        source='def run(ctx):\n    ctx.move(target="@blank")',
        success=False,
    )
    assert rejected['status'] == 'interrupted' and 'window identity' in rejected['error'], rejected
    fresh = ca('observe', '--window', identity)['observation']
    adopted = ca('target', identity, 'blank', '--observation', fresh['id'], '--revalidate')[
        'target'
    ]
    assert adopted['window_id'] == window_id, adopted
    ca('execute', '--window', identity, source='def run(ctx):\n    ctx.move(target="@blank")')
    report['legacy_target_requires_explicit_checked_revalidation'] = True
    report['named_target_and_inline_execution'] = True
    feedback = ca(
        'execute',
        '--window',
        identity,
        source='def run(ctx):\n    return ctx.path([(0.3,.65),(0.4,.65),(0.5,.65)],relative=True,until=lambda observation: True,observe_every=1)',
    )
    assert feedback['result']['status'] == 'condition_observed'
    assert feedback['result']['points'] == 1
    report['feedback_controlled_gesture'] = True
    ca('session', 'close')
    count = release_count()
    invalid = ca(
        'draw', '--window', identity, '--path', 'M30 300 L50 300 M60 300 L999 300', success=False
    )
    assert invalid.get('status') == 'failed' or invalid.get('ok') is False, invalid
    assert release_count() == count and not ca('session', 'status')['session']
    report['svg_invalid_later_stroke_has_no_input_or_session'] = True
    events_before = len((out / 'agent-events.jsonl').read_text().splitlines())
    drawn = ca(
        'draw',
        '--window',
        identity,
        '--path',
        'M30 300 C70 240 110 360 150 300 M180 300 A30 20 0 0 1 240 300',
        '--interval',
        '.002',
        '--verify-change',
    )
    assert drawn['status'] == 'verified' and drawn['result']['strokes'] == 2, drawn
    ca('capture', '--window', identity, str(out / 'svg-shape.png'))
    (out / 'svg-canvas.png').write_bytes((out / 'canvas.png').read_bytes())
    new_events = [
        json.loads(line)
        for line in (out / 'agent-events.jsonl').read_text().splitlines()[events_before:]
    ]
    presses = [e for e in new_events if e['event'] == 'press']
    assert len(presses) == 2 and len([e for e in new_events if e['event'] == 'release']) == 2, (
        new_events
    )
    # Independent analytic points, not samples returned by the path implementation.
    points = []
    for t in (0.1, 0.25, 0.5, 0.75, 0.9):
        points.append((30 + 120 * t, 300 - 180 * (1 - t) ** 2 * t + 180 * (1 - t) * t * t))
    points.extend(((180, 300), (210, 280), (240, 300)))
    offset = (30 - presses[0]['x'], 300 - presses[0]['y'])

    def painted(image, x, y):
        x, y = round(x), round(y)
        return any(
            image.getpixel((px, py))[2] > image.getpixel((px, py))[0] + 40
            for px in range(x - 3, x + 4)
            for py in range(y - 3, y + 4)
        )

    for name, delta in (('svg-shape.png', (0, 0)), ('svg-canvas.png', offset)):
        with Image.open(out / name).convert('RGB') as image:
            assert all(painted(image, x - delta[0], y - delta[1]) for x, y in points), (
                name,
                points,
                offset,
            )
            assert not painted(image, 165 - delta[0], 300 - delta[1]), (
                'SVG subpaths were connected while pen was down'
            )
    (out / 'svg-shape-check.json').write_text(
        json.dumps(
            {
                'analytic_points': points,
                'canvas_offset': offset,
                'gap_point': [165, 300],
                'capture': 'svg-shape.png',
                'saved_canvas': 'svg-canvas.png',
                'passed': True,
            },
            indent=2,
        )
        + '\n'
    )
    report['svg_cubic_arc_shape_and_pen_up_verified_in_capture_and_saved_canvas'] = True
    # An infinite loop while holding a drag must not outlive the supervisor deadline.
    ca(
        'fragments',
        'create',
        'stall',
        source='def run(ctx):\n    ctx.path([(0.2+i/10000,.8) for i in range(500)],relative=True,interval=.1)',
    )
    count = release_count()
    timed = ca('fragments', 'run', 'stall', '--window', identity, '--deadline', '1', success=False)
    assert timed['status'] == 'interrupted', timed
    assert timed['partial_evidence'] and not timed['final_outcome_known'], timed
    interrupted_record = ca('runs', 'inspect', timed['run_id'])['result']
    assert interrupted_record['trace'] and any(
        event['operation'] == 'button' for event in interrupted_record['trace']
    ), interrupted_record
    report['hard_deadline_keeps_partial_input_evidence'] = True
    end = time.monotonic() + 3
    while release_count() <= count and time.monotonic() < end:
        time.sleep(0.05)
    assert release_count() > count
    assert not any(w['agent'] for w in ca('windows')['windows'])
    report['hard_deadline_releases_held_drag'] = True
    ca('fragments', 'update', 'line', source=source + '\n# revision 2\n')
    assert len(ca('fragments', 'history', 'line')['result']) == 2
    assert (
        ca('fragments', 'show', 'line', '--version', created['version'])['result']['manifest'][
            'version'
        ]
        == created['version']
    )
    restored = ca('fragments', 'restore', 'line', '--version', created['version'])['result']
    assert (
        restored['version'] == created['version']
        and len(ca('fragments', 'history', 'line')['result']) == 2
    ), restored
    report['validated_revision_restore_preserves_history'] = True
    report['shared_fragment_version_history'] = True
finally:
    ca('session', 'close')
report.update(passed=True, compositor='/usr/bin/kwin_wayland', window=window_id, name=identity)
(out / 'harness-regression.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2))
