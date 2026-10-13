"""Live Windows checks in disposable apps; restore the original foreground on exit."""

import argparse
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from computer_artist.client import Client
from computer_artist.runtime import Context
from computer_artist.supervisor import run_supervised
from computer_artist.windows import Win32
from computer_artist.windows_agent import MessageLane
from computer_artist.windows_monitors import contains, select_monitor
from computer_artist.workspace import Workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--monitor',
        required=True,
        help='Exact allowed monitor/EDID ID; never defaults to another display',
    )
    parser.add_argument(
        '--host-checks',
        action='store_true',
        help='Also activate a disposable host fixture and send real desktop input',
    )
    args = parser.parse_args()
    os.environ['CA_WINDOWS_MONITOR'] = args.monitor
    api = Win32()
    monitor = select_monitor(api, args.monitor)
    left, top, right, bottom = monitor['work']
    if right - left < 1100 or bottom - top < 350:
        raise ValueError('The selected monitor is too small for both disposable test windows')
    foreground = api.user.GetForegroundWindow()
    cursor = wintypes.POINT()
    api.user.GetCursorPos(ctypes.byref(cursor))
    original_pointer = (cursor.x, cursor.y)
    marker = uuid.uuid4().hex[:8]
    titles = [f'CA independent {marker}', f'CA human {marker}']
    children, handles = [], []
    evidence = ROOT / 'local' / 'windows-validation'
    evidence.mkdir(parents=True, exist_ok=True)
    checks = {}
    try:
        for title, x in zip(titles, (left + 60, left + 590)):
            children.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).with_name('windows_fixture.py')),
                        title,
                        str(x),
                        str(top + 80),
                    ],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            )
        with Client('win32', deadline=45, action_budget=2000) as agent:
            until = time.monotonic() + 5
            windows = []
            while time.monotonic() < until:
                windows = agent.windows()
                if all(any(w['title'] == title for w in windows) for title in titles):
                    break
                time.sleep(0.05)
            targets = [next(w for w in windows if w['title'] == title) for title in titles]
            if not all(
                contains(monitor, [w[key] for key in ('x', 'y', 'width', 'height')])
                for w in targets
            ):
                raise ValueError('A fixture is outside the selected monitor; refusing input')
            handles = [int(w['id'].split('-')[1], 16) for w in targets]
            if args.host_checks:
                api.focus_host(handles[1])
            lane = MessageLane(api, handles[0])
            human_lane = MessageLane(api, handles[1])
            editor = lane.controls()[0]
            human_editor = human_lane.controls()[0]
            user_foreground = api.user.GetForegroundWindow()
            ctx = Context(
                agent, targets[0]['id'], Workspace(evidence / 'window', evidence / 'output')
            )
            ctx.click(x=40, y=35)
            ctx.write('Independent one')
            ctx.press('Ctrl+A')
            ctx.write('Independent two')

            def text(lane, control):
                size = lane.message(control, 0x000E)
                buffer = ctypes.create_unicode_buffer(size + 1)
                lane.message(control, 0x000D, size + 1, ctypes.addressof(buffer))
                return buffer.value

            checks['independent_text'] = text(lane, editor) == 'Independent two'
            ctx.click(x=60, y=105)
            checks['independent_button'] = 'clicked=1' in next(
                w['title'] for w in agent.windows() if w['id'] == targets[0]['id']
            )
            ctx.move(x=40, y=35)  # Select the edit control for concurrent independent typing.
            ctx.press('Ctrl+A')
            ctx.write('Agent:')
            with Client(
                'win32',
                lane='host' if args.host_checks else 'agent',
                deadline=20,
                action_budget=1000,
            ) as human:
                human.acquire(targets[1]['id'])

                def agent_work():
                    for char in 'independent':
                        ctx.write(char)

                def human_work():
                    mapping = human.keymap()['characters'] if args.host_checks else None
                    for char in 'human':
                        if args.host_checks:
                            human.chord(*mapping[char])
                        else:
                            human.request('write_text', text=char)

                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(lambda function: function(), [agent_work, human_work]))
                time.sleep(0.1)
                checks['concurrent_separate_text'] = (
                    text(lane, editor) == 'Agent:independent'
                    and text(human_lane, human_editor) == 'human'
                )
            api.user.GetCursorPos(ctypes.byref(cursor))
            checks['human_pointer_unchanged'] = (cursor.x, cursor.y) == original_pointer
            checks['human_foreground_unchanged'] = api.user.GetForegroundWindow() == user_foreground
            observation = ctx.observe()
            checks['capture'] = Path(observation['full_image']).is_file()
            capture = observation['full_image']
            agent.release()
            agent.native_backend.close()

        spec = {
            'socket': 'win32',
            'deadline': 2,
            'budget': 1000,
            'lane': 'agent',
            'window': targets[0]['id'],
            'window_root': str(evidence / 'window'),
            'output_root': str(evidence / 'output'),
            'source': 'def run(ctx):\n    ctx.verify("window exists", ctx.window["visible"])\n    return "checked"',
        }
        result = run_supervised(spec)
        checks['supervised_verified_program'] = result['ok'] and result['status'] == 'verified'
        result = run_supervised(
            {
                **spec,
                'deadline': 0.75,
                'source': 'def run(ctx):\n    ctx.verify("started", True)\n    while True: pass',
            }
        )
        checks['hard_deadline'] = (
            not result['ok'] and result['interruption']['code'] == 'deadline_exceeded'
        )
        checks['deadline_evidence'] = bool(result.get('checks'))
        # Host pointer/button dispatch is confined to the disposable human fixture.
        if args.host_checks:
            with Client('win32', lane='host', deadline=5) as host:
                ctx = Context(
                    host, targets[1]['id'], Workspace(evidence / 'window', evidence / 'output')
                )
                ctx.click(x=50, y=105)
                time.sleep(0.15)
                checks['host_button'] = 'clicked=1' in next(
                    w['title'] for w in host.windows() if w['id'] == targets[1]['id']
                )
        report = {
            'ok': all(checks.values()),
            'checks': checks,
            'capture': capture,
            'agent_scope': 'standard Win32 Edit/Button controls',
            'date': time.strftime('%Y-%m-%d'),
            'monitor': monitor,
            'host_checks': args.host_checks,
        }
        (evidence / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        return 0 if report['ok'] else 1
    finally:
        for handle in handles:
            try:
                MessageLane(api, handle).message(handle, 0x0010)
            except (OSError, ValueError):
                pass
        for process in children:
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait()
        if args.host_checks:
            api.user.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
            api.user.SetCursorPos(*original_pointer)
            if foreground and api.user.IsWindow(foreground):
                api.focus_host(foreground)


if __name__ == '__main__':
    raise SystemExit(main())
