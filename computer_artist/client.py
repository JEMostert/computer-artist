"""A bounded, synchronous controller with heartbeat and explicit application leases."""
from collections import deque
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import threading
import time


class ActionError(RuntimeError):
    def __init__(self, operation, reply):
        self.operation, self.reply = operation, reply
        super().__init__(f'{operation}: {reply.get("error", "rejected")}')


# Reserved keyboard helpers; require backend keyboard support before dispatch.
LETTERS = dict(zip('qwertyuiopasdfghjklzxcvbnm',
                   [16,17,18,19,20,21,22,23,24,25,30,31,32,33,34,35,36,37,38,44,45,46,47,48,49,50]))
PLAIN = {**LETTERS, **dict(zip('1234567890', range(2,12))),
         ' ':57, '\n':28, '\t':15, '-':12, '=':13, '[':26, ']':27,
         ';':39, "'":40, '`':41, '\\':43, ',':51, '.':52, '/':53}
SHIFTED = dict(zip('!@#$%^&*()_+{}:"~|<>?', '1234567890-=[];\'`\\,./'))


class ExecutionBudget:
    def __init__(self, deadline, actions):
        self.expires = time.monotonic() + deadline
        self.remaining = actions
        self.lock = threading.Lock()
        self.cancelled = threading.Event()


class Client:
    def __init__(self, path, *, deadline=120, action_budget=20000, lane="agent", shared=None):
        if lane not in ("agent", "host"):
            raise ValueError("lane must be agent or host")
        self.lane, self.socket_path = lane, str(path)
        self.shared = shared or ExecutionBudget(deadline, action_budget)
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(3)
        try:
            self.socket.connect(str(path))
        except OSError:
            self.socket.close()
            raise
        self.reader = self.socket.makefile('rb')
        self.lock = threading.RLock()
        self.closed = threading.Event()
        self.expires = self.shared.expires
        self.lease = ''
        self.generation = None
        self.failure = None
        self.trace = deque(maxlen=512)
        if lane == 'host':
            try:
                support = self._request('capabilities')
                if support.get('lane') != 'host' or support.get('host_pointer') is not True or support.get('protocol',0) < 3:
                    raise ValueError('Connected plugin does not support the host lane; refusing fallback')
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
                self.socket.sendall((json.dumps({**values, 'op':op, 'lane':self.lane})+'\n').encode())
                line = self.reader.readline(65537)
                if not line or len(line) > 65536:
                    raise ConnectionError('Compositor disconnected or sent an oversized reply')
                reply = json.loads(line)
            except BaseException:
                # An interrupted exchange has no reliable reply boundary. Close
                # the transport so KWin returns ownership and a heartbeat cannot
                # consume the abandoned reply or block cleanup on the reader.
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                raise
            self.trace.append({'time': time.monotonic(), 'lane':self.lane, 'operation':op,
                               'ok':reply.get('ok'), 'error':reply.get('error')})
            if not reply.get('ok'):
                raise ActionError(op, reply)
            return reply

    def request(self, op, **values):
        with self.lock:
            if self.failure:
                raise self.failure
            with self.shared.lock:
                exhausted = time.monotonic() >= self.expires or self.shared.remaining <= 0 or self.shared.cancelled.is_set()
                if not exhausted:
                    self.shared.remaining -= 1
            if exhausted:
                self.release()
                self.failure = TimeoutError('Execution cancelled or deadline/action budget exceeded')
                raise self.failure
            if op in ('move','button','key','scroll','focus'):
                values.update(lease=self.lease, generation=self.generation)
            return self._request(op, **values)

    def _heartbeat(self):
        while not self.closed.wait(1):
            try:
                with self.lock:
                    if time.monotonic() >= self.expires or self.shared.cancelled.is_set():
                        if self.lease:
                            self._request('release', lease=self.lease)
                            self.lease = ''
                        self.failure = TimeoutError('Program deadline exceeded')
                        return
                    reply = self._request('ping')
                    if self.lease and reply['lease'] != self.lease:
                        self.failure = ActionError('ping', {'error':'lease_revoked'})
                        return
            except Exception as e:
                self.failure = e
                return

    def windows(self):
        reply = self.request('windows')
        # Explicit observation is the only operation that refreshes geometry.
        self.generation = reply['generation']
        return reply['windows']

    def acquire(self, window_id):
        reply = self.request('acquire', window=window_id)
        self.lease, self.generation = reply['lease'], reply['generation']
        return self.lease

    def release(self):
        if self.lease:
            self._request('release', lease=self.lease)
            self.lease = ''

    @contextmanager
    def owned(self, window_id):
        self.acquire(window_id)
        try:
            yield self
        finally:
            self.release()

    def move(self, x, y):
        return self.request('move', x=x, y=y)

    def focus(self, window_id):
        """Focus a leased window on the agent keyboard without clicking in it."""
        return self.request('focus', window=window_id)

    def button(self, code=272, pressed=True):
        return self.request('button', code=code, pressed=pressed)

    def key(self, code, pressed):
        if pressed:
            end = time.monotonic()+3
            while not self.request('ping').get('keyboard_ready', False):
                if time.monotonic() >= end:
                    self._request('cancel')
                    raise TimeoutError('Application did not acknowledge agent activation')
                time.sleep(.01)
        return self.request('key', code=code, pressed=pressed)

    def click(self, x, y, button=272):
        self.move(x,y)
        self.button(button, True)
        self.button(button, False)

    def chord(self, *codes):
        try:
            for code in codes: self.key(code, True)
            for code in reversed(codes): self.key(code, False)
        except Exception:
            self._request('cancel')
            raise

    @staticmethod
    def text_sequence(text):
        sequence = []
        for char in text:
            shifted = char.isascii() and char.isupper() or char in SHIFTED
            base = SHIFTED.get(char, char.lower())
            if base not in PLAIN:
                raise ValueError(f'Character not supported by US keyboard driver: {char!r}')
            sequence.append((PLAIN[base], shifted))
        return sequence

    def type_text(self, text):
        sequence = self.text_sequence(text)
        for code, shifted in sequence:
            self.chord(42, code) if shifted else self.chord(code)

    def scroll(self, delta, *, axis='vertical', v120=0):
        return self.request('scroll', axis=axis, delta=delta, v120=v120)

    def path(self, points, *, interval=.016):
        """Draw sampled points. Errors cancel the gesture; coordinates never retry silently."""
        iterator = iter(points)
        first = next(iterator)
        self.move(*first)
        self.button(pressed=True)
        try:
            due = time.monotonic()
            for point in iterator:
                due += interval
                time.sleep(max(0, due-time.monotonic()))
                self.move(*point)
            self.button(pressed=False)
        except Exception:
            self._request('cancel')
            raise

    def wait_for(self, predicate, *, timeout=5, interval=.05):
        end = time.monotonic()+timeout
        while time.monotonic() < end:
            result = predicate(self.windows())
            if result: return result
            time.sleep(interval)
        raise TimeoutError('Expected application state was not observed')

    def save_trace(self, path):
        Path(path).write_text(json.dumps(list(self.trace), indent=2)+'\n')

    def close(self):
        if self.closed.is_set(): return
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

    def __enter__(self): return self
    def __exit__(self, *_): self.close()
