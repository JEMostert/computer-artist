"""One execution process; its supervisor enforces a wall-clock deadline."""

import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

from .client import ActionError, Client
from .contracts import contract
from .errors import Interrupted, YieldToAgent
from .files import atomic_json
from .journal import LIMIT, Journal
from .programs import evaluate, prepare
from .runtime import Context
from .workspace import Workspace


def interruption_evidence(ctx, spec, result):
    """Bounded fresh inspection after all input connections have been closed."""
    from .observations import Observations

    details = result.get('interruption', {}).get('details', {})
    errors = []
    observations = []
    try:
        with Client(spec['socket'], deadline=2, action_budget=20) as observer:
            windows = observer.windows()
            target = next((w for w in windows if w['id'] == ctx.window_id), None)
            candidates = ctx._related(windows, target or ctx.baseline)
            details.update(fresh_window=target, fresh_candidates=candidates)
            capture = Observations(ctx.store, observer)
            identities = [ctx.window_id] + [w['id'] for w in candidates if w.get('native')][:3]
            for identity in identities:
                try:
                    observation = capture.capture(identity)
                    observations.append(observation)
                    if identity == ctx.window_id:
                        result['observation'] = observation
                except Exception as error:
                    errors.append({'window_id': identity, 'error': str(error)})
                    if identity == ctx.window_id:
                        result['observation_error'] = str(error)
    except Exception as error:
        errors.append({'error': str(error)})
        result['observation_error'] = str(error)
    result['related_observations'] = [
        observation for observation in observations if observation['window']['id'] != ctx.window_id
    ]
    if 'interruption' in result:
        details['observations'] = observations
        details['evidence_errors'] = errors


def main(folder):
    folder = Path(folder)
    spec = json.loads((folder / 'request.json').read_text())
    result = {'ok': False, 'status': 'failed'}
    ctx = None
    journal = None
    with Client(
        spec['socket'],
        deadline=spec['deadline'],
        action_budget=spec['budget'],
        lane=spec.get('lane', 'agent'),
        window_dir=spec['window_root'],
    ) as client:
        try:
            ctx = Context(
                client, spec['window'], Workspace(spec['window_root'], spec['output_root'], folder)
            )
            journal = Journal(folder, ctx)
            journal.start()
            with redirect_stdout(sys.stderr):
                if 'module' in spec:
                    value = ctx.call(spec['module'], spec['arguments'], version=spec['version'])
                    status = ctx.last_call['status']
                else:
                    info = contract(spec['source'])
                    arguments = prepare(ctx, info, {})
                    value, status = evaluate(
                        ctx, spec['source'], arguments, filename='<ca execute>'
                    )
            result.update(ok=True, status=status, result=value)
        except BaseException as error:
            stopped = (
                next(
                    (c for c in ctx.execution.contexts.values() if c.client.failure is error),
                    None,
                )
                if ctx
                else None
            )
            if ctx and (
                (ctx.owned and isinstance(error, ActionError))
                or (stopped and stopped.owned and isinstance(error, OSError))
            ):
                stopped = stopped or ctx
                error = Interrupted(
                    f'Input connection lost or revoked: {error}',
                    code='input_connection_lost',
                    details={
                        'window_id': stopped.window_id,
                        'lane': stopped.lane,
                        'previous_window': stopped.baseline,
                        'recovery': 'Inspect fresh evidence and select an exact window in a new run',
                    },
                )
            result.update(
                error=str(error),
                error_type=type(error).__name__,
                status='yielded'
                if isinstance(error, YieldToAgent)
                else 'interrupted'
                if isinstance(error, (Interrupted, TimeoutError, KeyboardInterrupt))
                else 'failed',
            )
            if isinstance(error, Interrupted):
                result['interruption'] = error.as_dict()
        finally:
            try:
                client.release()
            except Exception:
                pass
            if ctx:
                try:
                    ctx.execution.close()
                except Exception as error:
                    result.update(ok=False, status='failed', cleanup_error=str(error))
                if journal:
                    journal.close()
                    if journal.errors:
                        result['evidence_errors'] = journal.errors
                contexts = list(ctx.execution.contexts.values())
                result.update(
                    modules=[event for c in contexts for event in c.events][-LIMIT:],
                    checks=[check for c in contexts for check in c.checks][-LIMIT:],
                    evidence_limits={'modules': LIMIT, 'checks': LIMIT, 'trace': 512},
                    observation=ctx.last_observation,
                )
                if not result['ok']:
                    details = result.get('interruption', {}).get('details', {})
                    stopped = next(
                        (
                            context
                            for context in contexts
                            if context.window_id == details.get('window_id')
                            and context.lane == details.get('lane')
                        ),
                        ctx,
                    )
                    result['observation'] = stopped.last_observation
                    interruption_evidence(stopped, spec, result)
            result['final_outcome_known'] = result['ok'] and result['status'] == 'verified'
            result['lane'] = spec.get('lane', 'agent')
            result['trace'] = (
                sorted(
                    (event for c in ctx.execution.contexts.values() for event in c.client.trace),
                    key=lambda e: e['time'],
                )[-512:]
                if ctx
                else list(client.trace)
            )
            atomic_json(folder / 'result.json', result)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    from .storage import active_run

    with active_run(sys.argv[1]):
        raise SystemExit(main(sys.argv[1]))
