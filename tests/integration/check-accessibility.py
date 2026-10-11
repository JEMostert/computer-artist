#!/usr/bin/env python3
"""Verify accessibility-located agent input in a private packaged-KWin harness only."""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
from harness import human, script, wait

from computer_artist.client import Client
from computer_artist.runtime import Context
from computer_artist.workspace import Workspace

state = json.loads(Path(sys.argv[1]).read_text())
assert state['compositor'] == '/usr/bin/kwin_wayland'
assert '/ca-stock-' in state['wayland'] and state['wayland'].endswith('/wayland-test')
out = Path(state['output'])
env = dict(
    os.environ,
    WAYLAND_DISPLAY=state['wayland'],
    QT_QPA_PLATFORM='wayland',
    DBUS_SESSION_BUS_ADDRESS=state['bus'],
    QT_LINUX_ACCESSIBILITY_ALWAYS_ON='1',
)
env.pop('DISPLAY', None)
env.pop('QT_PLUGIN_PATH', None)
os.environ['CA_ACCESSIBILITY_BUS'] = state['bus']
report = {}

FIXTURE = """
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QCheckBox, QLineEdit, QPushButton, QVBoxLayout, QWidget

log = Path(sys.argv[1])
app = QApplication(sys.argv)
window = QWidget()
window.setWindowTitle('Accessible form')
layout = QVBoxLayout(window)
save = QPushButton('Save')
save.clicked.connect(lambda: log.open('a').write('save\\n'))
grid = QCheckBox('Enable grid')
grid.toggled.connect(lambda on: log.open('a').write(f'grid {on}\\n'))
name = QLineEdit('hello')
for widget in (save, name, grid):
    layout.addWidget(widget)
window.resize(320, 220)
window.show()
app.exec()
"""


registryd = next(
    (
        p
        for p in ('/usr/lib/at-spi2-registryd', '/usr/libexec/at-spi2-registryd')
        if Path(p).exists()
    ),
    None,
)
assert registryd, 'at-spi2-registryd is required for the accessibility check'
log = out / 'accessible-form.log'
log.write_text('')
fixture = Path(tempfile.mkdtemp(dir=out)) / 'accessible_form.py'
fixture.write_text(FIXTURE)
children = [
    subprocess.Popen([registryd], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
]
time.sleep(0.5)
children.append(
    subprocess.Popen(
        [sys.executable, str(fixture), str(log)],
        env=env,
        stdout=(out / 'accessible-form-app.log').open('w'),
        stderr=subprocess.STDOUT,
    )
)
try:
    with Client(state['control']) as observer:
        wait(lambda: any(w['title'] == 'Accessible form' for w in observer.windows()))
        human_window = next(w for w in observer.windows() if w['title'] == 'Your typing space')
        script(
            env,
            out,
            'accessibility-arrange.js',
            'for (const w of workspace.windowList()) {\n'
            ' if(w.caption==="Agent canvas") w.minimized=true;\n'
            ' if(w.caption==="Accessible form") w.frameGeometry={x:700,y:80,width:360,height:260};\n'
            ' if(w.caption==="Your typing space") workspace.activeWindow=w;\n}',
        )
        time.sleep(0.3)
        human(state, 'move', human_window['x'] + 200, human_window['y'] + 300)
        human(state, 'click')
        wait(
            lambda: next(w for w in observer.windows() if w['id'] == human_window['id'])[
                'human_active'
            ]
        )
        form = next(w for w in observer.windows() if w['title'] == 'Accessible form')
    with Client(state['control']) as agent:
        ctx = Context(agent, form['id'], Workspace(out / 'a11y-window', out, out))

        # Qt registers asynchronously after the window maps.
        def registered():
            try:
                return len(ctx.accessible()) >= 3
            except ValueError:
                return False

        wait(registered, 10)
        elements = ctx.accessible()
        text = next(e for e in elements if e['role'] == 'text')
        assert text['text'] == 'hello', text
        report['tree_roles_names_text'] = sorted(e['role'] for e in elements)
        save = ctx.find(role='push button', name='save')
        ctx.click(element=save)
        wait(lambda: 'save' in log.read_text())
        report['click_at_accessible_center_activates_button'] = True
        grid = ctx.find(role='check box', name='grid')
        assert 'checked' not in grid['states']
        ctx.click(element=grid)
        wait(lambda: 'grid True' in log.read_text())
        wait(lambda: 'checked' in ctx.find(role='check box', name='grid')['states'])
        report['accessible_state_reflects_agent_click'] = True
        try:
            ctx.find(role='push button', name='missing')
        except ValueError:
            pass
        else:
            raise AssertionError('find accepted a missing element')
    with Client(state['control']) as observer:
        assert next(w for w in observer.windows() if w['id'] == human_window['id'])['human_active']
        report['human_focus_kept'] = True
finally:
    for child in reversed(children):
        child.terminate()
        try:
            child.wait(3)
        except subprocess.TimeoutExpired:
            child.kill()
print(json.dumps(report))
