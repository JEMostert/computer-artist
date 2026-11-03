"""Local bridge between ca and browsers running the Computer Artist extension.

Chrome starts ``browser_host`` through native messaging for each connected
extension (one per browser profile). The host listens on a same-user endpoint:
a Unix socket in an owner-only directory on Linux/macOS, loopback TCP on
Windows. Both ends prove knowledge of a per-user key before any request, so
other local users and web pages cannot drive a browser through it. This is
access control for a local tool, not a sandbox against the same user.

Only the standard library is imported: Chrome runs the host with ``python -I``.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import struct
import sys
from pathlib import Path

PROTOCOL = 1
HOST_NAME = 'com.computer_artist.browser'
# Chrome refuses native messages over 1 MB from the host, so requests stay below it.
REQUEST_LIMIT = 900 * 1024
# Chrome may send up to 64 MiB to the host; screenshots are the large replies.
REPLY_LIMIT = 64 * 1024 * 1024
INSTANCE = re.compile(r'[a-z0-9]{8}')
HANDSHAKE_TIMEOUT = 5.0


class BrowserError(Exception):
    """A refusal reported as {'ok': false, 'error': code, 'detail': ...}."""

    def __init__(self, code, detail, **extra):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.extra = extra

    def as_dict(self):
        return {'ok': False, 'error': self.code, 'detail': self.detail, **self.extra}


def data_dir():
    """Same per-user data root the rest of ca uses for installed files."""
    if os.environ.get('CA_BROWSER_DATA_DIR'):
        return Path(os.environ['CA_BROWSER_DATA_DIR']).expanduser()
    base = os.environ.get('XDG_DATA_HOME') or Path.home() / '.local/share'
    return Path(base) / 'computer-artist'


def rendezvous_dir():
    """Where live hosts publish their endpoints; must match in Chrome and the CLI."""
    if os.environ.get('CA_BROWSER_DIR'):
        return Path(os.environ['CA_BROWSER_DIR']).expanduser()
    runtime = os.environ.get('XDG_RUNTIME_DIR')
    if runtime and sys.platform.startswith('linux'):
        return Path(runtime) / 'computer-artist/browsers'
    return data_dir() / 'browsers'


def private_dir(path):
    """Create or validate an owner-only directory (POSIX permissions only)."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        info = path.lstat()
        if path.is_symlink() or info.st_uid != os.getuid():
            raise BrowserError('unsafe_directory', f'{path} is not a directory owned by you')
        if info.st_mode & 0o077:
            os.chmod(path, 0o700)
    return path


def auth_key(root=None):
    """The per-user secret both ends prove; created once, readable only by the owner."""
    path = private_dir(root or rendezvous_dir()) / 'key'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        value = path.read_bytes()
        if len(value) != 32:
            raise BrowserError('invalid_key', f'{path} is not a Computer Artist browser key')
        return value
    value = secrets.token_bytes(32)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(value)
    return value


def send_frame(sock, value, limit=REPLY_LIMIT):
    data = json.dumps(value, separators=(',', ':'), allow_nan=False).encode()
    if len(data) > limit:
        raise BrowserError('payload_too_large', f'Message is {len(data)} bytes; limit {limit}')
    sock.sendall(struct.pack('>I', len(data)) + data)


def recv_exact(sock, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(min(size - len(chunks), 1 << 20))
        if not chunk:
            raise ConnectionError('Connection closed')
        chunks += chunk
    return bytes(chunks)


def recv_frame(sock, limit=REPLY_LIMIT):
    (size,) = struct.unpack('>I', recv_exact(sock, 4))
    if size > limit:
        raise BrowserError('payload_too_large', f'Message is {size} bytes; limit {limit}')
    value = json.loads(recv_exact(sock, size))
    if not isinstance(value, dict):
        raise BrowserError('invalid_message', 'Expected a JSON object')
    return value


def mac(key, role, nonce):
    return hmac.new(key, role.encode() + bytes.fromhex(nonce), hashlib.sha256).hexdigest()


def handshake_server(sock, key):
    """Mutual challenge-response; the key itself never crosses the connection."""
    nonce = secrets.token_hex(16)
    send_frame(sock, {'protocol': PROTOCOL, 'nonce': nonce}, 256)
    answer = recv_frame(sock, 512)
    if not hmac.compare_digest(str(answer.get('mac', '')), mac(key, 'client', nonce)):
        raise PermissionError('Client did not prove the browser bridge key')
    client_nonce = str(answer.get('nonce', ''))
    if not re.fullmatch(r'[0-9a-f]{32}', client_nonce):
        raise PermissionError('Invalid client nonce')
    send_frame(sock, {'mac': mac(key, 'server', client_nonce)}, 256)


def handshake_client(sock, key):
    hello = recv_frame(sock, 256)
    if hello.get('protocol') != PROTOCOL or not re.fullmatch(
        r'[0-9a-f]{32}', str(hello.get('nonce', ''))
    ):
        raise BrowserError('protocol_mismatch', 'Browser host speaks another protocol version')
    nonce = secrets.token_hex(16)
    send_frame(sock, {'nonce': nonce, 'mac': mac(key, 'client', hello['nonce'])}, 512)
    reply = recv_frame(sock, 256)
    if not hmac.compare_digest(str(reply.get('mac', '')), mac(key, 'server', nonce)):
        raise BrowserError('host_unverified', 'Browser host did not prove the bridge key')


def listen(root, name):
    """Bind a new same-user endpoint and return (socket, address string)."""
    if hasattr(socket, 'AF_UNIX') and os.name != 'nt':
        path = Path(root) / f'{name}.sock'
        path.unlink(missing_ok=True)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o177)
        try:
            server.bind(str(path))
        finally:
            os.umask(previous)
        address = f'unix:{path}'
    else:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.bind(('127.0.0.1', 0))
        address = f'tcp:127.0.0.1:{server.getsockname()[1]}'
    server.listen(16)
    return server, address


def connect(address, key, timeout):
    kind, _, rest = address.partition(':')
    if kind == 'unix' and hasattr(socket, 'AF_UNIX'):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        target = rest
    elif kind == 'tcp':
        host, _, port = rest.rpartition(':')
        if host != '127.0.0.1':
            raise BrowserError('invalid_record', 'Browser endpoints must be loopback')
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        target = (host, int(port))
    else:
        raise BrowserError('invalid_record', f'Unsupported browser endpoint {address!r}')
    try:
        sock.settimeout(HANDSHAKE_TIMEOUT)
        sock.connect(target)
        handshake_client(sock, key)
        sock.settimeout(timeout)
        return sock
    except BaseException:
        sock.close()
        raise


def records(root=None):
    """Published host records; unreadable ones are skipped, not trusted."""
    root = Path(root or rendezvous_dir())
    found = []
    if not root.is_dir():
        return found
    for path in sorted(root.glob('*.json')):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and INSTANCE.fullmatch(str(record.get('instance', ''))):
            if path.stem == record['instance']:
                found.append(record)
    return found


def write_record(root, record):
    """Publish a host record atomically with owner-only permissions."""
    path = Path(root) / f'{record["instance"]}.json'
    temp = path.with_name(f'.{path.name}.{os.getpid()}')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(record, stream)
    os.replace(temp, path)


def remove_stale(record, root=None):
    """Remove a record (and its socket) only if it still names this endpoint."""
    root = Path(root or rendezvous_dir())
    path = root / f'{record["instance"]}.json'
    try:
        current = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if current.get('address') == record.get('address'):
        path.unlink(missing_ok=True)
        kind, _, rest = str(record.get('address', '')).partition(':')
        if kind == 'unix' and Path(rest).parent == root:
            Path(rest).unlink(missing_ok=True)


def call(record, request, timeout=30.0, key=None):
    """Send one request to a connected browser and return its reply object."""
    data = len(json.dumps(request))
    if data > REQUEST_LIMIT:
        raise BrowserError('payload_too_large', f'Request is {data} bytes; limit {REQUEST_LIMIT}')
    try:
        sock = connect(record['address'], key or auth_key(), timeout + 5)
    except (ConnectionRefusedError, FileNotFoundError) as error:
        # Nothing listens there any more: the browser or its host has exited.
        raise BrowserError(
            'instance_unavailable',
            f'Browser instance {record["instance"]} is gone: {error}',
            instance=record['instance'],
            stale=True,
        ) from None
    except OSError as error:
        raise BrowserError(
            'instance_unavailable',
            f'Browser instance {record["instance"]} is not answering: {error}',
            instance=record['instance'],
        ) from None
    with sock:
        send_frame(sock, {**request, 'timeout': timeout}, REQUEST_LIMIT)
        try:
            return recv_frame(sock, REPLY_LIMIT)
        except (ConnectionError, socket.timeout) as error:
            raise BrowserError(
                'instance_disconnected',
                f'Browser instance {record["instance"]} stopped answering: {error}',
                instance=record['instance'],
            ) from None


def live_instances(root=None, timeout=5.0, problems=None):
    """Ask every published host for its browser state; prune ones that are gone.

    Hosts that exist but fail to answer are reported in ``problems`` and are
    never selected implicitly.
    """
    root = Path(root or rendezvous_dir())
    if not root.is_dir():
        return []
    key = auth_key(root)
    instances = []
    for record in records(root):
        try:
            reply = call(record, {'op': 'info'}, timeout, key)
        except BrowserError as error:
            if error.extra.get('stale'):
                remove_stale(record, root)
            elif problems is not None:
                problems.append({**summary(record), 'error': error.code, 'detail': error.detail})
            continue
        if reply.get('ok'):
            instances.append({**record, **reply.get('info', {})})
        elif problems is not None:
            problems.append({**summary(record), 'error': reply.get('error')})
    return instances


def select_instance(instances, wanted=None):
    """Pick an instance by id or label; refuse ambiguity instead of guessing."""
    if wanted:
        matches = [i for i in instances if wanted in (i['instance'], i.get('label'))]
        if not matches:
            raise BrowserError(
                'instance_unavailable',
                f'No connected browser instance {wanted!r}',
                instances=[summary(i) for i in instances],
            )
        if len(matches) > 1:
            raise BrowserError(
                'ambiguous_instance',
                f'Several instances are labelled {wanted!r}; use an instance ID',
                instances=[summary(i) for i in matches],
            )
        return matches[0]
    if not instances:
        raise BrowserError(
            'no_instances',
            'No browser with the Computer Artist extension is connected. Run '
            '`ca browser doctor` to check the native host installation.',
        )
    if len(instances) > 1:
        raise BrowserError(
            'ambiguous_instance',
            'Several browser instances are connected; pass --instance or a full tab handle',
            instances=[summary(i) for i in instances],
        )
    return instances[0]


def summary(instance):
    return {
        key: instance.get(key)
        for key in ('instance', 'label', 'browser', 'extension_version', 'pid')
        if instance.get(key) is not None
    }


def parse_tab(value, instance=None):
    """Accept INSTANCE:TAB handles or a bare tab ID with --instance."""
    text = str(value)
    if ':' in text:
        prefix, _, tab = text.partition(':')
        if instance and instance != prefix:
            raise BrowserError(
                'wrong_instance', f'Tab handle {text} belongs to instance {prefix}, not {instance}'
            )
        instance = prefix
    else:
        tab = text
    if not tab.isdigit():
        raise BrowserError(
            'invalid_tab', f'Invalid tab {value!r}; use a handle from ca browser tabs'
        )
    return instance, int(tab)
