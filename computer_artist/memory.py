"""Versioned, local Python modules. Registration parses code; it never executes it."""
import ast
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid


def key(value):
    value = str(value).strip('{}')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value) or value in ('.', '..'):
        raise ValueError(f'Invalid memory identifier: {value!r}')
    return value


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex)
    try:
        temp.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


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
    compile(tree, '<memory-registration>', 'exec')
    functions = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == 'run']
    if len(functions) != 1 or isinstance(functions[0], ast.AsyncFunctionDef):
        raise ValueError('Define exactly one synchronous run(ctx, ...) function')
    fn = functions[0]
    args = fn.args
    if fn.decorator_list or args.posonlyargs or args.vararg or args.kwarg:
        raise ValueError('run cannot use decorators, positional-only arguments or variadic parameters')
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
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CONTRACT' for t in node.targets):
            declaration = ast.literal_eval(node.value)
    if not isinstance(declaration, dict):
        raise ValueError('CONTRACT must be a literal dictionary')
    allowed = {'parameters', 'requires', 'window', 'description', 'lane'}
    if set(declaration) - allowed:
        raise ValueError(f'Unknown CONTRACT fields: {sorted(set(declaration)-allowed)}')
    for name, rules in declaration.get('parameters', {}).items():
        if name not in schema or not isinstance(rules, dict) or set(rules) - {'min', 'max', 'choices', 'unit', 'description'}:
            raise ValueError(f'Invalid parameter contract: {name}')
        schema[name].update(rules)
    for name, spec in schema.items():
        for bound in ('min','max'):
            if bound in spec and (spec['type'] not in ('int','float') or type(spec[bound]) not in (int,float) or not math.isfinite(spec[bound])):
                raise ValueError(f'{name}: invalid numeric bound')
        if 'min' in spec and 'max' in spec and spec['min'] > spec['max']:
            raise ValueError(f'{name}: min exceeds max')
        if 'choices' in spec and (not isinstance(spec['choices'],list) or not spec['choices']):
            raise ValueError(f'{name}: choices must be a nonempty list')
        for field in ('description','unit'):
            if field in spec and not isinstance(spec[field],str):
                raise ValueError(f'{name}: {field} must be a string')
        if 'default' in spec:
            validate_value(name, spec['default'], spec)
    requires = declaration.get('requires', [])
    if not isinstance(requires, list) or not all(isinstance(x, str) for x in requires):
        raise ValueError('requires must be a list of operation names')
    window = declaration.get('window', {})
    if not isinstance(window, dict) or set(window) - {'title_contains', 'min_width', 'min_height', 'max_width', 'max_height'}:
        raise ValueError('Unknown window precondition')
    for field, value in window.items():
        if field == 'title_contains':
            if not isinstance(value,str): raise ValueError('title_contains must be a string')
        elif type(value) not in (int,float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Window dimensions must be positive finite numbers')
    lane = declaration.get('lane','any')
    if lane not in ('any','agent','host'): raise ValueError('CONTRACT lane must be any, agent or host')
    return {'lane': lane, 'description': description or declaration.get('description') or ast.get_docstring(fn) or '',
            'parameters': schema, 'requires': requires, 'window': window,
            'has_verify': any(isinstance(n, ast.FunctionDef) and n.name == 'verify' for n in tree.body)}


def bind(manifest, values):
    schema = manifest['parameters']
    if set(values) - set(schema):
        raise ValueError(f'Unknown arguments: {sorted(set(values)-set(schema))}')
    result = {}
    for name, spec in schema.items():
        if name not in values and spec['required']:
            raise ValueError(f'Missing argument: {name}')
        result[name] = validate_value(name, values.get(name, spec.get('default')), spec)
    return result


class Memory:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get('CA_MEMORY_DIR') or Path(__file__).resolve().parents[1] / 'computer-memory').resolve()

    def scope(self, identity, app=False):
        return self.root / ('apps' if app else 'windows') / key(identity)

    def folder(self, identity, name, app=False):
        name = key(name)
        if name in ('observations','targets','window.json'):
            raise ValueError('That module name is reserved for window metadata')
        return self.scope(identity, app) / name

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def load(self, identity, name, version=None, app=False):
        folder = self.folder(identity, name, app)
        revision = folder / 'versions' / key(version) if version else folder / 'current'
        manifest = json.loads((revision / 'manifest.json').read_text())
        # Pin source to the manifest revision even if current changes concurrently.
        source = (folder / 'versions' / manifest['version'] / 'module.py').read_text()
        if hashlib.sha256(source.encode()).hexdigest() != manifest['sha256']:
            raise ValueError('Module source differs from its recorded hash')
        return manifest, source

    def write(self, identity, name, source, *, description='', update=False, origin=None, app=False):
        info = contract(source, description)
        with self.locked():
            folder = self.folder(identity, name, app)
            exists = (folder / 'current').exists()
            if exists != update:
                raise ValueError('Module already exists; use update' if exists else 'Module does not exist; use create')
            version = uuid.uuid4().hex[:16]
            manifest = dict(info, name=key(name), version=version, sha256=hashlib.sha256(source.encode()).hexdigest(),
                            created=time.time(), origin=origin, compatibility='unverified')
            revision = folder / 'versions' / version
            revision.mkdir(parents=True)
            (revision / 'module.py').write_text(source)
            atomic_json(revision / 'manifest.json', manifest)
            temporary = folder / ('.current-' + version)
            temporary.symlink_to('versions/' + version)
            os.replace(temporary, folder / 'current')
            for filename in ('module.py', 'manifest.json'):
                if not (folder / filename).is_symlink():
                    (folder / filename).symlink_to('current/' + filename)
            return manifest

    def list(self, identity, app=False):
        scope = self.scope(identity, app)
        if not scope.exists():
            return []
        result = []
        for path in sorted(scope.iterdir()):
            if path.is_dir() and (path / 'current').exists():
                info, _ = self.load(identity, path.name, app=app)
                result.append({**{k: info[k] for k in ('name', 'description', 'parameters', 'requires', 'version', 'compatibility')}, 'lane': info.get('lane','any')})
        return result

    def history(self, identity, name, app=False):
        folder = self.folder(identity, name, app) / 'versions'
        return sorted((json.loads(p.read_text()) for p in folder.glob('*/manifest.json')), key=lambda v: v['created'])

    def remove(self, identity, name, app=False):
        with self.locked():
            path = self.folder(identity, name, app)
            if not path.exists():
                raise ValueError('Module does not exist')
            archive = self.root / 'trash' / (key(name) + '-' + uuid.uuid4().hex)
            archive.parent.mkdir(exist_ok=True)
            path.rename(archive)
            return {'archived': str(archive)}

    def attach(self, target, source, *, from_app=False, name=None):
        entries = [self.load(source, name, app=from_app)[0]] if name else self.list(source, app=from_app)
        # Preflight all collisions before copying any modules.
        if any((self.folder(target, e['name']) / 'current').exists() for e in entries):
            raise ValueError('Destination has a module with the same name; nothing copied')
        copied = []
        for entry in entries:
            info, code = self.load(source, entry['name'], version=entry['version'], app=from_app)
            copied.append(self.write(target, entry['name'], code, description=info['description'],
                                     origin={'scope': 'apps' if from_app else 'windows', 'id': source, 'version': info['version']}))
        return copied

    def promote(self, identity, name, app):
        info, source = self.load(identity, name)
        return self.write(app, name, source, app=True, description=info['description'],
                          origin={'scope': 'windows', 'id': identity, 'version': info['version']})
