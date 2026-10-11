#!/usr/bin/env python3
"""Real native dialog revocation and explicit handoff in separate packaged KWin."""

import json
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from harness import human, script

from computer_artist.client import Client
from computer_artist.supervisor import run_supervised

state = json.loads(Path(sys.argv[1]).read_text())
assert state['compositor'] == '/usr/bin/kwin_wayland'
assert '/ca-stock-' in state['wayland'] and state['wayland'].endswith('/wayland-test')
out = Path(state['output'])
with Client(state['control']) as observer:
    target = next(w for w in observer.windows() if w['title'] == 'Agent canvas')
    typing = next(w for w in observer.windows() if w['title'] == 'Your typing space')
env = dict(os.environ, DBUS_SESSION_BUS_ADDRESS=state['bus'])
script(
    env,
    out,
    'recover-arrange.js',
    'for(const w of workspace.windowList()) if(w.caption==="Your typing space") workspace.activeWindow=w;',
)
human(state, 'move', str(typing['x'] + 100), str(typing['y'] + 200))

spec = {
    'socket': state['control'],
    'window': target['id'],
    'lane': 'agent',
    'deadline': 6,
    'budget': 300,
    'window_root': str(out / 'recovery-window'),
    'output_root': str(out / 'recovery-output'),
    'arguments': {},
}
handoff = run_supervised(
    {
        **spec,
        'source': 'def run(ctx):\n    ctx.key_down("Shift")\n    ctx.handoff("Inspect a known next step")\n',
    }
)
assert handoff['status'] == 'yielded', handoff
assert handoff['interruption']['code'] == 'handoff_requested'
with Client(state['control']) as observer:
    lane = observer.request('session_status')['lanes']['agent']
    assert not lane['busy'] and lane['keys'] == 0

dialog = run_supervised(
    {
        **spec,
        'source': 'def run(ctx):\n'
        '    import json\n'
        '    from pathlib import Path\n'
        '    from computer_artist.errors import Interrupted\n'
        '    ctx.click(x=200,y=200)\n'
        '    ctx.observe()\n'
        + f'    Path({str(out / "agent-open-dialog")!r}).write_text("open")\n'
        + '    try:\n'
        '        ctx.sleep(2)\n'
        '    except Interrupted:\n'
        '        print("Fresh candidates: " + json.dumps(ctx.related_windows()),flush=True)\n'
        '        raise\n',
    }
)
assert not dialog['ok'] and dialog['status'] == 'interrupted', dialog
details = dialog['interruption']['details']
candidates = details['fresh_candidates']
assert any(w['title'] == 'Agent test dialog' and w['pid'] == target['pid'] for w in candidates)
assert dialog['related_observations']
assert 'Fresh candidates:' in Path(dialog['log']).read_text()
assert not dialog['final_outcome_known']
with Client(state['control']) as observer:
    lane = observer.request('session_status')['lanes']['agent']
    assert not lane['busy'] and lane['keys'] == 0
report = {
    'explicit_handoff_releases_held_modifier': True,
    'native_dialog_revocation_has_fresh_candidate_capture': True,
    'read_only_discovery_survives_revoked_connection': True,
    'interrupted_outcome_is_unconfirmed': True,
    'dialog_activation_prevented': False,
    'handoff_record': handoff['run_id'],
    'dialog_record': dialog['run_id'],
    'passed': True,
}
(out / 'recovery-report.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report), flush=True)
