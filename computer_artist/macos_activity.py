"""Observe conflicting human input without intercepting or recording its contents."""

import threading

EVENT_TAG = 0x43414D41434F53


class Activity:
    def __init__(self, api, pid, wid):
        self.api, self.pid, self.wid = api, int(pid), int(wid)
        self._conflict = threading.Event()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._reason = None
        self._tap = self._loop = self._cf = None
        self._callback = self._event
        preflight = getattr(api.q, 'CGPreflightListenEventAccess', None)
        if preflight is None or not preflight():
            raise PermissionError(
                'macOS Input Monitoring permission is required for the invoking application'
            )
        self._thread = threading.Thread(
            target=self._listen, name='macos-input-monitor', daemon=True
        )
        self._thread.start()
        if not self._ready.wait(timeout=2):
            self._fail('macOS input listener did not start within two seconds')
        if self._conflict.is_set():
            self.close()
            self.check()

    def _fail(self, reason):
        if not self._conflict.is_set():
            self._reason = reason
            self._conflict.set()

    def _target_at(self, event):
        point = self.api.q.CGEventGetLocation(event)
        # WindowServer order is front to back; inspect no title or AX content.
        for window in self.api.windows():
            if int(window.get('kCGWindowLayer', -1)) != 0:
                continue
            bounds = window['kCGWindowBounds']
            if (
                bounds['X'] <= point.x < bounds['X'] + bounds['Width']
                and bounds['Y'] <= point.y < bounds['Y'] + bounds['Height']
            ):
                return int(window['kCGWindowNumber']) == self.wid
        return False

    def _event(self, proxy, kind, event, context):
        q = self.api.q
        try:
            if kind in (q.kCGEventTapDisabledByTimeout, q.kCGEventTapDisabledByUserInput):
                self._fail('macOS input listener was disabled; input stopped')
            elif not self._stop.is_set() and not self._conflict.is_set():
                if q.CGEventGetIntegerValueField(event, q.kCGEventSourceUserData) == EVENT_TAG:
                    return event
                if kind == q.kCGEventKeyDown:
                    if self.api.front_pid() == self.pid:
                        self._fail('Human keyboard input entered the target application')
                elif kind in (
                    q.kCGEventLeftMouseDown,
                    q.kCGEventRightMouseDown,
                    q.kCGEventOtherMouseDown,
                ):
                    # A first click may merely focus a background target for watching.
                    if self.api.front_pid() == self.pid and self._target_at(event):
                        self._fail('Human click entered the target window')
                elif kind in (
                    q.kCGEventLeftMouseDragged,
                    q.kCGEventRightMouseDragged,
                    q.kCGEventOtherMouseDragged,
                    q.kCGEventScrollWheel,
                ) and self._target_at(event):
                    self._fail('Human drag or scroll entered the target window')
        except Exception:
            self._fail('macOS input listener failed while checking an event')
        # Listen-only taps never suppress or replace the observed event.
        return event

    def _listen(self):
        try:
            import CoreFoundation as cf
            import objc

            with objc.autorelease_pool():
                try:
                    self._cf = cf
                    q = self.api.q
                    kinds = (
                        q.kCGEventKeyDown,
                        q.kCGEventLeftMouseDown,
                        q.kCGEventRightMouseDown,
                        q.kCGEventOtherMouseDown,
                        q.kCGEventLeftMouseDragged,
                        q.kCGEventRightMouseDragged,
                        q.kCGEventOtherMouseDragged,
                        q.kCGEventScrollWheel,
                    )
                    self._tap = q.CGEventTapCreate(
                        q.kCGHIDEventTap,
                        q.kCGHeadInsertEventTap,
                        q.kCGEventTapOptionListenOnly,
                        sum(1 << kind for kind in kinds),
                        self._callback,
                        None,
                    )
                    if self._tap is None:
                        raise PermissionError('macOS refused the listen-only input event tap')
                    source = cf.CFMachPortCreateRunLoopSource(None, self._tap, 0)
                    if source is None:
                        raise PermissionError(
                            'macOS could not create the input listener run-loop source'
                        )
                    self._loop = cf.CFRunLoopGetCurrent()
                    cf.CFRunLoopAddSource(self._loop, source, cf.kCFRunLoopCommonModes)
                    self._ready.set()
                    while not self._stop.is_set():
                        result = cf.CFRunLoopRunInMode(cf.kCFRunLoopDefaultMode, 0.1, False)
                        if not self._stop.is_set() and (
                            result == cf.kCFRunLoopRunFinished
                            or not cf.CFMachPortIsValid(self._tap)
                        ):
                            self._fail('macOS input listener stopped unexpectedly')
                            break
                    cf.CFRunLoopRemoveSource(self._loop, source, cf.kCFRunLoopCommonModes)
                finally:
                    if self._tap is not None:
                        cf.CFMachPortInvalidate(self._tap)
        except PermissionError as error:
            self._fail(str(error))
        except Exception:
            self._fail('macOS listen-only input monitoring is unavailable')
        finally:
            self._ready.set()

    def check(self):
        if self._conflict.is_set():
            raise PermissionError(self._reason)
        if self._stop.is_set() or not self._thread.is_alive():
            raise PermissionError('macOS input listener is no longer running')

    def close(self):
        self._stop.set()
        cf, loop, tap = self._cf, self._loop, self._tap
        if cf is not None:
            if tap is not None:
                cf.CFMachPortInvalidate(tap)
            if loop is not None:
                cf.CFRunLoopStop(loop)
                cf.CFRunLoopWakeUp(loop)
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=0.5)
