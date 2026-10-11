"""A bounded, synchronous controller with heartbeat and explicit application leases."""

import json
import math
import socket
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

from .workspace import Workspace


class ActionError(RuntimeError):
    def __init__(self, operation, reply):
        self.operation, self.reply = operation, reply
        super().__init__(f'{operation}: {reply.get("error", "rejected")}')


CLIPBOARD_MAX_BYTES = 8192


def require_keyboard(capabilities, lane):
    """Reject unsupported physical-key input before acquiring or focusing a target."""
    operations = set(capabilities.get('operations', []))
    if lane == 'agent':
        agent = capabilities.get('lanes', {}).get('agent', {})
        if 'key' not in operations or not (
            capabilities.get('keyboard') is True or agent.get('keyboard') is True
        ):
            raise ValueError(
                'Connected plugin does not support independent agent keyboard input; '
                'check ca capabilities. No host fallback is permitted'
            )
    elif not {'key', 'focus'} <= operations:
        raise ValueError('This host backend does not support keyboard input')


class ExecutionBudget:
    def __init__(self, deadline, actions):
        if not math.isfinite(deadline) or deadline <= 0:
            raise ValueError('Deadline must be positive and finite')
        if type(actions) is not int or actions <= 0:
            raise ValueError('Action budget must be a positive integer')
        self.expires = time.monotonic() + deadline
        self.remaining = actions
        self.lock = threading.Lock()
        self.cancelled = threading.Event()


class Client:
    def __init__(
        self, path, *, deadline=120, action_budget=20000, lane='agent', shared=None, window_dir=None
    ):
        if lane not in ('agent', 'host'):
            raise ValueError('lane must be agent or host')
        self.lane, self.socket_path = lane, str(path)
        self.window_store = Workspace(window_dir)
        self.shared = shared or ExecutionBudget(deadline, action_budget)
        self.expires = self.shared.expires
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(max(0.001, min(3, self.expires - time.monotonic())))
        try:
            self.socket.connect(str(path))
        except OSError:
            self.socket.close()
            raise
        self.reader = self.socket.makefile('rb')
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.lease = ''
        self.generation = None
        self.failure = None
        self.held_keys = set()
        self.keyboard_ready = False
        self._keyboard_capabilities = None
        self._keyboard_initialized = False
        self.trace = deque(maxlen=512)
        if lane == 'host':
            try:
                support = self._request('capabilities')
                if (
                    support.get('lane') != 'host'
                    or support.get('host_pointer') is not True
                    or support.get('protocol', 0) < 3
                ):
                    raise ValueError(
                        'Connected plugin does not support the host lane; refusing fallback'
                    )
            except BaseException:
                self.reader.close()
                self.socket.close()
                raise
        self.heartbeat = threading.Thread(target=self._heartbeat, daemon=True)
        self.heartbeat.start()

    @property
    def budget(self):
        return self.shared.remaining

    @budget.setter
    def budget(self, value):
        self.shared.remaining = value

    def _request(self, op, **values):
        with self.lock:
            try:
                # Cleanup gets a short independent allowance after expiry. All
                # other exchanges share the execution's remaining wall time.
                end = (
                    time.monotonic() + 0.5
                    if op in ('release', 'cancel')
                    else min(self.expires, time.monotonic() + 3)
                )

                def remaining():
                    timeout = end - time.monotonic()
                    if timeout <= 0:
                        raise TimeoutError('Compositor exchange deadline exceeded')
                    self.socket.settimeout(timeout)

                remaining()
                self.socket.sendall(
                    (json.dumps({**values, 'op': op, 'lane': self.lane}) + '\n').encode()
                )
                line = bytearray()
                while b'\n' not in line and len(line) <= 65536:
                    remaining()
                    chunk = self.reader.read1(65537 - len(line))
                    if not chunk:
                        break
                    line.extend(chunk)
                if not line or len(line) > 65536:
                    raise ConnectionError('Compositor disconnected or sent an oversized reply')
                reply = json.loads(line)
                if (
                    not line.endswith(b'\n')
                    or not isinstance(reply, dict)
                    or type(reply.get('ok')) is not bool
                ):
                    raise ConnectionError('Compositor sent an invalid protocol reply')
            except BaseException as error:
                # An interrupted exchange has no reliable reply boundary. Close
                # the transport so KWin returns ownership and a heartbeat cannot
                # consume the abandoned reply or block cleanup on the reader.
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.failure = self.failure or error
                raise
            self.trace.append(
                {
                    'time': time.monotonic(),
                    'lane': self.lane,
                    'operation': op,
                    'ok': reply.get('ok'),
                    'error': reply.get('error'),
                }
            )
            if not reply.get('ok'):
                raise ActionError(op, reply)
            return reply

    def request(self, op, **values):
        with self.lock:
            if self.failure:
                raise self.failure
            with self.shared.lock:
                exhausted = (
                    time.monotonic() >= self.expires
                    or self.shared.remaining <= 0
                    or self.shared.cancelled.is_set()
                )
                if not exhausted:
                    self.shared.remaining -= 1
            if exhausted:
                self.failure = TimeoutError(
                    'Execution cancelled or deadline/action budget exceeded'
                )
                try:
                    self.release()
                except Exception:
                    pass  # Cleanup must preserve the exhaustion error.
                raise self.failure
            if op in (
                'move',
                'button',
                'key',
                'keyboard_begin',
                'scroll',
                'focus',
                'clipboard_get',
                'clipboard_set',
            ):
                values.update(lease=self.lease, generation=self.generation)
            return self._request(op, **values)

    def _heartbeat(self):
        while not self.closed.wait(1):
            try:
                with self.lock:
                    if (
                        time.monotonic() >= self.expires
                        or self.budget <= 0
                        or self.shared.cancelled.is_set()
                    ):
                        self.failure = TimeoutError(
                            'Program deadline/action budget exceeded or cancelled'
                        )
                        self.release()
                        return
                    reply = self._request('ping')
                    if self.lease and reply['lease'] != self.lease:
                        self.failure = ActionError('ping', {'error': 'lease_revoked'})
                        return
            except Exception as e:
                self.failure = self.failure or e
                return

    def windows(self):
        reply = self.request('windows')
        # Explicit observation is the only operation that refreshes geometry.
        self.generation = reply['generation']
        return self.window_store.display_windows(reply['windows'])

    def acquire(self, window_id):
        reply = self.request('acquire', window=self.window_store.resolve_window(window_id))
        self.lease, self.generation = reply['lease'], reply['generation']
        self.keyboard_ready = reply.get('keyboard_ready') is True
        return self.lease

    def release(self):
        if self.lease:
            try:
                self._request('release', lease=self.lease)
            finally:
                self.lease = ''
                self.held_keys.clear()
                self.keyboard_ready = False
                self._keyboard_initialized = False

    @contextmanager
    def owned(self, window_id):
        self.acquire(window_id)
        try:
            yield self
        except BaseException:
            try:
                self.release()
            except Exception:
                pass
            raise
        else:
            self.release()

    def move(self, x, y):
        return self.request('move', x=x, y=y)

    def focus(self, window_id):
        """Focus a leased host window without moving the pointer."""
        return self.request('focus', window=self.window_store.resolve_window(window_id))

    def button(self, code=272, pressed=True):
        return self.request('button', code=code, pressed=pressed)

    def key(self, code, pressed):
        if type(code) is not int or not 1 <= code <= 247 or type(pressed) is not bool:
            raise ValueError('Key requires a Linux key code (1–247) and a boolean pressed state')
        if self.lane == 'agent':
            self.require_keyboard()
            if not self.lease:
                raise ValueError('Independent keys require an acquired agent window')
            if not self.keyboard_ready:
                self.keyboard_ready = self.request('ping').get('keyboard_ready') is True
                if not self.keyboard_ready:
                    raise ValueError('Selected window is not ready for independent keyboard input')
            if (
                not self._keyboard_initialized
                and 'keyboard_begin' in self._keyboard_capabilities.get('operations', [])
            ):
                self.request('keyboard_begin')
                # Qt queues focus activation from keyboard enter. Separate that
                # notification from the first shortcut; verify the actual outcome
                # in the program before releasing the lease.
                time.sleep(min(0.05, max(0, self.expires - time.monotonic())))
                self.request('ping')
                self._keyboard_initialized = True
        result = self.request('key', code=code, pressed=pressed)
        if pressed:
            self.held_keys.add(code)
        else:
            self.held_keys.discard(code)
        return result

    def require_keyboard(self, capabilities=None):
        if capabilities is not None:
            self._keyboard_capabilities = capabilities
        if self._keyboard_capabilities is None:
            self._keyboard_capabilities = self.request('capabilities')
        require_keyboard(self._keyboard_capabilities, self.lane)

    def click(self, x, y, button=272):
        self.move(x, y)
        try:
            self.button(button, True)
            self.button(button, False)
        except BaseException:
            self.cancel()
            raise

    def cancel(self):
        """Reconcile held input after failure without masking the original error."""
        try:
            self._request('cancel')
        except Exception:
            pass  # A dead connection is reconciled by the compositor.
        self.held_keys.clear()

    def chord(self, *codes, duration=0):
        import math

        if not math.isfinite(duration) or duration < 0:
            raise ValueError('Key duration must be finite and nonnegative')
        if (
            not codes
            or len(set(codes)) != len(codes)
            or any(type(code) is not int or not 1 <= code <= 247 for code in codes)
        ):
            raise ValueError('Provide distinct Linux key codes (1–247)')
        try:
            for code in codes:
                self.key(code, True)
            end = time.monotonic() + duration
            while time.monotonic() < end:
                self.request('ping')  # Enforce cancellation and the shared budget while holding.
                time.sleep(min(0.05, max(0, end - time.monotonic())))
            for code in reversed(codes):
                self.key(code, False)
        except BaseException:
            self.cancel()
            raise

    @staticmethod
    def validate_text(text):
        if not isinstance(text, str) or len(text.encode('utf-8')) > CLIPBOARD_MAX_BYTES:
            raise ValueError(
                f'Clipboard text must be a string of at most {CLIPBOARD_MAX_BYTES} UTF-8 bytes'
            )

    def clipboard_get(self):
        if self.lane != 'host':
            raise ValueError('Clipboard access requires explicit host access')
        return self.request('clipboard_get')['text']

    def clipboard_set(self, text):
        self.validate_text(text)
        if self.lane != 'host':
            raise ValueError('Clipboard access requires explicit host access')
        return self.request('clipboard_set', text=text)

    def paste(self, text, *, shortcut=(42, 110)):
        """Paste into the already focused, leased host target (Shift+Insert)."""
        self.validate_text(text)
        if self.lane != 'host' or not self.lease:
            raise ValueError('Paste requires an acquired host window')
        if self.held_keys:
            raise ValueError('Release held keys before pasting')
        if not self.request('ping').get('keyboard_ready'):
            raise ValueError('Paste requires keyboard focus on the host target')
        self.clipboard_set(text)
        self.chord(*shortcut)

    def type_text(self, text):
        """Compatibility alias: text is entered through clipboard paste."""
        return self.paste(text)

    def stop_reason(self):
        """Ask a fresh connection why this lane last stopped; None when unknown.

        The compositor aborts a revoked connection without a reply, so the
        reason is only available from session status afterwards.
        """
        try:
            with socket.socket(socket.AF_UNIX) as probe:
                probe.settimeout(0.5)
                probe.connect(self.socket_path)
                probe.sendall(
                    (json.dumps({'op': 'session_status', 'lane': self.lane}) + '\n').encode()
                )
                line = probe.makefile('rb').readline(65537)
            reason = json.loads(line)['lanes'][self.lane].get('stop_reason')
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None
        return reason if isinstance(reason, str) and reason else None

    def keymap(self):
        """Characters the live layout produces, as physical modifier/key codes."""
        reply = self.request('keymap')
        characters = reply.get('characters')
        if not isinstance(characters, dict):
            raise ConnectionError('Compositor sent an invalid keymap reply')
        return {
            'characters': characters,
            'layout': reply.get('layout'),
            'layout_name': reply.get('layout_name'),
        }

    def scroll(self, delta, *, axis='vertical', v120=0):
        return self.request('scroll', axis=axis, delta=delta, v120=v120)

    def path(self, points, *, interval=0.016, button=272):
        """Draw sampled points. Errors cancel the gesture; coordinates never retry silently."""
        if not math.isfinite(interval) or interval < 0:
            raise ValueError('Path interval must be finite and nonnegative')
        iterator = iter(points)
        try:
            first = next(iterator)
        except StopIteration:
            raise ValueError('Path cannot be empty') from None
        self.move(*first)
        try:
            self.button(button, pressed=True)
            due = time.monotonic()
            for point in iterator:
                due += interval
                # Long intervals must remain responsive to cancellation/deadline.
                while time.monotonic() < due:
                    if self.failure:
                        raise self.failure
                    if (
                        self.closed.is_set()
                        or self.shared.cancelled.is_set()
                        or time.monotonic() >= self.expires
                        or self.budget <= 0
                    ):
                        self.request('ping')  # Reconcile ownership on exhaustion.
                    time.sleep(min(0.05, max(0, due - time.monotonic())))
                self.move(*point)
            self.button(button, pressed=False)
        except BaseException:
            self.cancel()
            raise

    def wait_for(self, predicate, *, timeout=5, interval=0.05):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = predicate(self.windows())
            if result:
                return result
            time.sleep(interval)
        raise TimeoutError('Expected application state was not observed')

    def save_trace(self, path):
        Path(path).write_text(json.dumps(list(self.trace), indent=2) + '\n')

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        self.heartbeat.join(timeout=4)
        try:
            self.release()
        except (OSError, ConnectionError, ActionError):
            # A revoked/dead transport is already reconciled by the compositor.
            pass
        finally:
            self.reader.close()
            self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
