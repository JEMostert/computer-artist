"""Parse and bind typed Python program contracts without executing code."""

import ast
import math


def validate_value(name, value, spec):
    kind = spec['type']
    types = {'int': int, 'float': (int, float), 'str': str, 'bool': bool}
    if not isinstance(value, types[kind]) or (kind in ('int', 'float') and isinstance(value, bool)):
        raise ValueError(f'{name} must be {kind}')
    if kind in ('int', 'float'):
        if not math.isfinite(value):
            raise ValueError(f'{name} must be finite')
        if 'min' in spec and value < spec['min'] or 'max' in spec and value > spec['max']:
            raise ValueError(f'{name} outside permitted range')
    if 'choices' in spec and value not in spec['choices']:
        raise ValueError(f'{name} must be one of {spec["choices"]}')
    return value


def contract(source, description=''):
    tree = ast.parse(source)
    compile(tree, '<fragment-registration>', 'exec')
    functions = [
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'run'
    ]
    if len(functions) != 1 or isinstance(functions[0], ast.AsyncFunctionDef):
        raise ValueError('Define exactly one synchronous run(ctx, ...) function')
    fn = functions[0]
    args = fn.args
    if fn.decorator_list or args.posonlyargs or args.vararg or args.kwarg:
        raise ValueError(
            'run cannot use decorators, positional-only arguments or variadic parameters'
        )
    if not args.args or args.args[0].arg != 'ctx':
        raise ValueError('First argument must be ctx')
    params = args.args + args.kwonlyargs
    defaults = [None] * (len(args.args) - len(args.defaults)) + args.defaults + args.kw_defaults
    if defaults[0] is not None:
        raise ValueError('ctx cannot have a default')
    schema = {}
    for arg, default in zip(params[1:], defaults[1:]):
        annotation = arg.annotation
        kind = annotation.id if isinstance(annotation, ast.Name) else None
        if kind not in ('int', 'float', 'str', 'bool'):
            raise ValueError(f'{arg.arg}: annotate with int, float, str or bool')
        schema[arg.arg] = {'type': kind, 'required': default is None}
        if default is not None:
            schema[arg.arg]['default'] = ast.literal_eval(default)
    declaration = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == 'CONTRACT' for t in node.targets
        ):
            declaration = ast.literal_eval(node.value)
    if not isinstance(declaration, dict):
        raise ValueError('CONTRACT must be a literal dictionary')
    allowed = {'parameters', 'requires', 'window', 'description', 'lane'}
    if set(declaration) - allowed:
        raise ValueError(f'Unknown CONTRACT fields: {sorted(set(declaration) - allowed)}')
    parameters = declaration.get('parameters', {})
    if not isinstance(parameters, dict):
        raise ValueError('parameters must be a dictionary')
    if 'description' in declaration and not isinstance(declaration['description'], str):
        raise ValueError('description must be a string')
    for name, rules in parameters.items():
        if (
            name not in schema
            or not isinstance(rules, dict)
            or set(rules) - {'min', 'max', 'choices', 'unit', 'description'}
        ):
            raise ValueError(f'Invalid parameter contract: {name}')
        schema[name].update(rules)
    for name, spec in schema.items():
        for bound in ('min', 'max'):
            if bound in spec and (
                spec['type'] not in ('int', 'float')
                or type(spec[bound]) not in (int, float)
                or not math.isfinite(spec[bound])
            ):
                raise ValueError(f'{name}: invalid numeric bound')
        if 'min' in spec and 'max' in spec and spec['min'] > spec['max']:
            raise ValueError(f'{name}: min exceeds max')
        if 'choices' in spec and (not isinstance(spec['choices'], list) or not spec['choices']):
            raise ValueError(f'{name}: choices must be a nonempty list')
        for choice in spec.get('choices', []):
            validate_value(name, choice, {k: v for k, v in spec.items() if k != 'choices'})
        for field in ('description', 'unit'):
            if field in spec and not isinstance(spec[field], str):
                raise ValueError(f'{name}: {field} must be a string')
        if 'default' in spec:
            validate_value(name, spec['default'], spec)
    requires = declaration.get('requires', [])
    if not isinstance(requires, list) or not all(isinstance(x, str) for x in requires):
        raise ValueError('requires must be a list of operation names')
    window = declaration.get('window', {})
    if not isinstance(window, dict) or set(window) - {
        'title_contains',
        'min_width',
        'min_height',
        'max_width',
        'max_height',
    }:
        raise ValueError('Unknown window precondition')
    for field, value in window.items():
        if field == 'title_contains':
            if not isinstance(value, str):
                raise ValueError('title_contains must be a string')
        elif type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Window dimensions must be positive finite numbers')
    for dimension in ('width', 'height'):
        if window.get('min_' + dimension, 0) > window.get('max_' + dimension, math.inf):
            raise ValueError(f'Window min_{dimension} exceeds max_{dimension}')
    lane = declaration.get('lane', 'any')
    if lane not in ('any', 'agent', 'host'):
        raise ValueError('CONTRACT lane must be any, agent or host')
    return {
        'lane': lane,
        'description': description or declaration.get('description') or ast.get_docstring(fn) or '',
        'parameters': schema,
        'requires': requires,
        'window': window,
        'has_verify': any(isinstance(n, ast.FunctionDef) and n.name == 'verify' for n in tree.body),
    }


def bind(manifest, values):
    schema = manifest['parameters']
    if set(values) - set(schema):
        raise ValueError(f'Unknown arguments: {sorted(set(values) - set(schema))}')
    result = {}
    for name, spec in schema.items():
        if name not in values and spec['required']:
            raise ValueError(f'Missing argument: {name}')
        result[name] = validate_value(name, values.get(name, spec.get('default')), spec)
    return result
