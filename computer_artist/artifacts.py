"""Bounded, read-only verification of stable exported files."""

import hashlib
import io
import json
import math
import os
import re
import stat
import time
from pathlib import Path

MAX_BYTES = 64 * 1024 * 1024
MAX_PIXELS = 16_000_000
IMAGE_FORMATS = ('PNG', 'JPEG', 'WEBP', 'BMP', 'TIFF', 'GIF')
UNSET = object()


def _identity(info):
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _read(path, *, max_bytes=MAX_BYTES, check=None):
    """Read one regular file generation, refusing special files and changing data."""
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_BYTES:
        raise ValueError(f'max_bytes must be between 1 and {MAX_BYTES}')
    path = Path(os.path.abspath(Path(path).expanduser()))
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('Artifact must be a regular file, not a link or special device')
        if before.st_size > max_bytes:
            raise ValueError(f'Artifact exceeds {max_bytes} bytes')
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            if check:
                check()
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b''.join(chunks)
        if len(data) > max_bytes:
            raise ValueError(f'Artifact exceeds {max_bytes} bytes')
        after = os.fstat(fd)
        current = path.stat(follow_symlinks=False)
        if _identity(before) != _identity(after) or _identity(after) != _identity(current):
            raise ValueError('Artifact changed while being read; wait for a stable export')
        return {
            'path': str(path),
            'exists': True,
            'bytes': len(data),
            'identity': _identity(after),
            'sha256': hashlib.sha256(data).hexdigest(),
        }, data
    finally:
        os.close(fd)


def snapshot_file(path, *, max_bytes=MAX_BYTES, check=None):
    """Capture a before-action fingerprint. A missing destination is valid."""
    try:
        return _read(path, max_bytes=max_bytes, check=check)[0]
    except FileNotFoundError:
        return {'path': os.path.abspath(Path(path).expanduser()), 'exists': False}


def _strict_json(data):
    def invalid(value):
        raise ValueError(f'Non-finite JSON value: {value}')

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result

    value = json.loads(data.decode('utf-8'), parse_constant=invalid, object_pairs_hook=unique)
    json.dumps(value, allow_nan=False)
    return value


def _decode(data, kind):
    if kind == 'file':
        return {}, UNSET
    if kind == 'text':
        text = data.decode('utf-8')
        return {'encoding': 'utf-8', 'characters': len(text)}, text
    if kind == 'json':
        value = _strict_json(data)
        return {'json_type': type(value).__name__}, value
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError('Pillow is required for image checks') from error
    try:
        with Image.open(io.BytesIO(data), formats=IMAGE_FORMATS) as image:
            if image.width * image.height > MAX_PIXELS:
                raise ValueError(f'Image exceeds {MAX_PIXELS} pixels')
            frames = getattr(image, 'n_frames', 1)
            if frames * image.width * image.height > MAX_PIXELS:
                raise ValueError('Image frame decoding exceeds the pixel budget')
            info = {'image_format': image.format, 'image_size': list(image.size), 'frames': frames}
            for index in range(frames):
                image.seek(index)
                image.load()
            return info, UNSET
    except Image.DecompressionBombError as error:
        # Pillow's own decode bomb guard; report it like the pixel budget above.
        raise ValueError('image exceeds decoded pixel limit') from error


def inspect_file(path, *, kind='file', max_bytes=MAX_BYTES):
    if kind not in ('file', 'text', 'json', 'image'):
        raise ValueError('kind must be file, text, json or image')
    info, data = _read(path, max_bytes=max_bytes)
    decoded, _ = _decode(data, kind)
    return {**info, 'kind': kind, **decoded}


def wait_for_file(
    path,
    *,
    kind='file',
    after=None,
    expected_text=None,
    expected_json=UNSET,
    image_size=None,
    sha256=None,
    min_bytes=1,
    max_bytes=MAX_BYTES,
    timeout=5,
    stable_for=0.2,
    interval=0.1,
    check=None,
    sleep=time.sleep,
):
    """Return a specific successful/failed outcome; never write or retry app input."""
    path = os.path.abspath(Path(path).expanduser())
    if kind not in ('file', 'text', 'json', 'image'):
        raise ValueError('kind must be file, text, json or image')
    for name, number in [('timeout', timeout), ('stable_for', stable_for), ('interval', interval)]:
        if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
            raise ValueError(f'{name} must be positive and finite')
    if stable_for >= timeout:
        raise ValueError('stable_for must be shorter than timeout')
    if (
        type(min_bytes) is not int
        or min_bytes < 0
        or type(max_bytes) is not int
        or not min_bytes <= max_bytes <= MAX_BYTES
        or max_bytes < 1
    ):
        raise ValueError('Invalid artifact byte limits')
    if expected_text is not None and (kind != 'text' or not isinstance(expected_text, str)):
        raise ValueError('expected_text requires kind=text and a string')
    if expected_json is not UNSET:
        if kind != 'json':
            raise ValueError('expected_json requires kind=json')
        json.dumps(expected_json, allow_nan=False)
    if image_size is not None and (
        kind != 'image'
        or len(image_size) != 2
        or any(type(n) is not int or n <= 0 for n in image_size)
    ):
        raise ValueError('image_size requires kind=image and two positive integers')
    if sha256 is not None and (
        not isinstance(sha256, str) or not re.fullmatch('[0-9a-fA-F]{64}', sha256)
    ):
        raise ValueError('sha256 must contain 64 hexadecimal characters')
    if after is not None and (
        not isinstance(after, dict)
        or after.get('path') != path
        or type(after.get('exists')) is not bool
        or (
            after['exists']
            and (
                not isinstance(after.get('identity'), list)
                or len(after['identity']) != 5
                or not isinstance(after.get('sha256'), str)
            )
        )
    ):
        raise ValueError('after must be a snapshot_file result for this exact path')
    requirements = {'kind': kind, 'freshness_required': after is not None, 'min_bytes': min_bytes}
    if expected_text is not None:
        requirements['expected_text_sha256'] = hashlib.sha256(expected_text.encode()).hexdigest()
    if expected_json is not UNSET:
        requirements['expected_json'] = expected_json
    if image_size is not None:
        requirements['image_size'] = list(image_size)
    if sha256 is not None:
        requirements['sha256'] = sha256.lower()
    end = time.monotonic() + timeout
    stable_identity = None
    stable_since = None
    decoded_identity = None
    decoded, value = {}, UNSET
    last = {'path': path, 'requirements': requirements, 'reason': 'File not observed'}
    while time.monotonic() < end:
        if check:
            check()
        try:
            info, data = _read(path, max_bytes=max_bytes, check=check)
            last = {**info, 'requirements': requirements}
            if info['bytes'] < min_bytes:
                raise ValueError('Export is smaller than the required size')
            changed = (
                after is None
                or not after['exists']
                or info['identity'] != after['identity']
                or info['sha256'] != after['sha256']
            )
            last['fresh'] = changed if after is not None else None
            if not changed:
                raise ValueError(
                    'File matches the before-action snapshot; fresh output not observed'
                )
            identity = (*info['identity'], info['sha256'])
            if identity != decoded_identity:
                decoded, value = _decode(data, kind)
                decoded_identity = identity
            last.update(decoded)
            if expected_text is not None and value != expected_text:
                raise ValueError('Saved text differs from the exact expected UTF-8 text')
            if expected_json is not UNSET and json.dumps(
                value, sort_keys=True, allow_nan=False
            ) != json.dumps(expected_json, sort_keys=True, allow_nan=False):
                raise ValueError('Saved JSON differs from the expected value')
            if image_size is not None and last['image_size'] != list(image_size):
                raise ValueError('Saved image dimensions differ from the expected size')
            if sha256 is not None and info['sha256'] != sha256.lower():
                raise ValueError('Saved file hash differs from the expected SHA-256')
            now = time.monotonic()
            if identity != stable_identity:
                stable_identity, stable_since = identity, now
            if now - stable_since >= stable_for and now < end:
                if check:
                    check()
                return {'passed': True, 'evidence': {**last, 'stable_for': stable_for}}
        except TimeoutError:
            raise
        except (OSError, ValueError, UnicodeError, RecursionError) as error:
            stable_identity, stable_since = None, None
            last['reason'] = str(error)
        sleep(min(interval, max(0, end - time.monotonic())))
    return {
        'passed': False,
        'evidence': {
            **last,
            'reason': last.get('reason', 'Export did not remain stable before timeout'),
        },
    }
