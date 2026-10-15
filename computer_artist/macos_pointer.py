"""A session-owned, click-through ink pen; action connections never own its lifetime."""

import json
import math
import os
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

from . import locking
from .platform_io import open_no_follow


def _directory():
    root = Path.home() / 'Library/Application Support/computer-artist/pointer'
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('The macOS pointer directory must be owned by the current user')
    if info.st_mode & 0o077:
        root.chmod(0o700)
    return root


def _connect(root):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(2)
    try:
        connection.connect(str(root / 'control.sock'))
    except BaseException:
        connection.close()
        raise
    return connection


def _reply(connection, command):
    connection.sendall((json.dumps(command) + '\n').encode())
    data = bytearray()
    while b'\n' not in data and len(data) < 4096:
        part = connection.recv(4096 - len(data))
        if not part:
            raise ValueError('The macOS pen session stopped')
        data.extend(part)
    if not data.endswith(b'\n'):
        raise ValueError('Invalid macOS pen session response')
    result = json.loads(data)
    if result.get('ok') is not True:
        raise ValueError(result.get('error', 'The macOS pen session refused the request'))
    return result


def session_status():
    """Read-only: inspecting an absent session must not start a pen."""
    root = Path.home() / 'Library/Application Support/computer-artist/pointer'
    try:
        with _connect(root) as connection:
            return _reply(connection, {'op': 'status'})
    except (FileNotFoundError, ConnectionRefusedError):
        return {
            'ok': True,
            'session': '',
            'position': None,
            'visible': False,
            'pressed': False,
            'busy': False,
        }


def close_session():
    """Explicit session end, including when requested by a fresh CLI process."""
    root = Path.home() / 'Library/Application Support/computer-artist/pointer'
    try:
        with _connect(root) as connection:
            _reply(connection, {'op': 'close'})
            # Wait for the helper to drop clients before allowing a new session.
            if connection.recv(1):
                raise ValueError('The macOS pen session did not close cleanly')
    except (FileNotFoundError, ConnectionRefusedError):
        pass


class AgentPointer:
    def __init__(self):
        root = _directory()
        self.connection = None
        deadline = time.monotonic() + 3
        while self.connection is None:
            try:
                self.connection = _connect(root)
                break
            except (FileNotFoundError, ConnectionRefusedError):
                pass
            fd = open_no_follow(root / 'session.lock', os.O_CREAT | os.O_RDWR)
            try:
                try:
                    locking.flock(fd, locking.LOCK_EX | locking.LOCK_NB)
                except BlockingIOError:
                    pass  # A helper is starting or ending; never replace its live socket.
                else:
                    endpoint = root / 'control.sock'
                    try:
                        info = endpoint.lstat()
                    except FileNotFoundError:
                        pass
                    else:
                        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                            raise ValueError('Unsafe macOS pointer socket path')
                        endpoint.unlink()
                    # The helper inherits only its lifetime lock, never a worker/run lease.
                    subprocess.Popen(
                        [sys.executable, '-m', 'computer_artist.macos_pointer', str(root), str(fd)],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                        pass_fds=(fd,),
                    )
            finally:
                os.close(fd)
            if time.monotonic() >= deadline:
                raise ValueError('The macOS pen session did not start within three seconds')
            time.sleep(0.02)
        try:
            status = _reply(self.connection, {'op': 'attach'})
            self.session = status['session']
            self.position = tuple(status['position']) if status.get('position') else None
        except BaseException:
            self.close()
            raise

    def move(self, x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('Agent pointer coordinates must be finite')
        self._send({'x': x, 'y': y})
        self.position = (x, y)

    def glide_to(self, x, y):
        """Let the pen travel visibly to a new action's position; returns when it arrives."""
        from .ink_cursor import glide

        points, delay = glide(self.position, (x, y))
        for point in points:
            self.move(*point)
            time.sleep(delay)
        self.move(x, y)

    def press(self, pressed):
        self._send({'pressed': bool(pressed)})

    def _send(self, event):
        if self.connection is None:
            raise ValueError('This macOS pen controller is closed')
        try:
            self.connection.sendall(
                (json.dumps({**event, 'session': self.session}) + '\n').encode()
            )
        except OSError as error:
            raise ValueError('The macOS pen session has stopped') from error

    def close(self):
        """Release this controller; the last visual position belongs to the session."""
        if self.connection is not None:
            self.connection.close()
            self.connection = None


def _run(root, lifetime_lock):
    import io
    import uuid

    import AppKit as a
    import objc
    import Quartz as q
    from Foundation import NSData, NSObject, NSTimer

    from .ink_cursor import InkCursor

    endpoint = root / 'control.sock'
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(endpoint))
    os.chmod(endpoint, 0o600)
    server.listen(16)
    server.setblocking(False)

    class PointerPanel(a.NSPanel):
        def canBecomeKeyWindow(self):
            return False

        def canBecomeMainWindow(self):
            return False

    class Reader(NSObject):
        @objc.python_method
        def setup(self, panel, view, app):
            self.panel, self.view, self.app = panel, view, app
            self.clients, self.controllers = {}, set()
            self.owner = None
            self.session = uuid.uuid4().hex
            scale = max((s.backingScaleFactor() for s in a.NSScreen.screens()), default=1)
            self.cursor = InkCursor(scale=min(4, max(1, scale)))
            self.next_frame = 0
            self.closing = False

        @objc.python_method
        def status(self):
            position = self.cursor.position
            return {
                'ok': True,
                'session': self.session,
                'position': list(position) if position else None,
                'visible': bool(self.panel.isVisible()),
                'pressed': self.cursor.pressed,
                'busy': bool(self.controllers),
                'pid': os.getpid(),
            }

        @objc.python_method
        def disconnect(self, connection):
            self.clients.pop(connection, None)
            self.controllers.discard(connection)
            if self.owner is connection:
                self.cursor.press(False)
                self.owner = None
            connection.close()

        def tick_(self, timer):
            if self.closing:
                return
            while len(self.clients) < 32:
                try:
                    connection, _ = server.accept()
                except BlockingIOError:
                    break
                connection.setblocking(False)
                self.clients[connection] = b''
            for connection in list(self.clients):
                try:
                    data = connection.recv(65536)
                    if not data:
                        self.disconnect(connection)
                        continue
                    buffer = self.clients[connection] + data
                    if len(buffer) > 131072:
                        self.disconnect(connection)
                        continue
                    lines = buffer.split(b'\n')
                    self.clients[connection] = lines.pop()
                    for line in lines:
                        self.apply(connection, line)
                        if self.closing:
                            return
                except BlockingIOError:
                    continue
                except (OSError, ValueError, TypeError, KeyError):
                    self.disconnect(connection)
            now = time.monotonic()
            if now >= self.next_frame:
                self.draw(now)
                self.next_frame = now + (0.016 if self.cursor.busy(now) else 0.05)

        @objc.python_method
        def apply(self, connection, line):
            event = json.loads(line)
            op = event.get('op')
            if op in ('status', 'attach', 'close'):
                if op == 'attach':
                    self.controllers.add(connection)
                connection.sendall((json.dumps(self.status()) + '\n').encode())
                if op == 'close':
                    self.closing = True
                    self.panel.orderOut_(None)
                    self.app.terminate_(None)
                return
            if connection not in self.controllers or event.get('session') != self.session:
                raise ValueError('A pen controller must attach to this exact session')
            if 'pressed' in event:
                if event['pressed']:
                    if self.owner is not connection:
                        self.cursor.press(False)
                    self.owner = connection
                    self.cursor.press(True)
                elif self.owner is connection:
                    self.cursor.press(False)
                return
            x, y = float(event['x']), float(event['y'])
            if not math.isfinite(x) or not math.isfinite(y):
                raise ValueError('Pen coordinates must be finite')
            if self.owner is not connection:
                self.cursor.press(False)
            self.owner = connection
            self.cursor.move(x, y)

        @objc.python_method
        def draw(self, now):
            session = q.CGSessionCopyCurrentDictionary()
            if (
                not session
                or session.get('CGSSessionScreenIsLocked', False)
                or not session.get(q.kCGSessionOnConsoleKey, False)
                or not session.get(q.kCGSessionLoginDoneKey, False)
            ):
                self.panel.orderOut_(None)
                return
            frame = self.cursor.render(now)
            if frame is None:
                return
            image, (left, top) = frame
            encoded = io.BytesIO()
            image.save(encoded, 'PNG', compress_level=1)
            data = encoded.getvalue()
            picture = a.NSImage.alloc().initWithData_(NSData.dataWithBytes_length_(data, len(data)))
            width, height = image.width / self.cursor.scale, image.height / self.cursor.scale
            picture.setSize_((width, height))
            screens = a.NSScreen.screens()
            if not screens or not any(
                q.CGRectContainsPoint(q.CGDisplayBounds(display), self.cursor.position)
                for display in q.CGGetActiveDisplayList(32, None, None)[1]
            ):
                self.panel.orderOut_(None)
                return
            main = screens[0].frame()
            bottom = main.origin.y + main.size.height - top - height
            self.panel.setFrame_display_(((left, bottom), (width, height)), False)
            self.view.setFrame_(((0, 0), (width, height)))
            self.view.setImage_(picture)
            self.panel.orderFrontRegardless()

    app = a.NSApplication.sharedApplication()
    if not app.setActivationPolicy_(a.NSApplicationActivationPolicyAccessory):
        raise ValueError('Cannot create a nonactivating agent pointer application')
    panel = PointerPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (1, 1)),
        a.NSWindowStyleMaskBorderless | a.NSWindowStyleMaskNonactivatingPanel,
        a.NSBackingStoreBuffered,
        False,
    )
    panel.setTitle_('Computer Artist pen')
    panel.setOpaque_(False)
    panel.setBackgroundColor_(a.NSColor.clearColor())
    panel.setHasShadow_(False)
    panel.setIgnoresMouseEvents_(True)
    panel.setHidesOnDeactivate_(False)
    panel.setLevel_(a.NSFloatingWindowLevel)
    panel.setCollectionBehavior_(
        a.NSWindowCollectionBehaviorCanJoinAllSpaces
        | a.NSWindowCollectionBehaviorFullScreenAuxiliary
    )
    view = a.NSImageView.alloc().initWithFrame_(((0, 0), (1, 1)))
    view.setImageScaling_(a.NSImageScaleAxesIndependently)
    panel.setContentView_(view)
    reader = Reader.alloc().init()
    reader.setup(panel, view, app)
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.016, reader, 'tick:', None, True
    )
    try:
        app.run()
    finally:
        timer.invalidate()
        for connection in list(reader.clients):
            reader.disconnect(connection)
        panel.close()
        server.close()
        endpoint.unlink(missing_ok=True)
        os.close(lifetime_lock)


if __name__ == '__main__':
    _run(Path(sys.argv[1]), int(sys.argv[2]))
