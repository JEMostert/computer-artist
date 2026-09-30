"""Process lifetime, hard deadlines and durable execution evidence."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .control import read_stop
from .files import atomic_json
from .journal import partial_result
from .storage import managed_run


def run_supervised(spec, *, trace=None):
    """Run a prepared request in one protected output folder."""
    with managed_run(spec['output_root']) as folder:
        return _supervise(spec, folder, trace=trace)


def _supervise(spec, folder, *, trace):
    run_id = folder.name
    atomic_json(folder / 'request.json', spec)
    started = time.monotonic()
    process = None
    failure = None
    interruption = None
    with (folder / '.active.lock').open('r') as lease, (folder / 'program.log').open('w') as log:
        fcntl.flock(lease.fileno(), fcntl.LOCK_SH)
        try:
            process = subprocess.Popen(
                [sys.executable, '-m', 'computer_artist.worker', str(folder)],
                cwd=Path(__file__).resolve().parents[1],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                pass_fds=(lease.fileno(),),
            )
            while process.poll() is None:
                stop = read_stop(folder)
                if stop is not None:
                    failure = stop['reason']
                    interruption = {'code': 'user_cancelled', 'reason': failure, 'details': stop}
                    break
                remaining = spec['deadline'] - (time.monotonic() - started)
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(process.args, spec['deadline'])
                try:
                    process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired:
                    continue
        except subprocess.TimeoutExpired:
            failure = 'Hard execution deadline exceeded'
            interruption = {'code': 'deadline_exceeded', 'reason': failure, 'details': {}}
        except KeyboardInterrupt:
            failure = 'Execution interrupted'
            interruption = {'code': 'user_cancelled', 'reason': failure, 'details': {}}
        except (OSError, ValueError) as error:
            failure = f'Execution supervision failed: {error}'
            interruption = {'code': 'supervision_failed', 'reason': failure, 'details': {}}
        finally:
            if process:
                # Kill any remaining descendants as well as a stalled worker.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
    result = partial_result(folder)
    if (folder / 'result.json').is_file():
        try:
            with (folder / 'result.json').open('rb') as stream:
                encoded = stream.read(8 * 1024 * 1024 + 1)
            if len(encoded) > 8 * 1024 * 1024:
                raise ValueError('Worker record exceeds 8 MiB')
            recorded = json.loads(encoded)
            if not isinstance(recorded, dict) or type(recorded.get('ok')) is not bool:
                raise ValueError('Worker result must include an explicit ok boolean')
            json.dumps(recorded, allow_nan=False)
            if any(
                not isinstance(recorded.get(key, []), list)
                or any(not isinstance(event, dict) for event in recorded.get(key, []))
                for key in ('modules', 'checks', 'trace')
            ):
                raise ValueError('Invalid worker evidence structure')
            result = recorded
        except (OSError, ValueError, RecursionError) as error:
            result['record_error'] = str(error)
    if failure:
        if 'status' in result:
            result['worker_status'] = result['status']
        result.update(
            ok=False,
            status='interrupted',
            error=failure,
            interruption=interruption,
            final_outcome_known=False,
        )
    elif 'ok' not in result:
        result.update(
            ok=False,
            status='failed',
            error=f'Worker exited without a valid result ({process.returncode})',
            final_outcome_known=False,
        )
    result.setdefault('final_outcome_known', result['ok'] and result.get('status') == 'verified')
    result.update(
        run_id=run_id,
        duration=round(time.monotonic() - started, 3),
        log=str(folder / 'program.log'),
        record=str(folder / 'result.json'),
    )
    atomic_json(folder / 'trace.json', result.get('trace', []))
    atomic_json(folder / 'result.json', result)
    if trace:
        atomic_json(trace, result)
    # Keep detailed events and traces on disk; default tool feedback stays compact.
    summary = {k: v for k, v in result.items() if k not in ('trace', 'modules')}
    if 'modules' in result:
        summary['modules'] = [
            {k: entry.get(k) for k in ('module', 'version', 'lane', 'status')}
            for entry in result['modules']
        ]
    return summary
