"""One execution process; its supervisor enforces a wall-clock deadline."""
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys
import time

from .client import Client
from .fragments import WindowStore, atomic_json, contract, bind
from .runtime import Context, Interrupted, YieldToAgent


def main(folder):
    folder = Path(folder)
    spec = json.loads((folder/'request.json').read_text())
    result = {'ok': False, 'status': 'failed'}
    ctx = None
    with Client(spec['socket'],deadline=spec['deadline'],action_budget=spec['budget'],lane=spec.get('lane','agent'),window_dir=spec['window_root']) as client:
        try:
            ctx = Context(client,spec['window'],WindowStore(spec['window_root'], spec['output_root'], folder))
            with redirect_stdout(sys.stderr):
                if 'module' in spec:
                    value = ctx.call(spec['module'],spec['arguments'],version=spec['version'])
                    status = ctx.events[-1]['status'] if len(ctx.events)==1 else ctx.events[0]['status']
                else:
                    # Apply the same preconditions and verification contract to inline programs.
                    info = contract(spec['source'])
                    if info.get('lane','any') not in ('any',ctx.lane):
                        raise PermissionError('Program requires a different lane; select it explicitly')
                    for operation in info['requires']:
                        if operation not in ctx.capabilities.get('operations',[]):
                            raise ValueError(f'Unsupported required operation: {operation}')
                    window = ctx.window
                    for field, expected in info['window'].items():
                        valid = expected in window.get('title','') if field=='title_contains' else (window[field[4:]] >= expected if field.startswith('min_') else window[field[4:]] <= expected)
                        if not valid: raise Interrupted(f'Window precondition failed: {field}')
                    namespace = {'__name__':'__computer_artist_program__'}
                    exec(compile(spec['source'],'<ca execute>','exec'),namespace)
                    value = namespace['run'](ctx,**bind(info,{}))
                    if callable(namespace.get('verify')):
                        check = namespace['verify'](ctx,value)
                        ctx.verify(check['check'],check['passed'],evidence=check.get('evidence'))
                    ctx.check_budget()
                    status = 'verified' if ctx.checks else 'returned_unverified'
            json.dumps(value,allow_nan=False)
            result.update(ok=True,status=status,result=value)
        except BaseException as error:
            result.update(error=str(error),error_type=type(error).__name__,
                          status='yielded' if isinstance(error,YieldToAgent) else 'interrupted' if isinstance(error,(Interrupted,TimeoutError,KeyboardInterrupt)) else 'failed')
        finally:
            try: client.release()
            except Exception: pass
            if ctx:
                ctx.execution.close()
                contexts = list(ctx.execution.contexts.values())
                result.update(modules=[event for c in contexts for event in c.events],
                              checks=[check for c in contexts for check in c.checks],observation=ctx.last_observation)
                if not result['ok']:
                    # Best effort evidence using a fresh read-only connection after input release.
                    try:
                        from .runtime import Observations
                        with Client(spec['socket'],deadline=2,action_budget=4) as observer:
                            result['observation'] = Observations(ctx.store,observer).capture(spec['window'])
                    except Exception as error:
                        result['observation_error'] = str(error)
            result['lane'] = spec.get('lane','agent')
            result['trace'] = sorted((event for c in ctx.execution.contexts.values() for event in c.client.trace),key=lambda e:e['time']) if ctx else list(client.trace)
            atomic_json(folder/'result.json',result)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    from .storage import active_run
    with active_run(sys.argv[1]):
        raise SystemExit(main(sys.argv[1]))
