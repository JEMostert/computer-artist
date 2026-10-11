"""Helpers shared by the standalone integration checks."""

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def wait(test, timeout=5, interval=0.05):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if test():
            return
        time.sleep(interval)
    raise AssertionError('condition timed out')


def human(state, *args):
    subprocess.run(
        [
            os.environ.get('CA_TEST_INPUT', str(ROOT / 'build/stock-test-input')),
            state['wayland'],
            *map(str, args),
        ],
        check=True,
    )


def script(env, out, name, source):
    path = out / name
    path.write_text(source)
    sid = subprocess.check_output(
        ['qdbus6', 'org.kde.KWin', '/Scripting', 'org.kde.kwin.Scripting.loadScript', str(path)],
        env=env,
        text=True,
    ).strip()
    subprocess.run(
        ['qdbus6', 'org.kde.KWin', '/Scripting/Script' + sid, 'org.kde.kwin.Script.run'],
        env=env,
        check=True,
    )


def ca(env, *args, source=None, success=True, timeout=20):
    result = subprocess.run(
        [str(ROOT / 'ca'), *args],
        input=source,
        text=True,
        capture_output=True,
        env=env,
        timeout=timeout,
    )
    assert (result.returncode == 0) == success, (result.stdout, result.stderr)
    return json.loads(result.stdout or result.stderr)
