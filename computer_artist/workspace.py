"""Window names, layouts, and evidence locations for the local workspace."""

import json
import os
from pathlib import Path

from .files import atomic_json, directory_lock, key
from .fragments import FragmentStore


class Workspace:
    def __init__(self, root=None, output=None, run_folder=None):
        from .config import default_output_dir, default_window_dir

        self.root = Path(root or os.environ.get('CA_WINDOW_DIR') or default_window_dir()).resolve()
        self.output = Path(
            output or os.environ.get('CA_OUTPUT_DIR') or default_output_dir(self.root)
        ).resolve()
        self.run_folder = Path(run_folder) if run_folder else None
        self.fragments = FragmentStore(self.root)

    def locked(self):
        return directory_lock(self.root)

    def layout(self, identity):
        return self.root / 'layout' / self.label(identity)

    def names(self):
        path = self.root / 'names.json'
        if not path.exists():
            return {}
        names = json.loads(path.read_text())
        if not isinstance(names, dict) or any(
            key(name) != name or key(identity) != identity for name, identity in names.items()
        ):
            raise ValueError('Invalid window name registry')
        return names

    def resolve_window(self, identity):
        identity = key(identity)
        return self.names().get(identity, identity)

    def label(self, identity):
        identity = self.resolve_window(identity)
        return next((name for name, target in self.names().items() if target == identity), identity)

    def display_windows(self, windows):
        aliases = {identity: name for name, identity in self.names().items()}
        return [
            {**window, **({'name': aliases[window['id']]} if window['id'] in aliases else {})}
            for window in windows
        ]

    def assign(self, identity, name, windows):
        identity, name = key(identity), key(name)
        if not any(window['id'] == identity for window in windows):
            raise ValueError(f'Window {identity!r} is not open; run ca windows')
        if any(window['id'] == name for window in windows):
            raise ValueError('A window name cannot be a live window ID')
        with self.locked():
            names = self.names()
            old = next((label for label, target in names.items() if target == identity), None)
            current = names.get(name)
            if (
                current
                and current != identity
                and any(window['id'] == current for window in windows)
            ):
                raise ValueError(f'Name {name!r} already belongs to an open window')
            source = self.root / 'layout' / (old or identity)
            destination = self.root / 'layout' / name
            prior_layout = destination / 'window.json'
            prior_window = json.loads(prior_layout.read_text()) if prior_layout.is_file() else None
            revalidate = current is not None and current != identity and destination.exists()
            if source != destination and source.exists() and destination.exists():
                raise ValueError(
                    f'Both {source} and {destination} exist; move or archive one before naming'
                )
            if source != destination and source.exists():
                source.rename(destination)
            if old and old != name:
                names.pop(old)
            names[name] = identity
            atomic_json(self.root / 'names.json', names)
        selected = next(w for w in windows if w['id'] == identity)
        return {
            'name': name,
            'window': identity,
            'title': selected.get('title'),
            'previous_window': current if current != identity else None,
            'layout': str(destination),
            'layout_revalidation_required': revalidate,
            'previous_layout_title': prior_window.get('title')
            if isinstance(prior_window, dict)
            else None,
        }
