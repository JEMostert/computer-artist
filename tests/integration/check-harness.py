#!/usr/bin/env python3
"""End-to-end Window layouts and API fragments checks against the separate packaged KWin fixture."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

root=Path(__file__).resolve().parents[2]
session=json.loads(Path(sys.argv[1]).read_text())
assert session['compositor']=='/usr/bin/kwin_wayland'
assert '/ca-stock-' in session['wayland'] and session['wayland'].endswith('/wayland-test')
out=Path(session['output'])
env=dict(os.environ,CA_SOCKET=session['control'],CA_WINDOW_DIR=str(out/'window'),CA_OUTPUT_DIR=str(out/'output'))

def ca(*args,source=None,success=True):
    result=subprocess.run([str(root/'ca'),*args],input=source,text=True,capture_output=True,env=env,timeout=20)
    if success and result.returncode:raise AssertionError(result.stderr+'\n'+result.stdout)
    if not success:assert result.returncode!=0,result.stdout
    return json.loads(result.stdout or result.stderr)

def release_count():
    return sum(json.loads(line)['event']=='release' for line in (out/'agent-events.jsonl').read_text().splitlines())

window_id=next(w['id'] for w in ca('windows')['windows'] if w['title']=='Agent canvas')
identity='agent-canvas'
report={}
before_name=ca('observe','--window',window_id)['observation']
ca('target',window_id,'blank','--observation',before_name['id'],'--rect','40','40','30','30')
named=ca('set',window_id,'--name',identity)
assert named['window']==window_id
assert (out/'window'/'layout'/identity/'targets'/'blank.json').is_file()
assert not (out/'window'/'layout'/window_id).exists()
assert next(w['name'] for w in ca('windows')['windows'] if w['id']==window_id)==identity
after_name=ca('observe','--window',identity,'--since',before_name['id'])['observation']
assert after_name['window']['id']==window_id
assert identity in Path(after_name['full_image']).parts
assert ca('capture','--window',identity,str(out/'named-capture.png'))['ok']
report['named_window_resolves_observation_and_legacy_capture']=True
source='''CONTRACT = {"requires": ["move", "button", "capture"], "parameters": {"y": {"min": 0.1, "max": 0.9, "unit": "fraction"}}}
def run(ctx, y: float):
    before = ctx.observe()
    ctx.path([(0.6+i/1000, y) for i in range(100)], relative=True, interval=.002)
    changed = ctx.wait_for({"drawing_changed": {"changed_since": before["id"]}}, timeout=2)
    return {"before": before["id"], "after": changed["observation"]["id"], "condition": changed["matches"]}
def verify(ctx, result):
    return {"check": "canvas pixels changed after stroke", "passed": result["condition"] == "drawing_changed", "evidence": result}
'''
created=ca('fragments','create','line',source=source)['result']
assert ca('fragments','list')['result'][0]['parameters']['y']['type']=='float'
ca('fragments','create','line',source=source,success=False)
report['stdin_registration_and_no_overwrite']=True
try:
    first=ca('fragments','run','line','--window',identity,'--y','.78')
    run_folder=out/'output'/first['run_id']
    assert (run_folder/'trace.json').is_file()
    assert (run_folder/'result.json').is_file()
    assert list((run_folder/'captures'/identity).glob('*.png'))
    assert not list((out/'window').rglob('*.png'))
    assert (out/'window'/'api-fragmants'/'line'/'module.py').is_file()
    assert (out/'window'/'layout'/identity/'window.json').is_file()
    report['layout_fragments_and_run_output_separated']=True
    assert first['status']=='verified',first
    second=ca('fragments','run','line','--window',identity,'--args','{"y":0.85}')
    assert second['status']=='verified',second
    report['reused_module_with_typed_inputs_and_pixel_verification']=True
    assert ca('session','status')['session']
    assert not any(w['agent'] for w in ca('windows')['windows'])
    report['returns_app_preserves_session']=True
    invalid=ca('fragments','run','line','--window',identity,'--y','2',success=False)
    assert 'range' in invalid['error']
    ca('fragments','create','nested',source='def run(ctx):\n    return ctx.fragments.call("line", y=.7)')
    nested=ca('fragments','run','nested','--window',identity)
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
    assert inline['result']==window_id
    report['named_target_and_inline_execution']=True
    feedback=ca('execute','--window',identity,source='def run(ctx):\n    return ctx.path([(0.3,.65),(0.4,.65),(0.5,.65)],relative=True,until=lambda observation: True,observe_every=1)')
    assert feedback['result']['status']=='condition_observed'
    assert feedback['result']['points']==1
    report['feedback_controlled_gesture']=True
    # An infinite loop while holding a drag must not outlive the supervisor deadline.
    ca('fragments','create','stall',source='def run(ctx):\n    ctx.path([(0.2+i/10000,.8) for i in range(500)],relative=True,interval=.1)')
    count=release_count()
    timed=ca('fragments','run','stall','--window',identity,'--deadline','1',success=False)
    assert timed['status']=='interrupted',timed
    end=time.monotonic()+3
    while release_count()<=count and time.monotonic()<end:time.sleep(.05)
    assert release_count()>count
    assert not any(w['agent'] for w in ca('windows')['windows'])
    report['hard_deadline_releases_held_drag']=True
    ca('fragments','update','line',source=source+'\n# revision 2\n')
    assert len(ca('fragments','history','line')['result'])==2
    assert ca('fragments','show','line','--version',created['version'])['result']['manifest']['version']==created['version']
    report['shared_fragment_version_history']=True
finally:
    ca('session','close')
report.update(passed=True,compositor='/usr/bin/kwin_wayland',window=window_id,name=identity)
(out/'harness-regression.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
