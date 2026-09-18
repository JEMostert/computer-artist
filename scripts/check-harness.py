#!/usr/bin/env python3
"""End-to-end Computer Memory checks against the separate packaged KWin fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root=Path(__file__).resolve().parents[1]
session=json.loads(Path(sys.argv[1]).read_text())
assert session['compositor']=='/usr/bin/kwin_wayland'
assert '/ca-stock-' in session['wayland'] and session['wayland'].endswith('/wayland-test')
out=Path(session['output'])
env=dict(os.environ,CA_SOCKET=session['control'],CA_MEMORY_DIR=str(out/'computer-memory'))

def ca(*args,source=None,success=True):
    result=subprocess.run([str(root/'ca'),*args],input=source,text=True,capture_output=True,env=env,timeout=20)
    if success and result.returncode:raise AssertionError(result.stderr+'\n'+result.stdout)
    if not success:assert result.returncode!=0,result.stdout
    return json.loads(result.stdout or result.stderr)

def release_count():
    return sum(json.loads(line)['event']=='release' for line in (out/'agent-events.jsonl').read_text().splitlines())

identity=next(w['id'] for w in ca('windows')['windows'] if w['title']=='Agent canvas')
report={}
source='''CONTRACT = {"requires": ["move", "button", "capture"], "parameters": {"y": {"min": 0.1, "max": 0.9, "unit": "fraction"}}}
def run(ctx, y: float):
    before = ctx.observe()
    ctx.path([(0.6+i/1000, y) for i in range(100)], relative=True, interval=.002)
    changed = ctx.wait_for({"drawing_changed": {"changed_since": before["id"]}}, timeout=2)
    return {"before": before["id"], "after": changed["observation"]["id"], "condition": changed["matches"]}
def verify(ctx, result):
    return {"check": "canvas pixels changed after stroke", "passed": result["condition"] == "drawing_changed", "evidence": result}
'''
created=ca('memory','create',identity,'line',source=source)['result']
assert ca('memory','list',identity)['result'][0]['parameters']['y']['type']=='float'
ca('memory','create',identity,'line',source=source,success=False)
report['stdin_registration_and_no_overwrite']=True
try:
    first=ca('memory','run',identity,'line','--y','.78')
    assert first['status']=='verified',first
    second=ca('memory','run',identity,'line','--args','{"y":0.85}')
    assert second['status']=='verified',second
    report['reused_module_with_typed_inputs_and_pixel_verification']=True
    assert ca('session','status')['session']
    assert not any(w['agent'] for w in ca('windows')['windows'])
    report['returns_app_preserves_session']=True
    invalid=ca('memory','run',identity,'line','--y','2',success=False)
    assert 'range' in invalid['error']
    ca('memory','create',identity,'nested',source='def run(ctx):\n    return ctx.memory.call("line", y=.7)')
    nested=ca('memory','run',identity,'nested')
    assert len(nested['modules'])==2,nested
    assert 'trace' not in nested
    stored=ca('runs','show',nested['run_id'])['result']
    assert stored['trace'] and stored['modules'][1]['arguments']=={'y':.7}
    assert ca('runs','list','--window',identity)['result']
    report['compact_feedback_and_full_record_inspection']=True
    report['nested_calls']=True
    observed=ca('observe','--window',identity)['observation']
    ca('target',identity,'blank','--observation',observed['id'],'--rect','40','40','30','30')
    inline=ca('execute','--window',identity,source='def run(ctx):\n    ctx.move(target="@blank")\n    return ctx.window["id"]')
    assert inline['status']=='returned_unverified'
    assert inline['result']==identity
    report['named_target_and_inline_execution']=True
    feedback=ca('execute','--window',identity,source='def run(ctx):\n    return ctx.path([(0.3,.65),(0.4,.65),(0.5,.65)],relative=True,until=lambda observation: True,observe_every=1)')
    assert feedback['result']['status']=='condition_observed'
    assert feedback['result']['points']==1
    report['feedback_controlled_gesture']=True
    # An infinite loop while holding a drag must not outlive the supervisor deadline.
    ca('memory','create',identity,'stall',source='def run(ctx):\n    ctx.path([(0.2+i/10000,.8) for i in range(500)],relative=True,interval=.1)')
    count=release_count()
    timed=ca('memory','run',identity,'stall','--deadline','1',success=False)
    assert timed['status']=='interrupted',timed
    end=time.monotonic()+3
    while release_count()<=count and time.monotonic()<end:time.sleep(.05)
    assert release_count()>count
    assert not any(w['agent'] for w in ca('windows')['windows'])
    report['hard_deadline_releases_held_drag']=True
    ca('memory','promote',identity,'line','--app','fixture')
    ca('memory','attach','future-window','--from-app','fixture')
    assert ca('memory','list','future-window')['result'][0]['compatibility']=='unverified'
    ca('memory','update',identity,'line',source=source+'\n# revision 2\n')
    assert len(ca('memory','history',identity,'line')['result'])==2
    assert ca('memory','show',identity,'line','--version',created['version'])['result']['manifest']['version']==created['version']
    report['attach_promotion_and_version_history']=True
finally:
    ca('session','close')
report.update(passed=True,compositor='/usr/bin/kwin_wayland',window=identity)
(out/'harness-regression.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
