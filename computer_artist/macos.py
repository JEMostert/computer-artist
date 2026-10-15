"""Native macOS lanes: process-targeted agent events and explicit shared host input.

Coordinates are logical desktop points, including the window decoration. No agent
operation uses the global event tap, raises windows, or defocuses the human app.
"""

import hashlib
import json
import math
import os
import time
import uuid
from pathlib import Path

from . import locking
from .files import atomic_json
from .platform_io import open_no_follow


class MacOSBackend:
    def __init__(self, lane):
        from .macos_accessibility import Accessibility
        from .macos_native import Native

        self.api, self.ax, self.lane = Native(), Accessibility(), lane
        self.lease, self.reason = '', None
        self.target = self.pid = self.identity = self.baseline = None
        self.keys, self.buttons = set(), set()
        self.pointer = self.host_pointer = self.overlay = None
        self.locks = []
        self.root = Path.home() / 'Library/Application Support/computer-artist'
        self.stop_file = self.root / 'input-stop.json'
        self.stop_stamp = None
        self.cleanup_file = os.environ.get('CA_MACOS_CLEANUP_FILE')
        if self.cleanup_file:
            base = Path(self.cleanup_file)
            self.cleanup_file = str(base.with_name(f'{base.stem}-{uuid.uuid4().hex}.json'))
        self.saved_clipboard = self.clipboard_stamp = None
        self.last_click = None
        self.click_count = 0
        self.activity = None

    def capabilities(self):
        operations = [
            'windows',
            'capture',
            'accessible',
            'capabilities',
            'ping',
            'session_status',
            'acquire',
            'release',
            'cancel',
            'session_close',
            'takeover',
            'move',
            'button',
            'key',
            'keymap',
            'keyboard_begin',
            'scroll',
        ]
        if self.lane == 'host':
            operations += [
                'focus',
                'clipboard_get',
                'clipboard_set',
                'clipboard_save',
                'clipboard_restore',
            ]
        return {
            'ok': True,
            'backend': 'macos',
            'protocol': 3,
            'lane': self.lane,
            'operations': operations,
            'host_pointer': self.lane == 'host',
            'keyboard': True,
            'clipboard': self.lane == 'host',
            'paste_shortcut': [125, 47],
            'coordinate_space': 'desktop_logical_points',
            'automatic_sessions': True,
            'lanes': {
                'agent': {
                    'pointer': True,
                    'keyboard': True,
                    'physical_keyboard': True,
                    'drawn_pointer': True,
                    'delivery': ['skylight', 'process_keyboard'],
                },
                'host': {'pointer': True, 'keyboard': True},
            },
            'limitations': [
                'Agent input uses private macOS APIs; application and OS compatibility vary. '
                'There is no host fallback and input separation is not a security sandbox.',
                'Targets must be visible on the current Space. Focusing the target is allowed; '
                'conflicting human input stops the lane. One lane owns an application at a time.',
                'Drawing and physical layout text require application acceptance of posted '
                'events. IME, dead-key composition and app data drag-and-drop are unsupported.',
                'Window captures include decoration; Retina pixel size differs from input points.',
                'An application may activate itself. ca does not restore or change human focus; '
                'keyboard input still requires the exact selected window.',
            ],
        }

    def _identity(self, info):
        pid, wid = int(info['kCGWindowOwnerPID']), int(info['kCGWindowNumber'])
        stamp = self.api.process_identity(pid)
        digest = hashlib.sha256(str(stamp).encode()).hexdigest()[:16]
        return f'macos-{wid:x}-{pid:x}-{digest}'

    @staticmethod
    def _geometry(info):
        b = info['kCGWindowBounds']
        return tuple(float(b[k]) for k in ('X', 'Y', 'Width', 'Height'))

    def _infos(self):
        return [
            i
            for i in self.api.windows()
            if int(i.get('kCGWindowLayer', -1)) == 0
            and float(i.get('kCGWindowAlpha', 1)) > 0
            and all(v > 1 for v in self._geometry(i)[2:])
        ]

    def windows(self):
        front = self.api.front_pid()
        entries = []
        for info in self._infos():
            try:
                identity = self._identity(info)
            except ValueError:
                continue
            pid = int(info['kCGWindowOwnerPID'])
            x, y, width, height = self._geometry(info)
            reasons = []
            if pid == front and self.ax.focused_window(pid) != int(info['kCGWindowNumber']):
                reasons.append('human_keyboard_in_another_window')
            entries.append(
                {
                    'id': identity,
                    'pid': pid,
                    'title': str(info.get('kCGWindowName', '')),
                    'class': str(info.get('kCGWindowOwnerName', '')),
                    'x': x,
                    'y': y,
                    'width': width,
                    'height': height,
                    'visible': True,
                    'native': True,
                    'observable': True,
                    'backend': 'macos',
                    'human_active': pid == front,
                    'agent': bool(
                        self.lease and self.lane == 'agent' and identity == self.identity
                    ),
                    'host': bool(self.lease and self.lane == 'host' and identity == self.identity),
                    'acquirable': not reasons if self.lane == 'agent' else True,
                    'agent_restrictions': reasons,
                    'agent_delivery': ['skylight', 'process_keyboard'],
                }
            )
        generation = hashlib.sha256(
            json.dumps(
                [tuple(w[k] for k in ('id', 'x', 'y', 'width', 'height')) for w in entries]
            ).encode()
        ).hexdigest()
        return {'ok': True, 'windows': entries, 'generation': generation}

    def resolve(self, identity):
        # Do not read any other application's title or accessibility content here.
        for info in self._infos():
            try:
                if self._identity(info) == identity:
                    return info
            except ValueError:
                continue
        raise ValueError('Exact live window is unavailable on this Space; observe and rebind it')

    def require_host(self):
        if self.lane != 'host':
            raise PermissionError('Shared pointer/focus/clipboard access requires explicit --host')

    def _lock(self, name, *, claim=True):
        fd = open_no_follow(self.root / name, os.O_CREAT | os.O_RDWR)
        try:
            locking.flock(fd, locking.LOCK_EX | locking.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise PermissionError(
                'Another lane already controls this application or host input'
            ) from None
        self.locks.append(fd)
        if claim:
            os.ftruncate(fd, 0)
            os.write(fd, self.lease.encode())
        return fd

    def acquire(self, identity):
        if self.lease:
            raise PermissionError('Release the existing window lease first')
        self.api.session_ready()
        if not self.ax.ax.AXIsProcessTrusted():
            raise PermissionError('Enable Accessibility permission for the terminal running ca')
        info = self.resolve(identity)
        self.pid, self.target = int(info['kCGWindowOwnerPID']), int(info['kCGWindowNumber'])
        self.identity, self.baseline = identity, self._geometry(info)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lease = uuid.uuid4().hex
        try:
            self._lock(f'app-{self.pid}.lock')
            if self.lane == 'host':
                self._lock('host-input.lock')
                from .macos_native import KEYCODES

                q = self.api.q
                if any(
                    q.CGEventSourceKeyState(q.kCGEventSourceStateHIDSystemState, key)
                    for key in set(KEYCODES.values())
                ) or any(
                    q.CGEventSourceButtonState(q.kCGEventSourceStateHIDSystemState, button)
                    for button in range(3)
                ):
                    raise PermissionError(
                        'Release human keys and mouse buttons before host acquisition'
                    )
            self.reason = None
            self.stop_stamp = self.stop_file.read_bytes() if self.stop_file.exists() else None
            self.guard({'lease': self.lease})
            self.ax.window(self.pid, self.target)
            if self.lane == 'agent':
                from .macos_activity import Activity
                from .macos_pointer import AgentPointer

                if (
                    self.api.front_pid() == self.pid
                    and self.ax.focused_window(self.pid) != self.target
                ):
                    raise PermissionError('Human keyboard is in another window of this application')
                self.activity = Activity(self.api, self.pid, self.target)
                self.api.prepare(self.pid, self.target)
                self.ax.prepare_keyboard(self.pid, self.target)
                self.overlay = AgentPointer()
                time.sleep(0.05)
            else:
                self._focus_host()
            self.guard({'lease': self.lease})
            self.save_cleanup()
            return {
                'ok': True,
                'lease': self.lease,
                'generation': self.windows()['generation'],
                'keyboard_ready': True,
            }
        except BaseException:
            self.revoke(self.reason)
            raise

    def guard(self, values):
        if not self.lease or values.get('lease') != self.lease:
            raise PermissionError("Input requires this connection's acquired window lease")
        try:
            self.api.session_ready()
            if self.stop_file.exists() and self.stop_file.read_bytes() != self.stop_stamp:
                self.reason = 'explicit_stop'
                raise PermissionError('Input stopped by another command')
            try:
                info = self.resolve(self.identity)
            except ValueError:
                self.reason = 'target_unavailable'
                raise
            if self._geometry(info) != self.baseline:
                self.reason = 'geometry_changed'
                raise ValueError('Target geometry changed; observe again in a fresh run')
            if self.lane == 'agent':
                front = self.api.front_pid()
                if self.activity:
                    try:
                        self.activity.check()
                    except PermissionError:
                        self.reason = 'human_takeover'
                        raise
                if front == self.pid and self.ax.focused_window(self.pid) != self.target:
                    self.reason = 'keyboard_window_changed'
                    raise PermissionError('Human keyboard moved to another application window')
            elif self.host_pointer is not None:
                if (
                    self.api.front_pid() != self.pid
                    or self.ax.focused_window(self.pid) != self.target
                ):
                    self.reason = 'foreground_changed'
                    raise PermissionError('Host focus changed; input stopped')
                if self.api.cursor() != self.host_pointer:
                    self.reason = 'human_pointer_moved'
                    raise PermissionError('Human pointer moved; input stopped')
        except BaseException:
            self.revoke(self.reason or 'session_unavailable')
            raise

    def _focus_host(self):
        self.require_host()
        import ApplicationServices as AS
        from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication

        app = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        if app is None or not app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps):
            raise PermissionError('macOS refused host activation; focus the target and retry')
        window = self.ax.window(self.pid, self.target)
        AS.AXUIElementSetAttributeValue(window, 'AXMain', True)
        AS.AXUIElementPerformAction(window, 'AXRaise')
        time.sleep(0.05)
        if self.api.front_pid() != self.pid or self.ax.focused_window(self.pid) != self.target:
            raise PermissionError('Host target did not become focused')
        self.host_pointer = self.api.cursor()

    def _keyboard(self):
        if self.ax.focused_window(self.pid) != self.target:
            # Re-routing a held chord would send its release to a different window.
            if self.keys:
                self.revoke('keyboard_window_changed')
                raise PermissionError('Target keyboard window changed during held input')
            if self.api.front_pid() == self.pid:
                self.revoke('keyboard_window_changed')
                raise PermissionError('Refusing to redirect the human application keyboard window')
            self.ax.prepare_keyboard(self.pid, self.target)
        if self.ax.focused_window(self.pid) != self.target:
            raise PermissionError('Keyboard focus does not match the acquired window')

    def _flags(self, keys=None):
        from .macos_native import MODIFIERS

        flags = 0
        for code in self.keys if keys is None else keys:
            flags |= MODIFIERS.get(code, 0)
        return flags

    def save_cleanup(self):
        if self.cleanup_file:
            atomic_json(
                self.cleanup_file,
                {
                    'lane': self.lane,
                    'lease': self.lease,
                    'pid': self.pid,
                    'window': self.target,
                    'identity': self.identity,
                    'geometry': self.baseline,
                    'pointer': self.pointer,
                    'keys': sorted(self.keys),
                    'buttons': sorted(self.buttons),
                },
            )

    def release_input(self):
        # Only process-targeted releases are safe after human takeover. Never send
        # an agent key-up into an application's newly selected unrelated window.
        errors = []
        live = False
        if self.identity:
            try:
                self.resolve(self.identity)
                live = True
            except ValueError:
                pass
        key_target = live and self.ax.focused_window(self.pid) == self.target
        for code in list(self.keys):
            if key_target or self.lane == 'host':
                try:
                    self.api.key(
                        self.pid,
                        code,
                        False,
                        self._flags(self.keys - {code}),
                        host=self.lane == 'host',
                    )
                except Exception as error:
                    errors.append(str(error))
                    continue
            self.keys.discard(code)
        for code in list(self.buttons):
            if (live and self.pointer) or self.lane == 'host':
                try:
                    point = self.api.cursor() if self.lane == 'host' else self.pointer
                    self.api.mouse(
                        self.pid,
                        self.target,
                        self.baseline[:2],
                        point,
                        'up',
                        button=code - 272,
                        clicks=1,
                        host=self.lane == 'host',
                    )
                except Exception as error:
                    errors.append(str(error))
                    continue
            self.buttons.discard(code)
        self.save_cleanup()
        if errors:
            raise OSError('macOS input release failed: ' + '; '.join(errors))

    def revoke(self, reason=None):
        try:
            self.release_input()
        finally:
            if self.activity:
                self.activity.close()
                self.activity = None
            if self.overlay:
                self.overlay.close()
                self.overlay = None
            self.lease, self.reason = '', reason
            self.target = self.pid = self.identity = self.baseline = None
            self.pointer = self.host_pointer = None
            for fd in self.locks:
                os.close(fd)
            self.locks.clear()

    def _clipboard(self, op, values):
        self.require_host()
        from AppKit import NSPasteboard, NSPasteboardItem, NSPasteboardTypeString

        board = NSPasteboard.generalPasteboard()
        if op == 'clipboard_get':
            text = board.stringForType_(NSPasteboardTypeString)
            if text is None:
                raise ValueError('Clipboard does not contain plain text')
            if len(text.encode('utf-8')) > 8192:
                raise ValueError('Clipboard exceeds the 8192-byte text limit')
            return {'ok': True, 'text': str(text)}
        if op == 'clipboard_save':
            saved, size = [], 0
            for item in board.pasteboardItems() or []:
                copied = NSPasteboardItem.alloc().init()
                for kind in item.types():
                    data = item.dataForType_(kind)
                    if data is None:
                        raise ValueError('A clipboard format cannot be preserved')
                    size += len(data)
                    if size > 8 * 1024 * 1024:
                        raise ValueError('Clipboard exceeds the bounded restore size')
                    copied.setData_forType_(data, kind)
                saved.append(copied)
            self.saved_clipboard = saved
            self.clipboard_stamp = board.changeCount()
            return {'ok': True, 'saved': True}
        if op == 'clipboard_restore':
            if self.saved_clipboard is None or board.changeCount() != self.clipboard_stamp:
                self.saved_clipboard = None
                return {
                    'ok': True,
                    'restored': False,
                    'reason': 'Clipboard changed; preserving latest',
                }
            board.clearContents()
            restored = not self.saved_clipboard or bool(board.writeObjects_(self.saved_clipboard))
            self.saved_clipboard = None
            return {'ok': True, 'restored': restored}
        if self.lease:
            self.guard(values)
        text = values['text']
        if not isinstance(text, str) or len(text.encode('utf-8')) > 8192:
            raise ValueError('Clipboard text must be at most 8192 UTF-8 bytes')
        board.clearContents()
        if not board.setString_forType_(text, NSPasteboardTypeString):
            raise OSError('Could not write the host clipboard')
        self.clipboard_stamp = board.changeCount()
        return {'ok': True}

    def request(self, op, **values):
        if op == 'capabilities':
            return self.capabilities()
        if op == 'windows':
            return self.windows()
        if op == 'keymap':
            return self.api.keymap()
        if op == 'acquire':
            return self.acquire(values['window'])
        if op in ('release', 'cancel', 'session_close', 'takeover'):
            if op in ('session_close', 'takeover'):
                self.root.mkdir(parents=True, exist_ok=True)
                atomic_json(self.stop_file, {'id': uuid.uuid4().hex})
            try:
                self.revoke('explicit_stop' if op in ('session_close', 'takeover') else None)
            finally:
                if op == 'session_close':
                    from .macos_pointer import close_session

                    close_session()
            return {'ok': True}
        if op == 'ping':
            if self.lease:
                self.guard({'lease': self.lease})
            return {'ok': True, 'lease': self.lease, 'keyboard_ready': bool(self.lease)}
        if op == 'session_status':
            from .macos_pointer import session_status

            pointer = session_status()
            return {
                **pointer,
                'scope': 'desktop_visual_session',
                'lanes_scope': 'connection',
                'lanes': {
                    lane: {
                        'busy': bool(self.lease and self.lane == lane),
                        'supported': True,
                        'stop_reason': self.reason if self.lane == lane else None,
                    }
                    for lane in ('agent', 'host')
                },
            }
        if op in ('accessible', 'capture'):
            self.api.session_ready()
            info = self.resolve(values['window'])
            pid, wid = int(info['kCGWindowOwnerPID']), int(info['kCGWindowNumber'])
            if op == 'accessible':
                result = self.ax.elements(
                    pid,
                    wid,
                    self._geometry(info)[:2],
                    role=values.get('role'),
                    name=values.get('name'),
                    max_nodes=values.get('max_nodes', 2000),
                    deadline=time.monotonic() + 3,
                )
                return {'ok': True, 'source': 'macos_accessibility', **result}
            from .macos_capture import capture

            result = capture(wid, values['path'])
            window = next(w for w in self.windows()['windows'] if w['id'] == values['window'])
            return {
                'ok': True,
                'path': str(values['path']),
                'source': 'macos_window_server',
                'window': window,
                'may_include_overlapping_windows': False,
                **result,
            }
        if op.startswith('clipboard_'):
            return self._clipboard(op, values)
        self.guard(values)
        host = self.lane == 'host'
        if op == 'focus':
            self.require_host()
            if values['window'] != self.identity:
                raise PermissionError('Focus requires the acquired target')
            self._focus_host()
        elif op == 'keyboard_begin':
            self._keyboard()
        elif op == 'key':
            from .macos_native import KEYCODES

            code, pressed = values['code'], values['pressed']
            if type(code) is not int or code not in KEYCODES or type(pressed) is not bool:
                raise ValueError('Unsupported macOS physical key')
            self._keyboard()
            if pressed:
                self.keys.add(code)
                self.save_cleanup()
            flags = self._flags() if pressed else self._flags(self.keys - {code})
            self.api.key(self.pid, code, pressed, flags, host=host)
            if not pressed:
                self.keys.discard(code)
            self.save_cleanup()
        elif op == 'move':
            x, y = values['x'], values['y']
            left, top, width, height = self.baseline
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y)) or not (
                left <= x < left + width and top <= y < top + height
            ):
                raise ValueError('Point is outside the acquired window')
            if not host and self.overlay and not self.buttons:
                # A new action's pen travels there visibly; the input follows on arrival.
                self.overlay.glide_to(x, y)
            self.pointer = (x, y)
            self.save_cleanup()
            self.api.mouse(
                self.pid,
                self.target,
                self.baseline[:2],
                self.pointer,
                'drag' if self.buttons else 'move',
                button=next(iter(self.buttons), 272) - 272,
                clicks=1 if self.buttons else 0,
                flags=self._flags(),
                host=host,
            )
            if host:
                self.host_pointer = self.api.cursor()
            elif self.overlay:
                self.overlay.move(x, y)
            time.sleep(0.012)
        elif op == 'button':
            code, pressed = values['code'], values['pressed']
            if type(code) is not int or code not in (272, 273, 274) or type(pressed) is not bool:
                raise ValueError('Unsupported mouse button')
            if not self.pointer:
                raise ValueError('Move inside the leased window before pressing a button')
            if pressed:
                if self.buttons:
                    raise ValueError('Release the held mouse button first')
                now = time.monotonic()
                self.click_count = (
                    self.click_count + 1
                    if self.last_click
                    and (self.last_click[1] == self.pointer and now - self.last_click[0] < 0.5)
                    else 1
                )
                self.last_click = (now, self.pointer)
                self.buttons.add(code)
                self.save_cleanup()
            self.api.mouse(
                self.pid,
                self.target,
                self.baseline[:2],
                self.pointer,
                'down' if pressed else 'up',
                button=code - 272,
                clicks=self.click_count,
                flags=self._flags(),
                host=host,
            )
            if not host and self.overlay:
                self.overlay.press(pressed)
            if not pressed:
                self.buttons.discard(code)
                self.save_cleanup()
            time.sleep(0.016 if pressed else 0.05)
        elif op == 'scroll':
            delta, axis = values['delta'], values.get('axis', 'vertical')
            if (
                axis not in ('vertical', 'horizontal')
                or type(delta) not in (int, float)
                or not (math.isfinite(delta) and abs(delta) <= 100000)
            ):
                raise ValueError('Invalid scroll delta or axis')
            if not self.pointer:
                raise ValueError('Move inside the leased window before scrolling')
            self.api.scroll(
                self.pid,
                self.target,
                self.baseline[:2],
                self.pointer,
                delta,
                axis,
                flags=self._flags(),
                host=host,
            )
        else:
            raise ValueError(f'Unsupported macOS operation {op!r}; no host fallback')
        self.guard(values)
        return {'ok': True}

    def close(self):
        self.saved_clipboard = None
        self.revoke(self.reason)


def cleanup_input(path):
    """Supervisor reconciliation after a worker dies, limited to its original app."""
    path = Path(path)
    errors = []
    for entry in sorted(path.parent.glob(f'{path.stem}-*.json')):
        try:
            _cleanup_input(entry)
        except (OSError, ValueError) as error:
            errors.append(str(error))
    if errors:
        raise OSError('; '.join(errors))


def _cleanup_input(path):
    if not path.is_file():
        return
    saved = json.loads(path.read_text())
    if not saved.get('keys') and not saved.get('buttons'):
        return
    backend = MacOSBackend(saved['lane'])
    try:
        backend.cleanup_file = None
        # Never let an old worker's key-up interrupt a newer owner's held chord.
        names = [f'app-{saved["pid"]}.lock']
        if saved['lane'] == 'host':
            names.append('host-input.lock')
        for name in names:
            fd = backend._lock(name, claim=False)
            os.lseek(fd, 0, os.SEEK_SET)
            if os.read(fd, 128).decode() != saved['lease']:
                raise OSError(
                    'Input ownership changed before crash cleanup; stale releases refused'
                )
        backend.identity = saved['identity']
        if backend.lane == 'agent':
            backend.resolve(backend.identity)
        backend.pid, backend.target = saved['pid'], saved['window']
        backend.baseline, backend.pointer = saved['geometry'], saved['pointer']
        backend.keys, backend.buttons = set(saved['keys']), set(saved['buttons'])
        backend.lease = saved['lease']
        backend.cleanup_file = str(path)
        backend.release_input()
    except ValueError:
        # A closed/off-Space/replaced target must never redirect cleanup elsewhere.
        return
    finally:
        backend.close()
