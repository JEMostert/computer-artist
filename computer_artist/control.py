"""Targeted stop requests for managed programs; never signal recorded process IDs."""

import fcntl
import json
import math
import os
import stat
import time
from pathlib import Path

from .errors import Interrupted
from .files import atomic_json, key
from .records import _read
from .storage import _marker, locked

STOP_FILE = '.cancel.json'


def read_stop(folder):
    """Return a validated durable stop request, or None when none exists."""
    path = Path(folder) / STOP_FILE
    try:
        encoded, _ = _read(path, 4096)
    except FileNotFoundError:
        return None
    value = json.loads(encoded)
    if (
        not isinstance(value, dict)
        or type(value.get('format')) is not int
        or value['format'] != 1
        or type(value.get('requested_at')) not in (int, float)
        or not math.isfinite(value['requested_at'])
        or not 0 <= value['requested_at'] <= 1e15
        or not isinstance(value.get('reason'), str)
        or not 1 <= len(value['reason']) <= 512
    ):
        raise ValueError('Invalid run stop request')
    return value


def check_stop(folder):
    request = read_stop(folder)
    if request is not None:
        raise Interrupted(request['reason'], code='user_cancelled', details=request)


def request_stop(output_root, identity, *, reason='Stopped by the user'):
    """Request cancellation only while an ordinary managed run holds its lease.

    The supervisor/watch owns cleanup and acknowledges the request in its result.
    A request racing with completion cannot turn a completed run into a new task.
    """
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 512:
        raise ValueError('Stop reason must contain 1–512 characters')
    root = Path(output_root)
    if not root.exists():
        raise ValueError('Run output root does not exist')
    with locked(root) as root:
        folder = root / key(identity)
        _marker(folder)
        encoded, _ = _read(folder / 'request.json', 8 * 1024 * 1024)
        spec = json.loads(encoded)
        if not isinstance(spec, dict) or not (
            spec.get('kind') == 'watch'
            or isinstance(spec.get('source'), str)
            or isinstance(spec.get('module'), str)
        ):
            raise ValueError('This recorded work does not support targeted stopping')
        fd = os.open(folder / '.active.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Run lease must be a regular file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                request = read_stop(folder)
                if request is None:
                    request = {'format': 1, 'requested_at': time.time(), 'reason': reason.strip()}
                    atomic_json(folder / STOP_FILE, request)
                return {'id': folder.name, 'stop_requested': True, 'request': request}
            raise ValueError('Run is no longer active')
        finally:
            os.close(fd)
