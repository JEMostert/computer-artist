"""Continuous read-only observation with bounded frames and streamed events."""

import json
import math
import time

from .control import check_stop
from .errors import Interrupted
from .files import atomic_json
from .observations import Observations
from .storage import managed_run
from .workspace import Workspace


def watch(client, args, emit):
    if not math.isfinite(args.duration) or not 0 < args.duration <= args.deadline:
        raise ValueError('duration must be positive and no greater than --deadline')
    if not math.isfinite(args.interval) or args.interval < 0.05:
        raise ValueError('interval must be finite and at least 0.05 seconds')
    store = Workspace(args.window_dir, args.output_dir)
    identity = store.resolve_window(args.window.strip('{}'))
    if 'capture' not in client.request('capabilities').get('operations', []):
        raise ValueError('This backend cannot capture windows')
    with managed_run(store.output) as folder:
        store = Workspace(store.root, store.output, folder)
        observer = Observations(store, client)
        atomic_json(
            folder / 'request.json',
            {
                'window': identity,
                'module': None,
                'kind': 'watch',
                'duration': args.duration,
                'interval': args.interval,
            },
        )
        started = time.monotonic()
        previous = None
        frames = emitted = 0
        result = {
            'ok': True,
            'command': 'watch',
            'status': 'observed',
            'run_id': folder.name,
            'record': str(folder / 'result.json'),
            'events': str(folder / 'events.jsonl'),
        }
        error = None
        with (folder / 'events.jsonl').open('w') as events:
            try:
                while True:
                    check_stop(folder)
                    if client.budget < 3:
                        raise TimeoutError('Observation request budget exceeded')
                    observation = observer.capture(identity, since=previous, region=args.region)
                    previous = observation['id']
                    frames += 1
                    event = {
                        'ok': True,
                        'event': 'observation',
                        'sequence': frames,
                        'run_id': folder.name,
                        **{
                            k: observation[k]
                            for k in (
                                'id',
                                'time',
                                'image',
                                'full_image',
                                'pixel_size',
                                'region',
                                'sha256',
                                'changes',
                            )
                        },
                        'window': {
                            k: observation['window'][k] for k in ('id', 'x', 'y', 'width', 'height')
                        },
                    }
                    events.write(json.dumps(event, allow_nan=False) + '\n')
                    events.flush()
                    changed = (
                        frames == 1
                        or observation['changes']['pixels_changed']
                        or observation['changes']['geometry_changed']
                    )
                    if not args.changes_only or changed:
                        emit(event)
                        emitted += 1
                    due = min(started + args.duration, time.monotonic() + args.interval)
                    while time.monotonic() < due:
                        check_stop(folder)
                        if client.failure:
                            raise client.failure
                        if time.monotonic() >= client.expires:
                            raise TimeoutError('Observation deadline or request budget exceeded')
                        time.sleep(min(0.05, max(0, due - time.monotonic())))
                    if time.monotonic() >= started + args.duration:
                        break
            except BaseException as failure:
                error = failure
                result.update(
                    ok=False,
                    status='interrupted'
                    if isinstance(failure, (KeyboardInterrupt, TimeoutError, Interrupted))
                    else 'failed',
                    error=str(failure) or type(failure).__name__,
                    error_type=type(failure).__name__,
                )
                if isinstance(failure, Interrupted):
                    result['interruption'] = failure.as_dict()
            finally:
                result.update(
                    frames=frames,
                    emitted=emitted,
                    last_observation=previous,
                    duration=round(time.monotonic() - started, 3),
                    retained_frame_limit=observer.limit,
                )
                atomic_json(folder / 'result.json', result)
                emit({**result, 'event': 'watch_finished'})
        if error:
            raise error
        return result
