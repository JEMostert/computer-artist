#!/usr/bin/env python3
"""Test the native plugin using packaged KWin, isolated from the desktop."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

root = Path(__file__).resolve().parents[2]
if '--private-bus' not in sys.argv:
    os.execvp(
        'dbus-run-session',
        [
            'dbus-run-session',
            '--',
            sys.executable,
            str(Path(__file__).resolve()),
            '--private-bus',
            *sys.argv[1:],
        ],
    )
base = Path(tempfile.mkdtemp(prefix='ca-stock-', dir=os.environ['XDG_RUNTIME_DIR']))
sys.path.insert(0, str(root))
from computer_artist.storage import managed_run

output_root = (
    Path(os.environ.get('CA_OUTPUT_DIR') or tempfile.mkdtemp(prefix='ca-test-evidence-'))
    .expanduser()
    .resolve()
)
run_storage = managed_run(output_root)
output = run_storage.__enter__()
run_fd = os.open(output / '.active.lock', os.O_RDWR)
fcntl.flock(run_fd, fcntl.LOCK_SH)
config = base / 'config'
config.mkdir()
(config / 'kwinrc').write_text(
    '[Plugins]\ncomputerartistEnabled=true\nnightlightEnabled=false\n[Wayland]\nInputMethod=\n'
)
parent = Path(os.environ.get('WAYLAND_DISPLAY', 'wayland-0'))
if not parent.is_absolute():
    parent = Path(os.environ['XDG_RUNTIME_DIR']) / parent
display = str(base / 'wayland-test')
env = dict(
    os.environ,
    QT_PLUGIN_PATH=os.environ.get('CA_TEST_PLUGIN_PATH', str(root / 'build/plugin')),
    CA_PLUGIN_RUNTIME=str(base),
    XDG_CONFIG_HOME=str(config),
    CA_WINDOW_DIR=os.environ.get('CA_WINDOW_DIR', str(base / 'window')),
    CA_OUTPUT_DIR=str(output_root),
    GSETTINGS_BACKEND='memory',
    QT_QPA_PLATFORMTHEME='generic',
    QT_NO_XDG_DESKTOP_PORTAL='1',
    KWIN_USE_OVERLAYS='0',
    KWIN_WAYLAND_NO_PERMISSION_CHECKS='1',
    CA_EXPERIMENT_OUTPUT=str(output),
)
for key in (
    'LD_LIBRARY_PATH',
    'KWIN_EXPERIMENTAL_AGENT_DIR',
    'KWIN_AGENT_ALLOW_DRM',
    'DISPLAY',
    'SESSION_MANAGER',
):
    env.pop(key, None)
children = []
logs = []


def launch(name, command, environment):
    log = (output / (name + '.log')).open('w')
    logs.append(log)
    child = subprocess.Popen(
        command, env=environment, stdout=log, stderr=subprocess.STDOUT, pass_fds=(run_fd,)
    )
    children.append(child)
    return child


def stop(*_):
    raise KeyboardInterrupt


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
try:
    backend = ['--virtual'] if '--headless' in sys.argv else ['--wayland-display', str(parent)]
    compositor = launch(
        'kwin',
        [
            '/usr/bin/setpriv',
            '--no-new-privs',
            '/usr/bin/kwin_wayland',
            *backend,
            '--socket',
            display,
            '--width',
            '1500',
            '--height',
            '850',
            '--no-lockscreen',
        ],
        env,
    )
    (root / 'build/stock-plugin-starting.json').write_text(
        json.dumps(
            {
                'pid': compositor.pid,
                'bus': os.environ['DBUS_SESSION_BUS_ADDRESS'],
                'output': str(output),
                'wayland': display,
            }
        )
    )
    until = time.monotonic() + 60
    while not (base / 'control').exists():
        if compositor.poll() is not None or time.monotonic() > until:
            raise RuntimeError(
                'Packaged KWin/plugin startup failed: '
                + str(output)
                + ' exit='
                + str(compositor.poll())
                + ' socket='
                + str(Path(display).exists())
            )
        time.sleep(0.1)
    if any(
        flag in sys.argv
        for flag in ('--check', '--check-text', '--check-independent', '--headless')
    ):
        launch('input-device', [str(root / 'build/stock-test-input'), display, 'hold'], env)
        time.sleep(0.3)
    app_env = dict(env, WAYLAND_DISPLAY=display, QT_QPA_PLATFORM='wayland')
    app_env.pop('QT_PLUGIN_PATH', None)
    launch(
        'canvas',
        [sys.executable, str(root / 'tests/integration/seat_fixture.py'), 'agent'],
        app_env,
    )
    time.sleep(1)
    launch(
        'human', [sys.executable, str(root / 'tests/integration/seat_fixture.py'), 'human'], app_env
    )
    time.sleep(1)
    sid = subprocess.check_output(
        [
            'qdbus6',
            'org.kde.KWin',
            '/Scripting',
            'org.kde.kwin.Scripting.loadScript',
            str(root / 'tests/integration/arrange-fixtures.js'),
        ],
        env=env,
        text=True,
    ).strip()
    subprocess.run(
        ['qdbus6', 'org.kde.KWin', '/Scripting/Script' + sid, 'org.kde.kwin.Script.run'],
        env=env,
        check=True,
    )
    state = {
        'pid': compositor.pid,
        'compositor': '/usr/bin/kwin_wayland',
        'plugin': str(Path(env['QT_PLUGIN_PATH']) / 'kwin/plugins/computerartist.so'),
        'control': str(base / 'control'),
        'runtime': str(base),
        'output': str(output),
        'wayland': display,
        'bus': os.environ['DBUS_SESSION_BUS_ADDRESS'],
    }
    (output / 'session.json').write_text(json.dumps(state, indent=2) + '\n')
    (root / 'build/stock-plugin-session.json').write_text(json.dumps(state, indent=2) + '\n')
    print(json.dumps(state), flush=True)
    if '--check-compatibility' in sys.argv:
        subprocess.run(
            [
                sys.executable,
                str(root / 'tests/integration/check-compatibility.py'),
                str(output / 'session.json'),
            ],
            check=True,
        )
    if '--check' in sys.argv:
        subprocess.run(
            [
                sys.executable,
                str(root / 'tests/integration/check-stock-plugin.py'),
                str(output / 'session.json'),
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                str(root / 'tests/integration/check-harness.py'),
                str(output / 'session.json'),
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                str(root / 'tests/integration/check-host-lanes.py'),
                str(output / 'session.json'),
            ],
            check=True,
        )
    if '--check-independent' in sys.argv:
        subprocess.run(
            [
                sys.executable,
                str(root / 'tests/integration/check-independent-keyboard.py'),
                str(output / 'session.json'),
            ],
            check=True,
        )
    elif '--check' in sys.argv or '--check-text' in sys.argv:
        subprocess.run(
            [
                sys.executable,
                str(root / 'tests/integration/check-host-text.py'),
                str(output / 'session.json'),
            ],
            check=True,
        )
    elif '--check-accessibility' in sys.argv:
        subprocess.run(
            [
                sys.executable,
                str(root / 'tests/integration/check-accessibility.py'),
                str(output / 'session.json'),
            ],
            check=True,
        )
    elif '--check-recovery' in sys.argv:
        subprocess.run(
            [
                sys.executable,
                str(root / 'tests/integration/check-recovery.py'),
                str(output / 'session.json'),
            ],
            check=True,
        )
    elif not any(
        flag in sys.argv
        for flag in ('--check-independent', '--check-compatibility', '--check-accessibility')
    ):
        compositor.wait()
except KeyboardInterrupt:
    pass
finally:
    for child in reversed(children):
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    for log in logs:
        log.close()
    os.close(run_fd)
    run_storage.__exit__(*sys.exc_info())
