"""Native messaging host that Chrome starts for the Computer Artist extension.

Chrome talks to this process over stdin/stdout (32-bit native-endian length
plus JSON). The host publishes an authenticated same-user endpoint so separate
``ca browser`` invocations can reach this one browser profile, forwards their
requests to the extension and relays the replies. It exits when Chrome closes
the port, removing its record. It never sends input to the operating system.
"""

import itertools
import json
import os
import signal
import socket
import struct
import sys
import threading
import time

from . import browser as bridge

# Chrome rejects host messages over 1 MB.
NATIVE_LIMIT = 1024 * 1024
CLIENT_IDLE = 300.0
MAX_TIMEOUT = 120.0


class Host:
    def __init__(self, stdin, stdout, root=None):
        self.stdin = stdin
        self.stdout = stdout
        self.root = root
        self.write_lock = threading.Lock()
        self.pending = {}
        self.pending_lock = threading.Lock()
        self.ids = itertools.count(1)
        self.closed = threading.Event()
        self.record = None
        self.server = None
        self.key = None
        self.busy = 0
        self.idle = threading.Condition()

    def read_native(self):
        header = self.stdin.read(4)
        if len(header) < 4:
            return None
        (size,) = struct.unpack('=I', header)
        if size > bridge.REPLY_LIMIT:
            raise bridge.BrowserError('payload_too_large', f'Extension sent {size} bytes')
        data = self.stdin.read(size)
        if len(data) < size:
            return None
        value = json.loads(data)
        return value if isinstance(value, dict) else {}

    def write_native(self, value):
        data = json.dumps(value, separators=(',', ':')).encode()
        if len(data) > NATIVE_LIMIT:
            raise bridge.BrowserError(
                'payload_too_large', f'Request is {len(data)} bytes; Chrome accepts {NATIVE_LIMIT}'
            )
        with self.write_lock:
            self.stdout.write(struct.pack('=I', len(data)) + data)
            self.stdout.flush()

    def publish(self, hello):
        record = {
            'instance': hello['instance'],
            'label': str(hello.get('label') or '')[:80],
            'browser': str(hello.get('browser') or '')[:80],
            'extension_version': str(hello.get('extension_version') or '')[:32],
            'protocol': bridge.PROTOCOL,
            'address': self.record['address'] if self.record else None,
            'pid': os.getpid(),
            'started': self.record['started'] if self.record else time.time(),
        }
        self.record = record
        bridge.write_record(self.root, record)

    def run(self):
        hello = self.read_native()
        if not hello or hello.get('type') != 'hello':
            return 2
        if hello.get('protocol') != bridge.PROTOCOL:
            self.write_native(
                {
                    'type': 'refused',
                    'error': 'protocol_mismatch',
                    'detail': f'Host speaks protocol {bridge.PROTOCOL}; update ca or the '
                    'extension so they match',
                }
            )
            return 2
        if not bridge.INSTANCE.fullmatch(str(hello.get('instance', ''))):
            self.write_native({'type': 'refused', 'error': 'invalid_instance'})
            return 2
        self.root = bridge.private_dir(self.root or bridge.rendezvous_dir())
        self.key = bridge.auth_key(self.root)
        self.server, address = bridge.listen(self.root, f'{hello["instance"]}-{os.getpid()}')
        self.record = {'address': address, 'started': time.time()}
        try:
            self.publish(hello)
            threading.Thread(target=self.accept, daemon=True).start()
            self.write_native({'type': 'ready', 'address': address, 'protocol': bridge.PROTOCOL})
            while True:
                message = self.read_native()
                if message is None:
                    return 0
                if message.get('type') == 'hello' and message.get('instance') == hello['instance']:
                    self.publish(message)
                elif message.get('type') == 'reply':
                    self.deliver(message)
        finally:
            self.close()

    def close(self):
        self.closed.set()
        with self.pending_lock:
            waiting = list(self.pending.values())
            self.pending.clear()
        for slot in waiting:
            slot[1] = {
                'ok': False,
                'error': 'instance_disconnected',
                'detail': 'The browser closed the extension connection',
                'delivered': True,
            }
            slot[0].set()
        if self.server:
            self.server.close()
        # Let handlers deliver those failures before the process exits.
        with self.idle:
            self.idle.wait_for(lambda: self.busy == 0, timeout=1.0)
        if self.record and self.record.get('instance'):
            bridge.remove_stale(self.record, self.root)

    def deliver(self, message):
        with self.pending_lock:
            slot = self.pending.pop(message.get('id'), None)
        if slot:
            reply = {k: v for k, v in message.items() if k not in ('type', 'id')}
            slot[1] = reply
            slot[0].set()

    def forward(self, request):
        op = request.get('op')
        if not isinstance(op, str) or not op:
            return {'ok': False, 'error': 'invalid_request', 'detail': 'Missing op'}
        try:
            timeout = min(max(float(request.get('timeout', 30)), 0.1), MAX_TIMEOUT)
        except (TypeError, ValueError):
            return {'ok': False, 'error': 'invalid_request', 'detail': 'Invalid timeout'}
        if self.closed.is_set():
            return {
                'ok': False,
                'error': 'instance_disconnected',
                'detail': 'Browser is gone',
                'delivered': False,
            }
        ident = next(self.ids)
        slot = [threading.Event(), None]
        with self.pending_lock:
            self.pending[ident] = slot
        try:
            self.write_native({**request, 'type': 'request', 'id': ident, 'timeout': timeout})
        except bridge.BrowserError as error:
            with self.pending_lock:
                self.pending.pop(ident, None)
            return error.as_dict()
        except OSError:
            with self.pending_lock:
                self.pending.pop(ident, None)
            return {
                'ok': False,
                'error': 'instance_disconnected',
                'detail': 'Browser is gone',
                'delivered': False,
            }
        if not slot[0].wait(timeout + 2):
            with self.pending_lock:
                self.pending.pop(ident, None)
            return {
                'ok': False,
                'error': 'timeout',
                'detail': f'The extension did not answer {op!r} within {timeout:g} s',
            }
        return slot[1]

    def accept(self):
        while not self.closed.is_set():
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(connection,), daemon=True).start()

    def serve(self, connection):
        with connection:
            try:
                connection.settimeout(bridge.HANDSHAKE_TIMEOUT)
                bridge.handshake_server(connection, self.key)
                connection.settimeout(CLIENT_IDLE)
                while not self.closed.is_set():
                    request = bridge.recv_frame(connection, bridge.REQUEST_LIMIT)
                    with self.idle:
                        self.busy += 1
                    try:
                        bridge.send_frame(connection, self.forward(request))
                    finally:
                        with self.idle:
                            self.busy -= 1
                            self.idle.notify_all()
            except (ConnectionError, PermissionError, socket.timeout, ValueError, OSError):
                return
            except bridge.BrowserError as error:
                try:
                    bridge.send_frame(connection, error.as_dict())
                except OSError:
                    pass


def main():
    if os.name == 'nt':
        import msvcrt

        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

    def terminate(signum, frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, terminate)
    try:
        return Host(sys.stdin.buffer, sys.stdout.buffer).run()
    except bridge.BrowserError as error:
        print(json.dumps(error.as_dict()), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
