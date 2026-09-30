"""Offline execution review. Reading evidence never dispatches desktop input."""

import fcntl
import json
import math
import os
import stat
from pathlib import Path

from .files import key
from .storage import MARKER, _marker, locked

JSON_LIMIT = 8 * 1024 * 1024


def _read(path, limit, *, tail=False):
    """Read a bounded regular file, refusing every symlink component."""
    path = Path(path)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Symlink evidence is refused')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('Evidence must be a regular file')
        if info.st_size > limit:
            if not tail:
                raise ValueError(f'Evidence exceeds {limit} byte review limit')
            os.lseek(fd, -limit, os.SEEK_END)
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(limit), info.st_size > limit
    finally:
        os.close(fd)


def _json(folder, filename, issues):
    path = folder / filename
    if not path.exists() and not path.is_symlink():
        return None
    try:
        encoded, _ = _read(path, JSON_LIMIT)
        value = json.loads(
            encoded,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError('Nonfinite JSON value: ' + value)
            ),
        )
        json.dumps(value, allow_nan=False)
        if not isinstance(value, (dict, list)):
            raise ValueError('Expected a JSON object or array')
        return value
    except (OSError, ValueError, UnicodeError, RecursionError, OverflowError) as error:
        issues.append({'file': filename, 'error': str(error)})
        return None


def _record(value, filename, issues):
    """Check the fields review consumes, while retaining useful partial records."""
    if value is None:
        return None
    try:
        if not isinstance(value, dict):
            raise ValueError('Expected a JSON object')
        if 'status' in value and not isinstance(value['status'], str):
            raise ValueError('status must be a string')
        if 'ok' in value and type(value['ok']) is not bool:
            raise ValueError('ok must be a boolean')
        if 'duration' in value and (
            type(value['duration']) not in (int, float) or not math.isfinite(value['duration'])
        ):
            raise ValueError('duration must be a finite number')
        if 'interruption' in value:
            interruption = value['interruption']
            if (
                not isinstance(interruption, dict)
                or not isinstance(interruption.get('code'), str)
                or not isinstance(interruption.get('reason'), str)
                or not isinstance(interruption.get('details'), dict)
            ):
                raise ValueError('interruption must include code, reason and object details')
            for field in ('candidates', 'fresh_candidates'):
                candidates = interruption['details'].get(field, [])
                if not isinstance(candidates, list) or any(
                    not isinstance(w, dict) for w in candidates
                ):
                    raise ValueError('Interruption candidates must be an array of window objects')
        for field in ('checks', 'modules', 'trace'):
            if field not in value:
                continue
            entries = value[field]
            if not isinstance(entries, list) or any(
                not isinstance(entry, dict) for entry in entries
            ):
                raise ValueError(f'{field} must be an array of objects')
            for entry in entries:
                if 'passed' in entry and type(entry['passed']) is not bool:
                    raise ValueError(f'{field} passed values must be booleans')
        return value
    except (ValueError, OverflowError) as error:
        issues.append({'file': filename, 'error': str(error)})
        return None


def _lifecycle(folder, issues):
    if not (folder / MARKER).exists() and not (folder / MARKER).is_symlink():
        return 'unmanaged', False
    try:
        _read(folder / MARKER, 16384)
        marker = _marker(folder)
        fd = os.open(folder / '.active.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError('Run lease must be a regular file')
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                active = False
            except BlockingIOError:
                active = True
        finally:
            os.close(fd)
        return (
            'active' if active else 'completed' if marker.get('completed') else 'abandoned'
        ), marker.get('preserved', False)
    except (OSError, ValueError, RecursionError, OverflowError) as error:
        issues.append({'file': MARKER, 'error': str(error)})
        return 'invalid', False


def _inspect(folder, *, log_bytes=16384, evidence_limit=24, details=True):
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Run must be an existing ordinary directory')
    issues = []
    lifecycle, preserved = _lifecycle(folder, issues)
    request = _json(folder, 'request.json', issues)
    if request is not None and not isinstance(request, dict):
        issues.append({'file': 'request.json', 'error': 'Expected a JSON object'})
        request = None
    result = _record(_json(folder, 'result.json', issues), 'result.json', issues)
    checkpoint = (
        _record(_json(folder, 'checkpoint.json', issues), 'checkpoint.json', issues)
        if result is None
        else None
    )
    trace = _json(folder, 'trace.json', issues) if details else []
    if trace is None:
        trace = (result or checkpoint or {}).get('trace', [])
    if not isinstance(trace, list) or any(not isinstance(event, dict) for event in trace):
        issues.append({'file': 'trace.json', 'error': 'Expected an array of trace objects'})
        trace = []
    log = ''
    truncated = False
    if details and ((folder / 'program.log').exists() or (folder / 'program.log').is_symlink()):
        try:
            encoded, truncated = _read(folder / 'program.log', log_bytes, tail=True)
            log = encoded.decode('utf-8', errors='replace')
        except (OSError, ValueError) as error:
            issues.append({'file': 'program.log', 'error': str(error)})
    evidence = []
    # Scanning and rendering are bounded even when a user program creates many files.
    scanned = 0
    scan_truncated = False
    for base, dirs, files in os.walk(folder, followlinks=False) if details else []:
        scanned += 1
        if scanned > 1000:
            scan_truncated = True
            break
        dirs[:] = sorted(d for d in dirs if not (Path(base) / d).is_symlink())
        for filename in sorted(files):
            scanned += 1
            if scanned > 1000:
                scan_truncated = True
                break
            path = Path(base) / filename
            if path.suffix.lower() != '.png':
                continue
            relative = str(path.relative_to(folder))
            if path.is_symlink():
                issues.append({'file': relative, 'error': 'Symlink evidence is refused'})
                continue
            try:
                if not path.is_file():
                    continue
                evidence.append({'path': relative, 'bytes': path.stat().st_size})
            except OSError as error:
                issues.append({'file': relative, 'error': str(error)})
            if len(evidence) >= evidence_limit:
                scan_truncated = True
                break
        if scan_truncated:
            break
    if scan_truncated:
        issues.append(
            {'file': '.', 'error': 'Evidence listing bounded; additional files may exist'}
        )
    status = (
        result.get('status', 'unknown')
        if result
        else ('incomplete' if lifecycle == 'completed' else lifecycle)
    )
    return {
        'id': folder.name,
        'managed': lifecycle not in ('unmanaged', 'invalid'),
        'lifecycle': lifecycle,
        'preserved': preserved,
        'status': status,
        'request': request,
        'result': result,
        'checkpoint': checkpoint,
        'incomplete': result is None,
        'issues': issues,
        'log': log,
        'log_truncated': truncated,
        'evidence': evidence,
        'trace_summary': {
            'recorded_events': len(trace),
            'successful_input_replies': sum(
                isinstance(event, dict)
                and event.get('ok') is True
                and event.get('operation')
                in ('move', 'button', 'key', 'scroll', 'focus', 'clipboard_set')
                for event in trace
            ),
            'events': trace[-200:],
            'truncated': len(trace) > 200,
        },
        'recovery': [
            'Inspect retained frames and current application state before retrying.',
            'A dispatched action or returned program does not prove the intended outcome.',
            'Start a fresh bounded run after takeover or geometry changes; do not replay this record.',
        ],
    }


def inspect_run(output_root, run_id, *, log_bytes=16384, evidence_limit=24):
    if not 1 <= log_bytes <= 1048576 or not 1 <= evidence_limit <= 100:
        raise ValueError('Review bounds must be positive (log ≤ 1 MiB, evidence ≤ 100)')
    root = Path(output_root)
    identity = key(run_id)
    if not root.exists():
        raise FileNotFoundError(root / identity)
    with locked(root):
        return _inspect(root / identity, log_bytes=log_bytes, evidence_limit=evidence_limit)


def list_runs(output_root, window_filter=None, workspace=None, *, limit=20):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('limit must be between 1 and 1000')
    root = Path(output_root)
    if not root.exists() and not root.is_symlink():
        return {'runs': [], 'issues': []}
    if window_filter is not None and workspace is not None:
        window_filter = workspace.resolve_window(window_filter.strip('{}'))
    entries, issues = [], []
    with locked(root):
        folders = []
        for folder in root.iterdir():
            if folder.is_symlink() or not folder.is_dir():
                continue
            if not any(
                (folder / name).exists() or (folder / name).is_symlink()
                for name in (MARKER, 'request.json', 'result.json')
            ):
                continue
            folders.append(folder)
        ordered = []
        for folder in folders:
            try:
                ordered.append((folder.stat().st_mtime, folder.name, folder))
            except OSError as error:
                issues.append({'id': folder.name, 'error': str(error)})
        folders = [entry[2] for entry in sorted(ordered, reverse=True)]
        for folder in folders:
            try:
                dossier = _inspect(folder, details=False)
            except (OSError, ValueError) as error:
                issues.append({'id': folder.name, 'error': str(error)})
                entries.append(
                    {
                        'id': folder.name,
                        'status': 'unreadable',
                        'managed': False,
                        'issues': [{'file': '.', 'error': str(error)}],
                    }
                )
                if len(entries) >= limit:
                    break
                continue
            request, result = dossier['request'] or {}, dossier['result'] or {}
            if window_filter is not None and request.get('window') != window_filter:
                continue
            entries.append(
                {
                    'id': folder.name,
                    'window': request.get('window'),
                    'module': request.get('module'),
                    'version': request.get('version'),
                    'kind': request.get('kind', 'program'),
                    'status': dossier['status'],
                    'duration': result.get('duration'),
                    'lifecycle': dossier['lifecycle'],
                    'managed': dossier['managed'],
                    'preserved': dossier['preserved'],
                    'issues': dossier['issues'],
                }
            )
            if len(entries) >= limit:
                break
    return {'runs': entries, 'issues': issues}
