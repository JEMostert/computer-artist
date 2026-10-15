"""Validated identifiers and durable local file publication."""

import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from . import locking as fcntl
from .platform_io import open_no_follow


def key(value):
    value = str(value).strip('{}')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError(f'Invalid window/fragment identifier: {value!r}')
    return value


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, indent=2, allow_nan=False) + '\n')


def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        replace(temp, path)
        sync_directory(path.parent)
    finally:
        temp.unlink(missing_ok=True)


WINDOWS = os.name == 'nt'


def replace(source, destination):
    """os.replace; on Windows retry briefly while a scanner or reader holds the target."""
    attempts = 20 if WINDOWS else 1
    for attempt in range(attempts):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.005 * (attempt + 1))


def sync_directory(path):
    if os.name == 'nt':
        return  # Windows cannot fsync directory descriptors; files are flushed before replace.
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def directory_lock(root, filename='.lock'):
    """Serialize updates sharing a directory and lock filename."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = open_no_follow(root / filename, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield root
