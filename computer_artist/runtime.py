"""Observation, guarded coordinates and composable bounded action programs."""
import hashlib
import json
import math
import time
import uuid

from .fragments import WindowStore, atomic_json, bind, key


class Interrupted(RuntimeError):
    pass


class YieldToAgent(Interrupted):
    pass


def geometry(window):
    return tuple(window[k] for k in ('x', 'y', 'width', 'height'))


def rectangle(values, width, height):
    if len(values) != 4 or any(not math.isfinite(v) for v in values):
        raise ValueError('Region must contain four finite numbers: x y width height')
    x, y, w, h = values
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x+w > width or y+h > height:
        raise ValueError('Region is outside window content')
    return list(values)


def image_box(rect, window, image):
    x, y, w, h = rect
    sx, sy = image.width / window['width'], image.height / window['height']
    return (int(x*sx), int(y*sy), math.ceil((x+w)*sx), math.ceil((y+h)*sy))


class Observations:
    limit = 15

    def __init__(self, store, client):
        self.store, self.client = store, client

    def directory(self, window):
        return self.store.run_folder / 'captures' / key(window)

    def load(self, window, identity):
        try:
            paths = list(self.store.output.glob('*/captures/' + key(window) + '/' + key(identity) + '.json'))
            if not paths: raise FileNotFoundError(identity)
            return json.loads(paths[0].read_text())
        except FileNotFoundError:
            raise ValueError('Observation expired or belongs to another window; observe again') from None

    def capture(self, identity, *, since=None, region=None):
        if self.store.run_folder is None:
            from .storage import managed_run
            with managed_run(self.store.output) as folder:
                store = WindowStore(self.store.root, self.store.output, folder)
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
        previous = self.load(identity, since) if since else None
        with self.store.locked():
            folder = self.directory(identity)
            folder.mkdir(parents=True, exist_ok=True)
            observation = uuid.uuid4().hex
            path = folder / (observation+'.png')
            try:
                self.client.request('capture', window=identity, path=str(path))
                after = next((w for w in self.client.windows() if w['id'] == identity), None)
                if after is None or geometry(after) != geometry(window):
                    raise Interrupted('Window geometry changed during capture; observe again')
                with Image.open(path) as captured:
                    image = captured.convert('RGB')
                digest = hashlib.sha256(image.tobytes()).hexdigest()
                changes = {'compared_to': since, 'pixels_changed': None, 'geometry_changed': None}
                changed_box = None
                if previous:
                    changes['geometry_changed'] = geometry(previous['window']) != geometry(window)
                    with Image.open(previous['full_image']) as prior:
                        old = prior.convert('RGB')
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
                    sx, sy = window['width']/image.width, window['height']/image.height
                    l, t, r, b = box
                    region = [l*sx, t*sy, (r-l)*sx, (b-t)*sy]
                else:
                    box = None
                output = path
                if box:
                    output = folder / (observation+'-crop.png')
                    image.crop(box).save(output)
                record = {'id': observation, 'time': time.time(), 'window': window,
                          'image': str(output), 'full_image': str(path), 'region': region,
                          'pixel_size': list(image.size), 'sha256': digest, 'changes': changes,
                          'sources': {'image': 'kwin_main_surface', 'accessibility': 'unavailable', 'detected_controls': 'unavailable'},
                          'targets': self.targets(identity)}
                atomic_json(folder / (observation+'.json'), record)
                atomic_json(self.store.layout(identity) / 'window.json', window)
                records = sorted(folder.glob('*.json'), key=lambda p: p.stat().st_mtime)
                total = sum(p.stat().st_size for p in folder.iterdir() if p.is_file() and not p.is_symlink())
                remaining = len(records)
                for expired in records[:-1]:
                    if remaining <= self.limit and total <= max_bytes: break
                    paths = (expired, expired.with_suffix('.png'), folder/(expired.stem+'-crop.png'))
                    total -= sum(p.stat().st_size for p in paths if p.exists())
                    remaining -= 1
                    expired.unlink()
                    expired.with_suffix('.png').unlink(missing_ok=True)
                    (folder / (expired.stem+'-crop.png')).unlink(missing_ok=True)
                return record
            except BaseException:
                path.unlink(missing_ok=True)
                (folder / (observation+'-crop.png')).unlink(missing_ok=True)
                raise

    def targets(self, window):
        folder = self.store.layout(window) / 'targets'
        return [json.loads(p.read_text()) for p in sorted(folder.glob('*.json'))]

    def target(self, window, name, observation, rect):
        from PIL import Image
        record = self.load(window, observation)
        rect = rectangle(rect, record['window']['width'], record['window']['height'])
        with Image.open(record['full_image']) as im:
            patch = im.convert('RGB').crop(image_box(rect, record['window'], im))
        target = {'name': key(name.lstrip('@')), 'observation': observation, 'rect': rect,
                  'geometry': list(geometry(record['window'])),
                  'source': 'agent_defined_region', 'sha256': hashlib.sha256(patch.tobytes()).hexdigest()}
        atomic_json(self.store.layout(window) / 'targets' / (target['name']+'.json'), target)
        return target

    def resolve(self, window, name):
        from PIL import Image
        name = key(name.lstrip('@'))
        target = json.loads((self.store.layout(window) / 'targets' / (name+'.json')).read_text())
        current = self.capture(window)
        if tuple(target['geometry']) != geometry(current['window']):
            raise Interrupted(f'Target @{name} has stale geometry; redefine it from a fresh observation')
        with Image.open(current['full_image']) as im:
            patch = im.convert('RGB').crop(image_box(target['rect'], current['window'], im))
        if hashlib.sha256(patch.tobytes()).hexdigest() != target['sha256']:
            raise Interrupted(f'Target @{name} changed visually; redefine it from a fresh observation')
        x, y, w, h = target['rect']
        return (x+w/2, y+h/2), current


class ModuleCalls:
    def __init__(self, context):
        self.context = context

    def call(self, name, *, lane=None, **values):
        target = lane if lane is not None else self.context
        if not isinstance(target, Context) or target.execution is not self.context.execution:
            raise ValueError('lane must be a window context from this execution')
        return target.call(name, values)


class Context:
    def __init__(self, client, window, store=None, *, execution=None):
        self.client, self.window_id = client, key(window)
        self.lane = getattr(client, "lane", "agent")
        self.store = store or WindowStore()
        self.output = self.store.run_folder
        self.observations = Observations(self.store, client)
        self.fragments = ModuleCalls(self)
        self.events = []
        self.stack = []
        self.checks = []
        self.last_observation = None
        self.interrupted = False
        self.owned = False
        self.baseline = None
        self.initial_connections = None
        self.capabilities = client.request('capabilities')
        self.refresh()
        from .lanes import Execution, Lane
        self.execution = execution or Execution(self)
        self.agent = Lane(self.execution, 'agent')
        self.host = Lane(self.execution, 'host')

    def parallel(self):
        from .lanes import Parallel
        return Parallel(self.execution)

    def focus(self):
        if self.lane != 'host':
            raise PermissionError('Desktop focus changes require the host lane')
        self._own()
        self.client.focus(self.window_id)
        return {'status': 'dispatched', 'lane': 'host'}

    def check_budget(self):
        if self.interrupted:
            raise Interrupted('Execution already interrupted; return to the planner')
        if self.client.failure:
            raise self.client.failure
        if time.monotonic() >= self.client.expires or self.client.budget <= 0:
            raise TimeoutError('Shared execution deadline or action budget exceeded')

    def refresh(self):
        self.check_budget()
        windows = self.client.windows()
        window = next((w for w in windows if w['id'] == self.window_id), None)
        if not window or not window.get('visible') or not window.get('native'):
            self.interrupted = True
            raise Interrupted('Target lost, hidden or unsupported')
        if self.owned and not window.get(self.lane):
            self.interrupted = True
            raise Interrupted('Human takeover or app lease revoked')
        siblings = {w['id'] for w in windows if w.get('pid') == window.get('pid')}
        if self.baseline is not None and geometry(window) != geometry(self.baseline):
            self.interrupted = True
            raise Interrupted('Window geometry changed; yield and re-observe')
        if self.initial_connections is not None and siblings - self.initial_connections:
            self.interrupted = True
            raise Interrupted('New window from target application; yield to inspect it')
        self.initial_connections = siblings
        self.baseline = window
        return window

    @property
    def window(self):
        return self.refresh()

    def observe(self, *, since=None, region=None):
        self.refresh()
        record = self.observations.capture(self.window_id, since=since, region=region)
        self.last_observation = record
        return record

    def target(self, name, *, observation, rect):
        return self.observations.target(self.window_id, name, observation, rect)

    def _point(self, *, target=None, relative=None, x=None, y=None):
        window = self.refresh()
        if sum((target is not None, relative is not None, x is not None or y is not None)) != 1:
            raise ValueError('Provide target, relative=(x,y), or x= and y=')
        if target is not None:
            (x, y), self.last_observation = self.observations.resolve(self.window_id, target)
            window = self.refresh()
        elif relative is not None:
            x, y = relative
            if not (0 <= x < 1 and 0 <= y < 1):
                raise ValueError('Relative coordinates must be in [0, 1)')
            x, y = x*window['width'], y*window['height']
        if x is None or y is None or not math.isfinite(x) or not math.isfinite(y) or not (0 <= x < window['width'] and 0 <= y < window['height']):
            raise ValueError('Point is outside window content')
        return window['x']+x, window['y']+y

    def _own(self):
        self.refresh()
        if not self.owned:
            self.client.acquire(self.window_id)
            self.owned = True
        elif not self.client.lease:
            raise Interrupted('App ownership was lost')

    def move(self, **point):
        position = self._point(**point)
        self._own()
        self.client.move(*position)
        return {'status': 'dispatched'}

    def click(self, *, button='left', **point):
        buttons = {'left': 272, 'right': 273, 'middle': 274}
        if button not in buttons:
            raise ValueError('Unknown mouse button')
        position = self._point(**point)
        self._own()
        self.client.click(*position, buttons[button])
        return {'status': 'dispatched'}

    def scroll(self, delta, *, axis='vertical', **point):
        if axis not in ('vertical', 'horizontal') or not math.isfinite(delta):
            raise ValueError('Invalid scroll')
        self.move(**point)
        return self.client.scroll(delta, axis=axis)

    def path(self, points, *, relative=False, interval=.016, until=None, observe_every=10):
        if until is not None and (not callable(until) or type(observe_every) is not int or observe_every < 1):
            raise ValueError('until must be callable and observe_every must be a positive integer')
        if not math.isfinite(interval) or interval < 0:
            raise ValueError('interval must be finite and nonnegative')
        # Validate all coordinates before pressing a button; bound caller allocation.
        positions = []
        window = self.refresh()
        for x, y in points:
            if len(positions) >= min(self.client.budget, 20000):
                raise ValueError('Path exceeds remaining action budget')
            if relative:
                x, y = x*window['width'], y*window['height']
            if not math.isfinite(x) or not math.isfinite(y) or not (0 <= x < window['width'] and 0 <= y < window['height']):
                raise ValueError('Path point outside window content')
            positions.append((window['x']+x, window['y']+y))
        if not positions:
            raise ValueError('Path cannot be empty')
        self._own()
        if until is None:
            self.client.path(positions, interval=interval)
            return {'status': 'dispatched', 'points': len(positions)}
        self.client.move(*positions[0])
        self.client.button(pressed=True)
        try:
            for index, position in enumerate(positions):
                self.refresh()
                self.client.move(*position)
                if index % observe_every == 0 or index == len(positions)-1:
                    observation = self.observe()
                    if until(observation):
                        return {'status': 'condition_observed', 'points': index+1, 'observation': observation['id']}
                self.sleep(interval)
            return {'status': 'dispatched', 'points': len(positions), 'condition_observed': False}
        finally:
            self.client.button(pressed=False)

    def _host_operations(self, *operations):
        if self.lane != 'host' or not set(operations) <= set(self.capabilities.get('operations', [])):
            raise ValueError('Operation requires a host context and backend support')

    def clipboard_get(self):
        self._host_operations('clipboard_get')
        self.check_budget()
        return self.client.clipboard_get()

    def clipboard_set(self, text):
        self._host_operations('clipboard_set')
        self.check_budget()
        return self.client.clipboard_set(text)

    def paste(self, text, *, shortcut='Shift+Insert'):
        from .cli import chord
        codes = chord(shortcut)
        self._host_operations('focus','key','clipboard_set')
        self.client.validate_text(text)
        self._own()
        self.client.focus(self.window_id)
        return self.client.paste(text, shortcut=codes)

    def type(self, text):
        return self.paste(text)

    def press(self, chord, *, duration=0):
        from .cli import chord as parse_chord
        codes = parse_chord(chord)
        self._host_operations('focus','key')
        self._own()
        self.client.focus(self.window_id)
        self.client.chord(*codes, duration=duration)

    def key_down(self, key):
        from .cli import chord
        codes = chord(key)
        if len(codes) != 1: raise ValueError('key_down takes one key')
        self._host_operations('focus','key')
        self._own()
        if not self.client.held_keys: self.client.focus(self.window_id)
        return self.client.key(codes[0], True)

    def key_up(self, key):
        from .cli import chord
        codes = chord(key)
        if len(codes) != 1: raise ValueError('key_up takes one key')
        self._host_operations('key')
        self._own()
        return self.client.key(codes[0], False)

    def sleep(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Invalid wait duration')
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            self.refresh()
            time.sleep(min(.1, max(0, end-time.monotonic())))

    def wait_for(self, conditions, *, timeout=5, interval=.15):
        """Named conditions: changed_since, title_contains, stable_for, or callable(observation)."""
        if not isinstance(conditions, dict) or not conditions or timeout <= 0 or interval <= 0 or not math.isfinite(timeout+interval):
            raise ValueError('Supply named conditions and positive finite timeout/interval')
        end = min(self.client.expires, time.monotonic()+timeout)
        baselines = {name: self.observations.load(self.window_id, p['changed_since'])
                     for name, p in conditions.items() if isinstance(p, dict) and set(p)=={'changed_since'}}
        previous_hash = None
        stable_since = time.monotonic()
        while time.monotonic() < end:
            observation = self.observe()
            now = time.monotonic()
            if observation['sha256'] != previous_hash:
                stable_since = now
                previous_hash = observation['sha256']
            for name, predicate in conditions.items():
                if callable(predicate):
                    matched = bool(predicate(observation))
                elif isinstance(predicate, dict) and set(predicate) == {'changed_since'}:
                    prior = baselines[name]
                    matched = prior['sha256'] != observation['sha256']
                elif isinstance(predicate, dict) and set(predicate) == {'title_contains'}:
                    matched = predicate['title_contains'] in observation['window'].get('title', '')
                elif isinstance(predicate, dict) and set(predicate) == {'stable_for'}:
                    duration = predicate['stable_for']
                    if not isinstance(duration, (int,float)) or not math.isfinite(duration) or duration <= 0:
                        raise ValueError('stable_for must be positive and finite')
                    matched = now-stable_since >= duration
                else:
                    raise ValueError(f'Unknown condition {name}')
                if matched:
                    return {'status': 'observed', 'matches': name, 'observation': observation}
            self.sleep(min(interval, max(0, end-time.monotonic())))
        raise TimeoutError('Expected condition not observed before timeout')

    def verify(self, name, passed, *, evidence=None):
        if type(passed) is not bool:
            raise ValueError('Verification must provide an explicit boolean')
        check = {'name': str(name), 'passed': passed, 'evidence': evidence, 'source': 'module_defined_check'}
        self.checks.append(check)
        if not passed:
            self.interrupted = True
            raise Interrupted(f'Outcome check failed: {name}')
        return check

    def yield_to_agent(self, reason):
        self.interrupted = True
        raise YieldToAgent(reason)

    def call(self, name, values, *, version=None):
        if len(self.stack) >= 16 or name in self.stack:
            raise ValueError('Recursive module call or nesting limit exceeded')
        manifest, source = self.store.load(name, version)
        arguments = bind(manifest, values)
        if manifest.get('lane','any') not in ('any',self.lane):
            raise PermissionError(f"Module requires the {manifest['lane']} lane; select it explicitly")
        window = self.refresh()
        missing = set(manifest['requires']) - set(self.capabilities.get('operations', []))
        if missing:
            raise ValueError(f'Unsupported required operations: {sorted(missing)}')
        for field, expected in manifest['window'].items():
            if field == 'title_contains':
                valid = expected in window.get('title', '')
            else:
                bound, dimension = field.split('_', 1)
                valid = window[dimension] >= expected if bound == 'min' else window[dimension] <= expected
            if not valid:
                raise Interrupted(f'Window precondition failed: {field}={expected}')
        record = {'module': name, 'lane': self.lane, 'version': manifest['version'], 'arguments': arguments, 'started': time.time()}
        self.events.append(record)
        self.stack.append(name)
        try:
            namespace = {'__name__': '__computer_artist_fragment__', '__file__': str(self.store.folder(name)/'versions'/manifest['version']/'module.py')}
            exec(compile(source, namespace['__file__'], 'exec'), namespace)
            before = len(self.checks)
            result = namespace['run'](self, **arguments)
            if callable(namespace.get('verify')):
                verification = namespace['verify'](self, result)
                if not isinstance(verification, dict) or type(verification.get('passed')) is not bool or not verification.get('check'):
                    raise ValueError('verify(ctx, result) must return {check, passed, evidence?}')
                self.verify(verification['check'], verification['passed'], evidence=verification.get('evidence'))
            self.check_budget()
            json.dumps(result, allow_nan=False)
            record.update(status='verified' if len(self.checks)>before else 'returned_unverified', result=result,
                          checks=self.checks[before:])
            return result
        except BaseException as error:
            record.update(status='failed', error=str(error))
            raise
        finally:
            record['finished'] = time.time()
            self.stack.pop()
