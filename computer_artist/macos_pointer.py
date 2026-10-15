"""An agent-only macOS pointer overlay drawing the ink cursor; never moves the user's pointer."""

import json
import math
import os
import select
import subprocess
import sys
import time


class AgentPointer:
    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'computer_artist.macos_pointer'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        deadline, response = time.monotonic() + 2, b''
        try:
            while time.monotonic() < deadline and b'\n' not in response:
                ready, _, _ = select.select(
                    [self.process.stdout], [], [], max(0, deadline - time.monotonic())
                )
                if not ready:
                    break
                part = os.read(self.process.stdout.fileno(), 64)
                if not part:
                    break
                response += part
            if response != b'READY\n' or self.process.poll() is not None:
                raise ValueError('macOS agent pointer overlay failed to start within two seconds')
        except BaseException:
            self.close()
            raise

    def move(self, x, y):
        if not math.isfinite(x) or not math.isfinite(y):
            raise ValueError('Agent pointer coordinates must be finite')
        self._send({'x': x, 'y': y})

    def press(self, pressed):
        self._send({'pressed': bool(pressed)})

    def _send(self, event):
        if self.process.poll() is not None or self.process.stdin.closed:
            raise ValueError('macOS agent pointer overlay has stopped')
        try:
            self.process.stdin.write((json.dumps(event) + '\n').encode())
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise ValueError('macOS agent pointer overlay connection failed') from error

    def close(self):
        process = self.process
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=0.3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        finally:
            if process.stdout:
                process.stdout.close()


def _run():
    import io

    import AppKit as a
    import objc
    from Foundation import NSData, NSObject, NSTimer

    from .ink_cursor import InkCursor

    class PointerPanel(a.NSPanel):
        def canBecomeKeyWindow(self):
            return False

        def canBecomeMainWindow(self):
            return False

    class Reader(NSObject):
        @objc.python_method
        def setup(self, panel, view, app):
            self.panel, self.view, self.app = panel, view, app
            self.buffer = b''
            self.parent = os.getppid()
            scale = max((s.backingScaleFactor() for s in a.NSScreen.screens()), default=1)
            self.cursor = InkCursor(scale=min(4, max(1, scale)))
            self.next_frame = 0

        def tick_(self, timer):
            if os.getppid() != self.parent:
                self.app.terminate_(None)
                return
            try:
                data = os.read(0, 65536)
            except BlockingIOError:
                data = None
            if data == b'':
                self.app.terminate_(None)
                return
            if data:
                self.buffer += data
                if len(self.buffer) > 131072:
                    self.app.terminate_(None)
                    return
                lines = self.buffer.split(b'\n')
                self.buffer = lines.pop()
                for line in lines:
                    self.apply(line)
            now = time.monotonic()
            if now >= self.next_frame or data:
                self.draw(now)
                self.next_frame = now + (0.016 if self.cursor.busy(now) else 0.05)

        @objc.python_method
        def apply(self, line):
            try:
                event = json.loads(line)
                if 'pressed' in event:
                    self.cursor.press(bool(event['pressed']))
                else:
                    x, y = float(event['x']), float(event['y'])
                    if math.isfinite(x) and math.isfinite(y):
                        self.cursor.move(x, y)
            except (ValueError, TypeError, KeyError):
                pass

        @objc.python_method
        def draw(self, now):
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
            # CG's top-left desktop origin is the primary display's top edge.
            main = a.NSScreen.screens()[0].frame()
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
    os.set_blocking(0, False)
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        0.016, reader, 'tick:', None, True
    )
    print('READY', flush=True)
    app.run()
    timer.invalidate()
    panel.close()


if __name__ == '__main__':
    _run()
