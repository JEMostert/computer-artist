"""Versioned local fragment storage; registration never executes source code."""

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from .contracts import contract
from .files import atomic_json, atomic_text, directory_lock, key, sync_directory


class FragmentStore:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def folder(self, name):
        name = key(name)
        if name == 'trash':
            raise ValueError('That name is reserved for archived fragments')
        scope = self.root / 'api-fragmants'
        if scope.is_symlink():
            raise ValueError('Fragment library cannot be a symlink')
        return scope / name

    def locked(self):
        return directory_lock(self.root)

    def load(self, name, version=None):
        if not self.root.exists():
            raise FileNotFoundError(self.root)
        with self.locked():
            return self._load(name, version)

    def _load(self, name, version=None):
        folder = self.folder(name)
        if folder.is_symlink() or (folder / 'versions').is_symlink():
            raise ValueError('Module storage cannot be a symlink')
        if version is None:
            current = folder / 'current'
            if not current.is_symlink():
                raise ValueError('Current revision must be a library revision link')
            link = os.readlink(current)
            parts = link.split('/')
            if len(parts) != 2 or parts[0] != 'versions' or key(parts[1]) != parts[1]:
                raise ValueError('Current revision points outside the fragment library')
            version = parts[1]
        version = key(version)
        revision = folder / 'versions' / version
        if revision.is_symlink():
            raise ValueError('Module revision cannot be a symlink')
        for filename in ('manifest.json', 'module.py'):
            path = revision / filename
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'Module {filename} must be a regular revision file')
        manifest = json.loads((revision / 'manifest.json').read_text())
        if (
            not isinstance(manifest, dict)
            or manifest.get('name') != key(name)
            or manifest.get('version') != version
        ):
            raise ValueError('Invalid module revision identity')
        if (
            not isinstance(manifest.get('description'), str)
            or type(manifest.get('created')) not in (int, float)
            or not 0 <= manifest['created'] <= 1e15
        ):
            raise ValueError('Invalid module revision metadata')
        source = (revision / 'module.py').read_text(encoding='utf-8')
        if hashlib.sha256(source.encode()).hexdigest() != manifest.get('sha256'):
            raise ValueError('Module source differs from its recorded hash')
        parsed = contract(source, manifest['description'])
        for field, value in parsed.items():
            # Old lane-neutral revisions may omit the field; other contract
            # metadata must agree with the immutable source before any execution.
            actual = manifest.get(field, 'any' if field == 'lane' else None)
            if json.dumps(actual, sort_keys=True, allow_nan=False) != json.dumps(
                value, sort_keys=True, allow_nan=False
            ):
                raise ValueError(f'Module manifest contract differs from source: {field}')
        return manifest, source

    def restore(self, name, version):
        """Select a validated historical revision without deleting newer work."""
        with self.locked():
            manifest, _ = self._load(name, version)
            folder = self.folder(name)
            current = folder / 'current'
            previous = os.readlink(current) if current.is_symlink() else None
            if current.exists() and not current.is_symlink():
                raise ValueError('Refusing to replace a non-link current revision')
            for filename in ('module.py', 'manifest.json'):
                alias = folder / filename
                if alias.is_symlink() and os.readlink(alias) == 'current/' + filename:
                    continue
                if alias.exists() or alias.is_symlink():
                    raise ValueError(f'Refusing to replace unexpected module alias: {alias}')
            temporary = folder / ('.restore-' + uuid.uuid4().hex)
            try:
                temporary.symlink_to('versions/' + manifest['version'])
                for filename in ('module.py', 'manifest.json'):
                    alias = folder / filename
                    if not alias.is_symlink():
                        alias.symlink_to('current/' + filename)
                os.replace(temporary, current)
                sync_directory(folder)
            finally:
                temporary.unlink(missing_ok=True)
            return {
                'name': key(name),
                'version': manifest['version'],
                'previous_version': previous.removeprefix('versions/') if previous else None,
            }

    def write(self, name, source, *, description='', update=False):
        info = contract(source, description)
        with self.locked():
            folder = self.folder(name)
            if folder.is_symlink() or (folder / 'versions').is_symlink():
                raise ValueError('Module storage cannot be a symlink')
            exists = (folder / 'current').exists() or (folder / 'current').is_symlink()
            if exists != update:
                raise ValueError(
                    'Module already exists; use update'
                    if exists
                    else 'Module does not exist; use create'
                )
            if update:
                self._load(name)
            for filename in ('module.py', 'manifest.json'):
                alias = folder / filename
                if alias.is_symlink():
                    if os.readlink(alias) != 'current/' + filename:
                        raise ValueError(f'Unexpected module alias: {alias}')
                elif alias.exists():
                    raise ValueError(f'Refusing to replace existing file: {alias}')
            version = uuid.uuid4().hex[:16]
            manifest = dict(
                info,
                name=key(name),
                version=version,
                sha256=hashlib.sha256(source.encode()).hexdigest(),
                created=time.time(),
                compatibility='unverified',
            )
            revision = folder / 'versions' / version
            revision.mkdir(parents=True)
            atomic_text(revision / 'module.py', source)
            atomic_json(revision / 'manifest.json', manifest)
            sync_directory(revision.parent)
            temporary = folder / ('.current-' + version)
            temporary.symlink_to('versions/' + version)
            for filename in ('module.py', 'manifest.json'):
                if not (folder / filename).is_symlink():
                    (folder / filename).symlink_to('current/' + filename)
            os.replace(temporary, folder / 'current')
            sync_directory(folder)
            sync_directory(folder.parent)
            sync_directory(self.root)
            return manifest

    def list(self):
        scope = self.root / 'api-fragmants'
        if not scope.exists():
            return []
        if scope.is_symlink():
            raise ValueError('Fragment library cannot be a symlink')
        result = []
        with self.locked():
            for path in sorted(scope.iterdir()):
                if path.name == 'trash' or not (path.is_dir() or path.is_symlink()):
                    continue
                try:
                    info, _ = self._load(path.name)
                    result.append(
                        {
                            **{
                                k: info[k]
                                for k in (
                                    'name',
                                    'description',
                                    'parameters',
                                    'requires',
                                    'version',
                                    'compatibility',
                                )
                            },
                            'lane': info.get('lane', 'any'),
                        }
                    )
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    SyntaxError,
                    RecursionError,
                    OverflowError,
                    TypeError,
                ) as error:
                    result.append({'name': path.name, 'status': 'invalid', 'error': str(error)})
        return result

    def history(self, name):
        if not self.root.exists():
            return []
        folder = self.folder(name) / 'versions'
        with self.locked():
            if folder.is_symlink() or folder.parent.is_symlink():
                raise ValueError('Module storage cannot be a symlink')
            if not folder.exists():
                return []
            result = []
            for path in sorted(folder.iterdir()):
                if not (path.is_dir() or path.is_symlink()):
                    continue
                try:
                    result.append(self._load(name, path.name)[0])
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    SyntaxError,
                    RecursionError,
                    OverflowError,
                    TypeError,
                ) as error:
                    result.append(
                        {
                            'name': key(name),
                            'version': path.name,
                            'status': 'invalid',
                            'error': str(error),
                        }
                    )
            return sorted(result, key=lambda value: (value.get('created', 0), value['version']))

    def remove(self, name):
        with self.locked():
            path = self.folder(name)
            if path.is_symlink():
                raise ValueError('Module storage cannot be a symlink')
            if not path.exists():
                raise ValueError('Module does not exist')
            archive = self.root / 'api-fragmants' / 'trash' / (key(name) + '-' + uuid.uuid4().hex)
            if archive.parent.is_symlink():
                raise ValueError('Archive storage cannot be a symlink')
            archive.parent.mkdir(exist_ok=True)
            path.rename(archive)
            sync_directory(archive.parent)
            sync_directory(archive.parent.parent)
            return {'archived': str(archive)}
