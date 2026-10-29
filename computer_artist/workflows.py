"""Durable, local workflow memories; retrieval never dispatches input."""

import ast
import hashlib
import json
import re
import time
from pathlib import Path

from .contracts import contract
from .files import atomic_json, directory_lock, key
from .records import inspect_run


class WorkflowStore:
    def __init__(self, root):
        self.root = Path(root)
        self.scope = self.root / 'workflows'

    def path(self, name):
        if self.scope.is_symlink():
            raise ValueError('Workflow library cannot be a symlink')
        path = self.scope / (key(name) + '.json')
        if path.is_symlink():
            raise ValueError('Workflow cannot be a symlink')
        return path

    def load(self, name):
        with directory_lock(self.root):
            return self._load(name)

    def _load(self, name):
        data = json.loads(self.path(name).read_text())
        if (
            not isinstance(data, dict)
            or data.get('name') != key(name)
            or any(not isinstance(data.get(k), str) for k in ('task', 'app', 'notes'))
            or not isinstance(data.get('history'), list)
        ):
            raise ValueError('Invalid workflow memory')
        source = data.get('source')
        if source is not None:
            if not isinstance(source, str) or digest(source) != data.get('sha256'):
                raise ValueError('Workflow source changed; learn the revised successful run')
            contract(source)
        return data

    def learn(self, name, run, task, app, notes, workspace, *, update=False):
        if not task.strip() or not app.strip():
            raise ValueError('Task and application must be nonempty')
        dossier = inspect_run(workspace.output, run)
        if not dossier['managed'] or dossier['lifecycle'] != 'completed' or dossier['issues']:
            raise ValueError('Run evidence must be complete and readable')
        request, result = dossier['request'] or {}, dossier['result'] or {}
        source = request.get('source')
        if request.get('module'):
            _, source = workspace.fragments.load(request['module'], request.get('version'))
        info = contract(source) if isinstance(source, str) else None
        window = result.get('initial_window') or {}
        identity = {
            k: window.get(k)
            for k in ('title', 'width', 'height', 'app_id', 'resource_class', 'class')
        }
        frozen_id = bool(
            source
            and request.get('window')
            and any(
                isinstance(node, ast.Constant) and node.value == request['window']
                for node in ast.walk(ast.parse(source))
            )
        )
        executable = (
            result.get('ok') is True
            and result.get('status') == 'verified'
            and result.get('final_outcome_known') is True
            and info is not None
            and bool(identity['title'])
            and any(identity[k] for k in ('app_id', 'resource_class', 'class'))
            and all(identity[k] is not None for k in ('width', 'height'))
            and not frozen_id
        )
        data = {
            'name': key(name),
            'task': task,
            'app': app,
            'notes': notes,
            'created': time.time(),
            'updated': time.time(),
            'source': source,
            'sha256': digest(source) if source else None,
            'contract': info,
            'request': {
                k: v
                for k, v in request.items()
                if k not in ('socket', 'window', 'window_root', 'output_root', 'source')
            },
            'provenance': {
                'run_id': run,
                'status': result.get('status'),
                'duration': result.get('duration'),
                'checks': result.get('checks', []),
                'result': result.get('result'),
            },
            'window': identity,
            'executable': executable,
            'history': [],
        }
        with directory_lock(self.root):
            path = self.path(name)
            if path.exists():
                if not update:
                    raise ValueError('Memory already exists; use learn --update to revise it')
                previous = self._load(name)
                data['created'] = previous['created']
                data['history'] = previous['history']
                data['revision'] = previous.get('revision', 1) + 1
            elif update:
                raise ValueError('Memory does not exist; omit --update to create it')
            else:
                data['revision'] = 1
            atomic_json(path, data)
        return data

    def list(self):
        with directory_lock(self.root):
            if self.scope.is_symlink():
                raise ValueError('Workflow library cannot be a symlink')
            if not self.scope.exists():
                return []
            return [summary(self._load(p.stem)) for p in sorted(self.scope.glob('*.json'))]

    def search(self, query, app=None):
        words = set(re.findall(r'\w+', query.casefold()))
        found = []
        for item in self.list():
            if app and app.casefold() not in item['app'].casefold():
                continue
            text = ' '.join(
                str(item.get(k, '')) for k in ('name', 'task', 'app', 'notes')
            ).casefold()
            tokens = set(re.findall(r'\w+', text))
            score = len(words & tokens)
            if score:
                found.append((score, item))
        return [
            {'score': score, **item}
            for score, item in sorted(found, key=lambda x: (-x[0], x[1]['name']))
        ]

    def forget(self, name):
        with directory_lock(self.root):
            self.path(name).unlink()
        return {'name': name, 'forgotten': True}

    def record(self, name, result):
        with directory_lock(self.root):
            data = self._load(name)
            data['history'].append(
                {k: result.get(k) for k in ('run_id', 'ok', 'status', 'duration')}
            )
            data['history'] = data['history'][-20:]
            atomic_json(self.path(name), data)


def digest(source):
    return hashlib.sha256(source.encode('utf-8')).hexdigest()


def summary(data):
    """Keep retrieval small; show supplies the full procedure only for a chosen match."""
    return {k: data[k] for k in ('name', 'task', 'app', 'notes', 'executable', 'revision')}
