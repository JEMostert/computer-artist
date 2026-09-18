#!/usr/bin/env python3
"""Pointer separation/lifetime regression for the plugin in STOCK KWin only."""
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
out=Path(s['output']);env=dict(os.environ,DBUS_SESSION_BUS_ADDRESS=s['bus'])

def human(*args):
    subprocess.run([str(root/'build/stock-test-input'),s['wayland'],*map(str,args)],check=True)

def wait(test,timeout=3):
    until=time.monotonic()+timeout
    while time.monotonic()<until:
        if test():return
        time.sleep(.02)
    raise AssertionError('condition did not become true')

def events():
    return [json.loads(line) for line in (out/'agent-events.jsonl').read_text().splitlines()]

def releases():return sum(e['event']=='release' for e in events())

def request(sock,**kwargs):
    sock.sendall((json.dumps(kwargs)+'\n').encode())
    return json.loads(sock.recv(65536))

human('move',200,250);human('click');time.sleep(.2)
report={'compositor':'/usr/bin/kwin_wayland','patched_compositor':False}
with Client(s['control']) as observer:
    assert not observer.request('session_status')['session']
    canvas=next(w for w in observer.windows() if w['title']=='Agent canvas')
    identity=canvas['id'];x=canvas['x']+180;y=canvas['y']+250
    human_id=next(w['id'] for w in observer.windows() if w['human_active'])
    before=(out/'human-text.txt').read_text() if (out/'human-text.txt').exists() else ''
    observer.request('capture',window=identity,path=str(out/'plugin-before.png'))
    owner=Client(s['control'])
    with owner:
        with owner.owned(identity):
            worker=threading.Thread(target=lambda: [human('key',code) for code in (48,46)])
            worker.start()
            owner.path([(x+i,y+40*__import__('math').sin(i/30)) for i in range(200)],interval=.004)
            worker.join()
    wait(lambda: (out/'human-text.txt').read_text()==before+'bc')
    assert next(w['id'] for w in observer.windows() if w['human_active'])==human_id
    assert not any(e['event']=='key' for e in events())
    assert observer.request('session_status')['cursor_visible']
    report['cursor_persists_after_release']=True
    report['simultaneous_pointer_and_human_typing']=True
    observer.request('capture',window=identity,path=str(out/'plugin-after.png'))
    assert (out/'plugin-before.png').read_bytes()!=(out/'plugin-after.png').read_bytes()
    report['rendered_result_changed']=True
    owner=Client(s['control']);owner.acquire(identity);owner.move(x,y);owner.button(pressed=True)
    count=releases();owner.close();wait(lambda:releases()>count)
    report['disconnect_releases_button']=True
    with Client(s['control']) as owner:
        owner.acquire(identity);owner.move(x,y);owner.button(pressed=True)
        count=releases();owner.generation-=1
        try:owner.move(x+1,y)
        except ActionError as error:assert error.reply['error']=='stale_lease_or_geometry'
        else:raise AssertionError('stale action accepted')
        wait(lambda:releases()>count)
    report['stale_action_cancels_drag']=True
    with Client(s['control']) as owner:
        owner.acquire(identity);owner.move(x,y);owner.button(pressed=True)
        count=releases();observer.request('takeover');wait(lambda:releases()>count)
        assert not next(w for w in observer.windows() if w['id']==identity)['agent']
    report['external_takeover']=True
    with Client(s['control']) as owner:
        owner.acquire(identity);owner.move(x,y);owner.button(pressed=True)
        count=releases();human('move',x+80,y)
        wait(lambda:releases()>count)
        assert not next(w for w in observer.windows() if w['id']==identity)['agent']
    report['human_pointer_entry_takeover']=True
    human('move',200,250)
    raw=socket.socket(socket.AF_UNIX);raw.settimeout(2);raw.connect(s['control'])
    lease=request(raw,op='acquire',window=identity)
    assert lease['ok']
    fields={'lease':lease['lease'],'generation':lease['generation']}
    assert request(raw,op='move',x=x,y=y,**fields)['ok']
    assert request(raw,op='button',code=272,pressed=True,**fields)['ok']
    count=releases()
    wait(lambda: not next(w for w in observer.windows() if w['id']==identity)['agent'],timeout=7)
    wait(lambda:releases()>count);raw.close()
    assert observer.request('session_status')['cursor_visible']
    report['watchdog_despite_observer_polling']=True
    with Client(s['control']) as owner:
        owner.acquire(identity);owner.move(x,y);owner.button(pressed=True)
        count=releases()
        subprocess.run(['qdbus6','org.kde.KWin','/Plugins','org.kde.KWin.Plugins.UnloadPlugin','computerartist'],env=env,check=True)
        wait(lambda:releases()>count)
    report['unload_releases_button']=True
result=subprocess.check_output(['qdbus6','org.kde.KWin','/Plugins','org.kde.KWin.Plugins.LoadPlugin','computerartist'],env=env,text=True).strip()
assert result=='true'
with Client(s['control']) as client:
    assert client.request('capabilities')['backend']=='stock_kwin_plugin'
    assert not client.request('session_status')['session']
    client.acquire(identity)
    client.move(x,y)
    client.button(pressed=True)
    count=releases()
    with Client(s['control']) as closer:
        state=closer.request('session_close')
        assert not state['session'] and not state['cursor_visible']
    wait(lambda:releases()>count)
report['explicit_session_close_releases_input_and_hides_cursor']=True
report.update(reload=True,passed=True)
(out/'plugin-regression.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
