"""Best-effort bounded evidence checkpoints for forcibly interrupted workers."""

import json
import threading
import time

from .files import atomic_json

LIMIT = 200
MAX_BYTES = 4 * 1024 * 1024


class Journal:
    def __init__(self, folder, context):
        self.path = folder / 'checkpoint.json'
        self.context = context
        self.stopped = threading.Event()
        self.errors = []
        self.previous = None
        self.thread = threading.Thread(target=self._run, name='ca-evidence', daemon=True)

    def start(self):
        try:
            self.checkpoint()
        except Exception as error:
            self.errors.append(str(error))
        self.thread.start()

    def checkpoint(self):
        contexts = list(self.context.execution.contexts.values())
        events = [dict(event) for ctx in contexts for event in list(ctx.events)][-LIMIT:]
        checks = [dict(check) for ctx in contexts for check in list(ctx.checks)][-LIMIT:]
        trace = sorted(
            [dict(event) for ctx in contexts for event in list(ctx.client.trace)],
            key=lambda event: event['time'],
        )[-512:]
        observation = self.context.last_observation
        record = {
            'status': 'in_progress',
            'final_outcome_known': False,
            'modules': events,
            'checks': checks,
            'trace': trace,
            'observation': observation,
            'limits': {'modules': LIMIT, 'checks': LIMIT, 'trace': 512},
        }
        encoded = json.dumps(record, indent=2, allow_nan=False)
        if encoded == self.previous:
            return
        record['time'] = time.time()
        if len((json.dumps(record, indent=2, allow_nan=False) + '\n').encode()) > MAX_BYTES:
            raise ValueError(
                'Evidence checkpoint exceeds 4 MiB; final record may still be available'
            )
        atomic_json(self.path, record)
        self.previous = encoded

    def _run(self):
        while not self.stopped.wait(0.25):
            try:
                self.checkpoint()
            except Exception as error:
                if len(self.errors) < 8:
                    self.errors.append(str(error))

    def close(self):
        self.stopped.set()
        if self.thread.ident is not None:
            self.thread.join()
        try:
            self.checkpoint()
        except Exception as error:
            self.errors.append(str(error))


def partial_result(folder):
    """Read only our latest bounded checkpoint; never infer completion from it."""
    path = folder / 'checkpoint.json'
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        with path.open('rb') as stream:
            encoded = stream.read(MAX_BYTES + 1)
        if len(encoded) > MAX_BYTES:
            raise ValueError('Checkpoint exceeds evidence limit')
        checkpoint = json.loads(encoded)
        json.dumps(checkpoint, allow_nan=False)
        if not isinstance(checkpoint, dict) or checkpoint.get('final_outcome_known') is not False:
            raise ValueError('Invalid evidence checkpoint')
        if any(
            not isinstance(checkpoint.get(key, []), list)
            or any(not isinstance(event, dict) for event in checkpoint.get(key, []))
            for key in ('modules', 'checks', 'trace')
        ) or (
            checkpoint.get('observation') is not None
            and not isinstance(checkpoint['observation'], dict)
        ):
            raise ValueError('Invalid checkpoint evidence structure')
        return {
            **{
                key: checkpoint[key]
                for key in ('modules', 'checks', 'trace', 'observation')
                if key in checkpoint
            },
            'checkpoint_time': checkpoint.get('time'),
            'partial_evidence': True,
        }
    except (OSError, ValueError, RecursionError) as error:
        return {'checkpoint_error': str(error), 'partial_evidence': True}
