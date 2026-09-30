"""Shared contract preflight and evaluation for inline and stored programs."""

import json

from .contracts import bind
from .errors import Interrupted


def prepare(ctx, info, values):
    """Bind arguments and check live preconditions before execution is recorded."""
    arguments = bind(info, values)
    if info.get('lane', 'any') not in ('any', ctx.lane):
        raise PermissionError(f'Program requires the {info["lane"]} lane; select it explicitly')
    window = ctx.refresh()
    missing = set(info['requires']) - set(ctx.capabilities.get('operations', []))
    if missing:
        raise ValueError(f'Unsupported required operations: {sorted(missing)}')
    for field, expected in info['window'].items():
        if field == 'title_contains':
            valid = expected in window.get('title', '')
        else:
            bound, dimension = field.split('_', 1)
            valid = (
                window[dimension] >= expected if bound == 'min' else window[dimension] <= expected
            )
        if not valid:
            raise Interrupted(f'Window precondition failed: {field}={expected}')
    return arguments


def evaluate(ctx, source, arguments, *, filename, module=False, checks=None):
    """Execute a prepared program and report checks from this invocation only."""
    namespace = {
        '__name__': '__computer_artist_fragment__' if module else '__computer_artist_program__'
    }
    if module:
        namespace['__file__'] = filename
    own_checks = [] if checks is None else checks
    ctx._check_scopes.append(own_checks)
    try:
        exec(compile(source, filename, 'exec'), namespace)
        result = namespace['run'](ctx, **arguments)
        if callable(namespace.get('verify')):
            verification = namespace['verify'](ctx, result)
            if (
                not isinstance(verification, dict)
                or type(verification.get('passed')) is not bool
                or not verification.get('check')
            ):
                raise ValueError('verify(ctx, result) must return {check, passed, evidence?}')
            ctx.verify(
                verification['check'], verification['passed'], evidence=verification.get('evidence')
            )
        ctx.check_budget()
        json.dumps(result, allow_nan=False)
        return result, 'verified' if own_checks else 'returned_unverified'
    finally:
        ctx._check_scopes.pop()
