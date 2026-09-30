"""Window captures, retained evidence and visually guarded target regions."""

import hashlib
import json
import math
import time
import uuid

from .errors import Interrupted
from .files import atomic_json, key
from .workspace import Workspace


def geometry(window):
    return tuple(window[k] for k in ('x', 'y', 'width', 'height'))


def rectangle(values, width, height):
    if len(values) != 4 or any(not math.isfinite(v) for v in values):
        raise ValueError('Region must contain four finite numbers: x y width height')
    x, y, w, h = values
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
        raise ValueError('Region is outside window content')
    return list(values)


def image_box(rect, window, image):
    x, y, w, h = rect
    sx, sy = image.width / window['width'], image.height / window['height']
    return (int(x * sx), int(y * sy), math.ceil((x + w) * sx), math.ceil((y + h) * sy))


class Observations:
    limit = 15

    def __init__(self, store, client):
        self.store, self.client = store, client

    def directory(self, window):
        return self.store.run_folder / 'captures' / self.store.label(window)

    def load(self, window, identity):
        from .storage import locked

        if not self.store.output.exists():
            raise ValueError('Observation expired or belongs to another window; observe again')
        with self.store.locked(), locked(self.store.output):
            return self._load(window, identity)

    def _load(self, window, identity):
        try:
            window_id = self.store.resolve_window(window)
            for label in dict.fromkeys((self.store.label(window), window_id)):
                for path in self.store.output.glob(
                    '*/captures/' + label + '/' + key(identity) + '.json'
                ):
                    record = json.loads(path.read_text())
                    if record['window']['id'] == window_id:
                        return record
            raise FileNotFoundError(identity)
        except FileNotFoundError:
            raise ValueError(
                'Observation expired or belongs to another window; observe again'
            ) from None

    def _snapshot(self, window, identity):
        """Decode evidence before another capture or retention can remove it."""
        from PIL import Image

        from .storage import locked

        if not self.store.output.exists():
            raise ValueError('Observation expired or belongs to another window; observe again')
        with self.store.locked(), locked(self.store.output):
            record = self._load(window, identity)
            try:
                with Image.open(record['full_image']) as image:
                    snapshot = image.convert('RGB')
            except FileNotFoundError:
                raise ValueError('Observation image expired; observe again') from None
            return record, snapshot

    def capture(self, identity, *, since=None, region=None):
        identity = self.store.resolve_window(identity)
        if self.store.run_folder is None:
            from .storage import managed_run

            with managed_run(self.store.output) as folder:
                store = Workspace(self.store.root, self.store.output, folder)
                observer = Observations(store, self.client)
                observer.limit = self.limit
                return observer.capture(identity, since=since, region=region)
        from PIL import Image, ImageChops

        from .storage import limits

        _, max_bytes = limits()
        window = next((w for w in self.client.windows() if w['id'] == identity), None)
        if window is None:
            raise Interrupted('Target window closed')
        if not window.get('native') or not window.get('visible'):
            raise Interrupted('Target is not a visible native Wayland window')
        previous, old = self._snapshot(identity, since) if since else (None, None)
        with self.store.locked():
            folder = self.directory(identity)
            folder.mkdir(parents=True, exist_ok=True)
            observation = uuid.uuid4().hex
            path = folder / (observation + '.png')
            try:
                capture = self.client.request('capture', window=identity, path=str(path))
                after = next((w for w in self.client.windows() if w['id'] == identity), None)
                if (
                    after is None
                    or not after.get('native')
                    or not after.get('visible')
                    or geometry(after) != geometry(window)
                ):
                    raise Interrupted(
                        'Window disappeared, became unsupported or changed geometry during capture; observe again'
                    )
                with Image.open(path) as captured:
                    image = captured.convert('RGB')
                digest = hashlib.sha256(image.tobytes()).hexdigest()
                changes = {'compared_to': since, 'pixels_changed': None, 'geometry_changed': None}
                changed_box = None
                if previous:
                    changes['geometry_changed'] = geometry(previous['window']) != geometry(window)
                    if old.size == image.size:
                        changed_box = ImageChops.difference(image, old).getbbox()
                        changes['pixels_changed'] = changed_box is not None
                    else:
                        changes['pixels_changed'] = True
                        changed_box = (0, 0, image.width, image.height)
                if region:
                    region = rectangle(region, window['width'], window['height'])
                    box = image_box(region, window, image)
                elif changed_box:
                    box = changed_box
                    sx, sy = window['width'] / image.width, window['height'] / image.height
                    left, top, right, bottom = box
                    region = [left * sx, top * sy, (right - left) * sx, (bottom - top) * sy]
                else:
                    box = None
                output = path
                if box:
                    output = folder / (observation + '-crop.png')
                    image.crop(box).save(output)
                record = {
                    'id': observation,
                    'time': time.time(),
                    'window': window,
                    'image': str(output),
                    'full_image': str(path),
                    'region': region,
                    'pixel_size': list(image.size),
                    'sha256': digest,
                    'changes': changes,
                    'sources': {
                        'image': (capture or {}).get('source', 'kwin_main_surface'),
                        'accessibility': 'unavailable',
                        'detected_controls': 'unavailable',
                    },
                    'targets': self.targets(identity),
                }
                atomic_json(folder / (observation + '.json'), record)
                atomic_json(self.store.layout(identity) / 'window.json', window)
                records = sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime)
                total = sum(
                    p.stat().st_size for p in folder.iterdir() if p.is_file() and not p.is_symlink()
                )
                remaining = len(records)
                for expired in records[:-1]:
                    if remaining <= self.limit and total <= max_bytes:
                        break
                    paths = (
                        expired,
                        expired.with_suffix('.png'),
                        folder / (expired.stem + '-crop.png'),
                    )
                    total -= sum(p.stat().st_size for p in paths if p.exists())
                    remaining -= 1
                    expired.unlink()
                    expired.with_suffix('.png').unlink(missing_ok=True)
                    (folder / (expired.stem + '-crop.png')).unlink(missing_ok=True)
                return record
            except BaseException:
                path.unlink(missing_ok=True)
                (folder / (observation + '-crop.png')).unlink(missing_ok=True)
                raise

    def targets(self, window):
        folder = self.store.layout(window) / 'targets'
        return [json.loads(p.read_text()) for p in sorted(folder.glob('*.json'))]

    def target(self, window, name, observation, rect):
        record, image = self._snapshot(window, observation)
        rect = rectangle(rect, record['window']['width'], record['window']['height'])
        patch = image.crop(image_box(rect, record['window'], image))
        target = {
            'name': key(name.lstrip('@')),
            'observation': observation,
            'window_id': record['window']['id'],
            'rect': rect,
            'geometry': list(geometry(record['window'])),
            'source': 'agent_defined_region',
            'sha256': hashlib.sha256(patch.tobytes()).hexdigest(),
        }
        with self.store.locked():
            self._validate_live_observation(window, record)
            atomic_json(self.store.layout(window) / 'targets' / (target['name'] + '.json'), target)
        return target

    def _validate_live_observation(self, window, record):
        identity = self.store.resolve_window(window)
        live = next((w for w in self.client.windows() if w['id'] == identity), None)
        if (
            record['window']['id'] != identity
            or live is None
            or not live.get('native')
            or not live.get('visible')
            or geometry(live) != geometry(record['window'])
        ):
            raise Interrupted(
                'Window identity or geometry changed; observe again before defining targets'
            )

    def revalidate(self, window, name, observation):
        """Explicitly adopt an unchanged guard for this exact live window."""
        name = key(name.lstrip('@'))
        record, image = self._snapshot(window, observation)
        with self.store.locked():
            self._validate_live_observation(window, record)
            path = self.store.layout(window) / 'targets' / (name + '.json')
            target = json.loads(path.read_text())
            rect = rectangle(target['rect'], record['window']['width'], record['window']['height'])
            if tuple(target['geometry']) != geometry(record['window']):
                raise Interrupted(
                    f'Target @{name} has changed geometry; redefine it from this observation'
                )
            patch = image.crop(image_box(rect, record['window'], image))
            if hashlib.sha256(patch.tobytes()).hexdigest() != target['sha256']:
                raise Interrupted(
                    f'Target @{name} changed visually; redefine it from this observation'
                )
            target = {**target, 'window_id': record['window']['id'], 'observation': observation}
            atomic_json(path, target)
            return target

    def resolve(self, window, name):
        name = key(name.lstrip('@'))
        target = json.loads((self.store.layout(window) / 'targets' / (name + '.json')).read_text())
        if target.get('window_id') != self.store.resolve_window(window):
            raise Interrupted(
                f'Target @{name} belongs to another window or has no recorded window identity; '
                'observe this window and redefine the target to revalidate it'
            )
        current = self.capture(window)
        current, image = self._snapshot(window, current['id'])
        if tuple(target['geometry']) != geometry(current['window']):
            raise Interrupted(
                f'Target @{name} has stale geometry; redefine it from a fresh observation'
            )
        patch = image.crop(image_box(target['rect'], current['window'], image))
        if hashlib.sha256(patch.tobytes()).hexdigest() != target['sha256']:
            raise Interrupted(
                f'Target @{name} changed visually; redefine it from a fresh observation'
            )
        x, y, w, h = target['rect']
        return (x + w / 2, y + h / 2), current
