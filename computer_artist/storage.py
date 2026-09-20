"""Bounded, explicitly managed run directories. Never adopts arbitrary files."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import time
import uuid

from .fragments import atomic_json, key

MARKER = '.ca-run.json'


def run_name():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d_%H-%M-%S-%fZ') + '-' + uuid.uuid4().hex[:6]


def limits():
    count = int(os.environ.get('CA_RUN_LIMIT', '5'))
    size = int(os.environ.get('CA_RUN_MAX_MB', '256')) * 1024 * 1024
    if count < 1 or size < 1:
        raise ValueError('CA_RUN_LIMIT and CA_RUN_MAX_MB must be positive integers')
    return count, size


@contextmanager
def locked(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('Storage root cannot be a symlink')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root / '.retention.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield root
    finally:
        os.close(fd)


def size_of(folder):
    total = 0
    for base, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base)/d).is_symlink()]
        for name in files:
            p = Path(base)/name
            if not p.is_symlink():
                try: total += p.stat().st_size
                except FileNotFoundError: pass
    return total


def _entries(root):
    for folder in root.iterdir():
        if folder.is_symlink() or not folder.is_dir(): continue
        marker = folder / MARKER
        if marker.is_symlink() or not marker.is_file(): continue
        try:
            info = json.loads(marker.read_text())
            if info.get('format') != 1 or not isinstance(info['created'], (int, float)): continue
        except (ValueError, KeyError, OSError): continue
        fd = os.open(folder/'.active.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        active = False
        try:
            try: fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: active = True
        finally: os.close(fd)
        yield {'id': folder.name, 'created': info['created'], 'active': active,
               'preserved': info.get('preserved', False), 'bytes': size_of(folder),
               'status': 'active' if active else 'completed' if info.get('completed') else 'abandoned'}


def inspect(root):
    with locked(root) as root:
        entries = sorted(_entries(root), key=lambda e: (e['created'], e['id']))
        return {'root': str(root), 'runs': entries, 'bytes': sum(e['bytes'] for e in entries)}


def cleanup(root, *, dry_run=False, count=None, max_bytes=None, protect=()):
    default_count, default_bytes = limits()
    count = default_count if count is None else count
    max_bytes = default_bytes if max_bytes is None else max_bytes
    if count < 1 or max_bytes < 1: raise ValueError('Retention limits must be positive')
    with locked(root) as root:
        entries = sorted(_entries(root), key=lambda e: (e['created'], e['id']))
        eligible = [e for e in entries if not e['active'] and not e['preserved']]
        total = sum(e['bytes'] for e in entries)
        deleted = []
        # Keep the newest result available even if it alone exceeds the byte cap.
        remaining = sum(not e['preserved'] for e in entries)
        for entry in eligible[:-1]:
            if entry['id'] in protect: continue
            if remaining <= count and total <= max_bytes: break
            if not dry_run: shutil.rmtree(root / entry['id'])
            deleted.append(entry['id'])
            total -= entry['bytes']; remaining -= 1
        return {'removed' if not dry_run else 'would_remove': deleted, 'remaining_bytes': total,
                'over_budget': total > max_bytes, 'limit': count, 'max_bytes': max_bytes}


def preserve(root, identity, value=True):
    with locked(root) as root:
        folder = root / key(identity)
        if folder.is_symlink() or (folder/MARKER).is_symlink(): raise ValueError('Not a managed run')
        info = json.loads((folder/MARKER).read_text())
        if info.get('format') != 1: raise ValueError('Not a managed run')
        info['preserved'] = value
        atomic_json(folder/MARKER, info)


@contextmanager
def managed_run(root, identity=None):
    limits()  # Validate policy before starting work.
    with locked(root) as root:
        folder = root / key(identity or run_name())
        folder.mkdir(mode=0o700)
        fd = os.open(folder/'.active.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(fd, fcntl.LOCK_SH)
        atomic_json(folder/MARKER, {'format': 1, 'created': time.time(), 'completed': False})
    try:
        cleanup(root, protect=(folder.name,))
        yield folder
    finally:
        try:
            with locked(root):
                info = json.loads((folder/MARKER).read_text())
                info['completed'] = True
                atomic_json(folder/MARKER, info)
        finally: os.close(fd)
        cleanup(root, protect=(folder.name,))


@contextmanager
def active_run(folder):
    """Keep a child worker's artifacts alive if its supervisor disappears."""
    fd = os.open(Path(folder)/'.active.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        yield
    finally:
        os.close(fd)
