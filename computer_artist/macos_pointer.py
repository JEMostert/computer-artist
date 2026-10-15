"""An agent-only macOS pointer overlay; never moves the user's pointer."""

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
        if self.process.poll() is not None or self.process.stdin.closed:
            raise ValueError('macOS agent pointer overlay has stopped')
        try:
            self.process.stdin.write((json.dumps({'x': x, 'y': y}) + '\n').encode())
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
    import AppKit as a
    import objc
    from Foundation import NSObject, NSTimer

    class PointerPanel(a.NSPanel):
        def canBecomeKeyWindow(self):
            return False

        def canBecomeMainWindow(self):
            return False

    class PointerView(a.NSView):
        def isFlipped(self):
            return True

        def drawRect_(self, rect):
            arrow = a.NSBezierPath.bezierPath()
            arrow.moveToPoint_((2, 2))
            for point in ((2, 29), (9, 23), (15, 36), (21, 33), (15, 21), (25, 21)):
                arrow.lineToPoint_(point)
            arrow.closePath()
            a.NSColor.colorWithCalibratedRed_green_blue_alpha_(0.1, 0.9, 1.0, 1.0).setFill()
            arrow.fill()
            a.NSColor.colorWithCalibratedWhite_alpha_(0.08, 1.0).setStroke()
            arrow.setLineWidth_(1.8)
            arrow.stroke()

    class Reader(NSObject):
        @objc.python_method
        def setup(self, panel, app):
            self.panel, self.app = panel, app
            self.buffer = b''
            self.parent = os.getppid()

        def tick_(self, timer):
            if os.getppid() != self.parent:
                self.app.terminate_(None)
                return
            try:
                data = os.read(0, 65536)
            except BlockingIOError:
                return
            if not data:
                self.app.terminate_(None)
                return
            self.buffer += data
            if len(self.buffer) > 131072:
                self.app.terminate_(None)
                return
            lines = self.buffer.split(b'\n')
            self.buffer = lines.pop()
            latest = None
            for line in lines:
                try:
                    point = json.loads(line)
                    x, y = float(point['x']), float(point['y'])
                    if math.isfinite(x) and math.isfinite(y):
                        latest = x, y
                except (ValueError, TypeError, KeyError):
                    continue
            if latest is not None:
                # CG's top-left desktop origin is the primary display's top edge.
                main = a.NSScreen.screens()[0].frame()
                top = main.origin.y + main.size.height
                x, y = latest
                self.panel.setFrameOrigin_((x - 2, top - y - 36))
                self.panel.orderFrontRegardless()

    app = a.NSApplication.sharedApplication()
    if not app.setActivationPolicy_(a.NSApplicationActivationPolicyAccessory):
        raise ValueError('Cannot create a nonactivating agent pointer application')
    panel = PointerPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        ((0, 0), (30, 38)),
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
    panel.setContentView_(PointerView.alloc().initWithFrame_(((0, 0), (30, 38))))
    reader = Reader.alloc().init()
    reader.setup(panel, app)
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
