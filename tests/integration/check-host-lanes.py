#!/usr/bin/env python3
"""Host pointer tests only on the explicitly isolated stock KWin display."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time

root=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(root))
from computer_artist import Client,ActionError
s=json.loads(Path(sys.argv[1]).read_text())
assert s['compositor']=='/usr/bin/kwin_wayland'
assert '/ca-stock-' in s['wayland'] and s['wayland'].endswith('/wayland-test')
out=Path(s['output'])
env=dict(os.environ,CA_SOCKET=s['control'],CA_MEMORY_DIR=str(out/'lane-memory'))
report={}

def human(*args):
    subprocess.run([str(root/'build/stock-test-input'),s['wayland'],*map(str,args)],check=True)

def wait(test,timeout=3):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if test():return
        time.sleep(.02)
    raise AssertionError('condition timed out')

def ca(*args,source=None,success=True):
    result=subprocess.run([str(root/'ca'),*args],input=source,text=True,capture_output=True,env=env,timeout=20)
    assert (result.returncode==0)==success,(result.stdout,result.stderr)
    return json.loads(result.stdout or result.stderr)

human('move',200,250);human('click')
with Client(s['control']) as observer:
    windows=observer.windows()
    agent_window=next(w for w in windows if w['title']=='Agent canvas')
    host_window=next(w for w in windows if w['title']=='Your typing space')
    aid,hid=agent_window['id'],host_window['id']
    ax,ay=agent_window['x']+200,agent_window['y']+300
    hx,hy=host_window['x']+180,host_window['y']+300
    assert not observer.request('session_status')['session']
    ca('--host','capabilities');ca('windows');ca('memory','list',hid)
    assert not observer.request('session_status')['session']
    report['read_only_does_not_open_session']=True
    ca('--host','move','--window',hid,'--x','180','--y','300')
    state=observer.request('session_status')
    assert state['session'] and state['host_position']==[hx,hy]
    assert not state['cursor_visible']
    first_session=state['session']
    report['host_auto_session_and_real_pointer_position']=True
    with Client(s['control']) as agent, Client(s['control'],lane='host') as host:
        agent.acquire(aid);host.acquire(hid)
        # Same-app conflicts are rejected in both directions before input.
        with Client(s['control'],lane='host') as other:
            try:other.acquire(aid)
            except ActionError:pass
            else:raise AssertionError('Host controller conflict was accepted')
        host.release()
        try:host.acquire(aid)
        except ActionError:pass
        else:raise AssertionError('Host acquired agent application')
        host.acquire(hid)
        agent.release()
        try:agent.acquire(hid)
        except ActionError:pass
        else:raise AssertionError('Agent acquired host application')
        agent.acquire(aid)
        report['same_connection_conflicts_rejected']=True
        host.focus(hid)
        assert next(w['id'] for w in observer.windows() if w['human_active'])==hid
        host.move(hx,hy)
        host.button(pressed=True)
        agent.move(ax,ay);agent.button(pressed=True)
        for i in range(60):
            host.move(hx+i,hy+10)
            agent.move(ax+i,ay+15)
        state=observer.request('session_status')
        assert state['lanes']['host']['busy'] and state['lanes']['agent']['busy']
        assert state['host_position']==[hx+59,hy+10]
        assert state['lanes']['host']['buttons']==1 and state['lanes']['agent']['buttons']==1
        wait(lambda:any(json.loads(line)['event']=='move' for line in (out/'human-events.jsonl').read_text().splitlines()))
        report['simultaneous_two_lane_drags']=True
        # A different source device must stop only the host automation.
        human('move',hx+30,hy+20)
        wait(lambda:not observer.request('session_status')['lanes']['host']['busy'])
        assert observer.request('session_status')['lanes']['agent']['busy']
        assert observer.request('session_status')['lanes']['host']['buttons']==0
        agent.move(ax+70,ay+15);agent.button(pressed=False)
        report['external_pointer_preempts_host_preserves_agent']=True
    with Client(s['control'],lane='host') as host:
        host.acquire(hid);host.move(hx,hy);host.button(pressed=True)
        human('click')
        wait(lambda:not observer.request('session_status')['lanes']['host']['busy'])
    # If physical press/release got stuck, a fresh acquire would fail here.
    with Client(s['control'],lane='host') as host:
        host.acquire(hid);host.move(hx,hy);host.button(pressed=True)
        human('key',30)
        wait(lambda:not observer.request('session_status')['lanes']['host']['busy'])
    report['external_click_and_keyboard_preempt_host']=True
    with Client(s['control'],lane='host') as host:
        host.acquire(hid);host.move(hx,hy);host.button(pressed=True)
    wait(lambda:not observer.request('session_status')['lanes']['host']['busy'])
    report['host_disconnect_releases_button']=True
    with Client(s['control'],lane='host') as host:
        host.acquire(hid);host.move(hx,hy);host.scroll(30)
    wait(lambda:any(json.loads(line)['event']=='wheel' for line in (out/'human-events.jsonl').read_text().splitlines()))
    report['host_scroll_reaches_application']=True
    # The watchdog must expire even while a different connection polls status.
    raw=socket.socket(socket.AF_UNIX);raw.connect(s['control']);reader=raw.makefile('rb')
    def request(op,**fields):
        raw.sendall((json.dumps(dict(op=op,lane='host',**fields))+'\n').encode())
        return json.loads(reader.readline())
    lease=request('acquire',window=hid);assert lease['ok']
    fields={'lease':lease['lease'],'generation':lease['generation']}
    assert request('move',x=hx,y=hy,**fields)['ok']
    assert request('button',code=272,pressed=True,**fields)['ok']
    wait(lambda:not observer.request('session_status')['lanes']['host']['busy'],timeout=7)
    reader.close();raw.close()
    report['host_watchdog_releases_button']=True
    # Shared runtime starts two supplied programs and keeps separate client leases.
    source=f'''def run(ctx):
    paint=ctx.agent.window({aid!r})
    text=ctx.host.window({hid!r})
    def stroke(c):
        return c.path([(180+i,350) for i in range(60)], interval=.003)
    def moves(c):
        for i in range(60):
            c.move(x=180+i,y=350)
            c.sleep(.003)
        return {{'points':60}}
    with ctx.parallel() as tasks:
        a=tasks.start(stroke,paint)
        b=tasks.start(moves,text)
    return [a.result(),b.result()]
'''
    result=ca('--host','execute','--window',hid,source=source)
    assert result['ok'] and all(v['points']==60 for v in result['result'])
    detail=ca('runs','show',result['run_id'])['result']
    assert {'host','agent'} <= {e['lane'] for e in detail['trace']}
    assert observer.request('session_status')['session']==first_session
    report['parallel_runtime_and_lane_traces']=True
    # A failure in one parallel task must release the sibling's held input.
    failure_source=f"""def run(ctx):
    paint=ctx.agent.window({aid!r})
    text=ctx.host.window({hid!r})
    def fail(c):
        c.sleep(.1)
        raise ValueError('deliberate sibling failure')
    def draw(c):
        c.path([(180+i,400) for i in range(200)],interval=.01)
    with ctx.parallel() as tasks:
        tasks.start(draw,paint)
        tasks.start(fail,text)
"""
    failed=ca('--host','execute','--window',hid,source=failure_source,success=False)
    assert not failed['ok']
    state=observer.request('session_status')
    assert not state['lanes']['host']['busy'] and not state['lanes']['agent']['busy']
    report['parallel_failure_cancels_sibling']=True
    timed=ca('--host','execute','--window',hid,'--deadline','1',source='def run(ctx):\n    ctx.move(x=200,y=300)\n    while True: pass',success=False)
    assert timed['status']=='interrupted'
    wait(lambda:not observer.request('session_status')['lanes']['host']['busy'])
    report['host_worker_hard_deadline']=True
    refused=ca('execute','--window',aid,source=f'def run(ctx): ctx.host.window({hid!r})',success=False)
    assert refused['error_type']=='PermissionError'
    report['no_implicit_host_escalation']=True
    # The agent still cannot prevent modal auto-focus, but explicit host focus
    # can recover and make the native dialog available for agent pointer input.
    (out/'agent-open-dialog').touch()
    wait(lambda:any(w['title']=='Agent test dialog' for w in observer.windows()))
    dialog=next(w for w in observer.windows() if w['title']=='Agent test dialog')
    ca('--host','focus','--window',hid)
    assert next(w['id'] for w in observer.windows() if w['human_active'])==hid
    with Client(s['control']) as agent:
        agent.acquire(dialog['id'])
        # Standard fixture button is centered near the bottom of the dialog.
        agent.click(dialog['x']+dialog['width']/2,dialog['y']+dialog['height']-35)
    wait(lambda:not any(w['id']==dialog['id'] and w['visible'] for w in observer.windows()))
    report['explicit_host_focus_recovers_agent_dialog']=True
    with Client(s['control']) as agent, Client(s['control'],lane='host') as host:
        agent.acquire(aid);host.acquire(hid)
        agent.move(ax,ay);agent.button(pressed=True)
        host.move(hx,hy);host.button(pressed=True)
        closed=observer.request('session_close')
        assert not closed['session'] and not closed['cursor_visible']
        assert not closed['lanes']['host']['busy'] and not closed['lanes']['agent']['busy']
        assert closed['lanes']['host']['buttons']==0 and closed['lanes']['agent']['buttons']==0
    report['session_close_releases_both_lanes']=True
    # Reload while the host lane holds input must deliver a final button release.
    events_path=out/'human-events.jsonl'
    def host_releases():
        return sum(json.loads(line)['event']=='release' for line in events_path.read_text().splitlines())
    count=host_releases()
    with Client(s['control'],lane='host') as host:
        host.acquire(hid);host.move(hx,hy);host.button(pressed=True)
        subprocess.run(['qdbus6','org.kde.KWin','/Plugins','org.kde.KWin.Plugins.UnloadPlugin','computerartist'],
                       env=dict(os.environ,DBUS_SESSION_BUS_ADDRESS=s['bus']),check=True)
        wait(lambda:host_releases()>count)
    assert subprocess.check_output(['qdbus6','org.kde.KWin','/Plugins','org.kde.KWin.Plugins.LoadPlugin','computerartist'],
                      env=dict(os.environ,DBUS_SESSION_BUS_ADDRESS=s['bus']),text=True).strip()=='true'
    with Client(s['control'],lane='host') as host:
        assert not host.request('session_status')['session']
        host.acquire(hid)
        host.move(hx,hy)
        stale=host.generation-1
        host.generation=stale
        try:host.move(hx+1,hy)
        except ActionError:pass
        else:raise AssertionError('Stale host generation accepted')
    with Client(s['control']) as final:
        assert not final.request('session_status')['lanes']['host']['busy']
        final.request('session_close')
    report['host_unload_release_reload_and_stale_generation']=True
report.update(passed=True,compositor='/usr/bin/kwin_wayland',external_input_source='private test fake-input device')
(out/'host-lanes-regression.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
