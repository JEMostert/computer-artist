"""Guarded window actions and nested bounded program execution."""

import json
import math
import time

from .client import ActionError, Client, require_keyboard
from .errors import Interrupted, YieldToAgent
from .input import BUTTONS
from .journal import LIMIT
from .observations import Observations, geometry
from .observations import image_box as image_box
from .observations import rectangle as rectangle
from .programs import evaluate, prepare
from .workspace import Workspace

WRITE_MAX_CHARACTERS = 4096


class ModuleCalls:
    def __init__(self, context):
        self.context = context

    def call(self, name, *, lane=None, version=None, **values):
        target = lane if lane is not None else self.context
        if not isinstance(target, Context) or target.execution is not self.context.execution:
            raise ValueError('lane must be a window context from this execution')
        return target.call(name, values, version=version)


class Context:
    def __init__(self, client, window, store=None, *, execution=None):
        self.store = store or Workspace()
        self.client, self.window_id = client, self.store.resolve_window(window)
        self.lane = getattr(client, 'lane', 'agent')
        self.output = self.store.run_folder
        self.observations = Observations(self.store, client)
        self.fragments = ModuleCalls(self)
        self.events = []
        self.last_call = None
        self.stack = []
        self.checks = []
        self._check_scopes = []
        self.last_observation = None
        self.interrupted = False
        self.interruption = None
        self.owned = False
        self.baseline = None
        self.initial_connections = None
        self.capabilities = client.request('capabilities')
        self.refresh()
        from .lanes import Execution, Lane

        self.execution = execution or Execution(self, context_factory=Context)
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
            raise self.interruption or Interrupted(
                'Execution already interrupted; return to the planner'
            )
        self._check_limits()

    def _check_limits(self, *, connection=True):
        if connection and self.client.failure:
            if self.owned and isinstance(self.client.failure, (OSError, ActionError)):
                self._interrupt(
                    f'Input connection lost: {self.client.failure}', 'input_connection_lost'
                )
            raise self.client.failure
        shared = getattr(self.client, 'shared', None)
        if shared is not None and shared.cancelled.is_set():
            raise Interrupted('Shared execution cancelled')
        if time.monotonic() >= self.client.expires or self.client.budget <= 0:
            raise TimeoutError('Shared execution deadline or action budget exceeded')

    @staticmethod
    def _related(windows, target):
        pid = target.get('pid') if target else None
        if type(pid) is not int or pid <= 0:
            return []
        return [
            {**window, 'relation': 'same_process_candidate'}
            for window in windows
            if window['id'] != target['id'] and window.get('visible') and window.get('pid') == pid
        ]

    def related_windows(self):
        """Fresh visible same-process candidates; no dialog or ownership inference.

        Read-only discovery remains available after an interruption. Continuing
        input requires an explicitly selected window in a new execution.
        """
        self._check_limits(connection=False)
        if self.client.failure and isinstance(self.client.failure, (OSError, ActionError)):
            with Client(
                self.client.socket_path,
                shared=self.client.shared,
                window_dir=self.store.root,
            ) as observer:
                windows = observer.windows()
        else:
            windows = self.client.windows()
        target = next((w for w in windows if w['id'] == self.window_id), self.baseline)
        return self._related(windows, target)

    def _interrupt(self, reason, code, *, window=None, windows=(), kind=Interrupted):
        details = {
            'window_id': self.window_id,
            'lane': self.lane,
            'previous_window': self.baseline,
            'current_window': window,
            'previous_geometry': list(geometry(self.baseline)) if self.baseline else None,
            'current_geometry': list(geometry(window)) if window else None,
            'candidates': self._related(windows, window or self.baseline),
            'last_observation': self.last_observation,
            'recovery': 'Inspect fresh evidence and explicitly select a window in a new execution',
        }
        if code in ('input_connection_lost', 'ownership_revoked'):
            stop_reason = self.client.stop_reason() if hasattr(self.client, 'stop_reason') else None
            if stop_reason:
                details['stop_reason'] = stop_reason
                reason = f'{reason} ({stop_reason})'
        self.interrupted = True
        self.interruption = kind(reason, code=code, details=details)
        raise self.interruption

    def refresh(self):
        self.check_budget()
        try:
            windows = self.client.windows()
        except OSError as error:
            if self.owned:
                self._interrupt(f'Input connection lost: {error}', 'input_connection_lost')
            raise
        window = next((w for w in windows if w['id'] == self.window_id), None)
        if not window or not window.get('visible') or not window.get('native'):
            self._interrupt(
                'Target lost, hidden or unsupported',
                'target_unavailable',
                window=window,
                windows=windows,
            )
        if self.owned and not window.get(self.lane):
            self._interrupt(
                'Human takeover or app lease revoked',
                'ownership_revoked',
                window=window,
                windows=windows,
            )
        siblings = {w['id'] for w in self._related(windows, window)}
        if self.baseline is not None and geometry(window) != geometry(self.baseline):
            self._interrupt(
                'Window geometry changed; yield and re-observe',
                'geometry_changed',
                window=window,
                windows=windows,
            )
        if self.initial_connections is not None and siblings - self.initial_connections:
            self._interrupt(
                'New window from target application; yield to inspect it',
                'new_app_window',
                window=window,
                windows=windows,
            )
        self.initial_connections = siblings
        self.baseline = dict(window)
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
            x, y = x * window['width'], y * window['height']
        if (
            x is None
            or y is None
            or not math.isfinite(x)
            or not math.isfinite(y)
            or not (0 <= x < window['width'] and 0 <= y < window['height'])
        ):
            raise ValueError('Point is outside window content')
        return window['x'] + x, window['y'] + y

    def _own(self):
        self.refresh()
        if not self.owned:
            self.client.acquire(self.window_id)
            self.owned = True
            if self.lane == 'host' and self.capabilities.get('environment'):
                # The plugin reports a private desktop: no human uses its seat, so
                # raise the target before other environment windows occlude input.
                self.client.focus(self.window_id)
        elif not self.client.lease:
            self._interrupt('App ownership was lost', 'ownership_revoked')

    def move(self, **point):
        position = self._point(**point)
        self._own()
        self.client.move(*position)
        return {'status': 'dispatched'}

    def click(self, *, button='left', **point):
        if button not in BUTTONS:
            raise ValueError('Unknown mouse button')
        position = self._point(**point)
        self._own()
        self.client.click(*position, BUTTONS[button])
        return {'status': 'dispatched'}

    def scroll(self, delta, *, axis='vertical', **point):
        if axis not in ('vertical', 'horizontal') or not math.isfinite(delta):
            raise ValueError('Invalid scroll')
        self.move(**point)
        return self.client.scroll(delta, axis=axis)

    def path(self, points, *, relative=False, interval=0.016, until=None, observe_every=10):
        if until is not None and (
            not callable(until) or type(observe_every) is not int or observe_every < 1
        ):
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
                x, y = x * window['width'], y * window['height']
            if (
                not math.isfinite(x)
                or not math.isfinite(y)
                or not (0 <= x < window['width'] and 0 <= y < window['height'])
            ):
                raise ValueError('Path point outside window content')
            positions.append((window['x'] + x, window['y'] + y))
        if not positions:
            raise ValueError('Path cannot be empty')
        self._own()
        if until is None:
            self.client.path(positions, interval=interval)
            return {'status': 'dispatched', 'points': len(positions)}
        self.client.move(*positions[0])
        try:
            self.client.button(pressed=True)
            for index, position in enumerate(positions):
                self.refresh()
                self.client.move(*position)
                if index % observe_every == 0 or index == len(positions) - 1:
                    observation = self.observe()
                    if until(observation):
                        result = {
                            'status': 'condition_observed',
                            'points': index + 1,
                            'observation': observation['id'],
                        }
                        break
                self.sleep(interval)
            else:
                result = {
                    'status': 'dispatched',
                    'points': len(positions),
                    'condition_observed': False,
                }
            self.client.button(pressed=False)
            return result
        except BaseException:
            self.client.cancel()
            raise

    def _host_operations(self, *operations):
        if self.lane != 'host' or not set(operations) <= set(
            self.capabilities.get('operations', [])
        ):
            raise ValueError('Operation requires a host context and backend support')

    def svg_path(
        self, data, *, origin=(0, 0), scale=1, spacing=2, interval=0.016, verify_change=False
    ):
        """Draw a bounded SVG path after validating every stroke before input."""
        from .gestures import svg_path

        if not math.isfinite(interval) or interval < 0:
            raise ValueError('interval must be finite and nonnegative')
        if type(verify_change) is not bool:
            raise ValueError('verify_change must be a boolean')
        strokes = svg_path(data, origin=origin, scale=scale, spacing=spacing)
        window = self.refresh()
        for stroke in strokes:
            for x, y in stroke:
                if (
                    not math.isfinite(x)
                    or not math.isfinite(y)
                    or not (0 <= x < window['width'] and 0 <= y < window['height'])
                ):
                    raise ValueError('SVG path point outside window content')
        # Each stroke includes refreshes, ownership checks, a move and two
        # button transitions; reserve startup overhead for the first lease.
        actions = sum(len(stroke) + 5 for stroke in strokes) + 1
        if verify_change:
            actions += 8  # Reserve both complete before/after observations.
        if actions > self.client.budget:
            raise ValueError('SVG path exceeds remaining action budget')
        duration = sum(max(0, len(stroke) - 1) for stroke in strokes) * interval
        if time.monotonic() + duration >= self.client.expires:
            raise TimeoutError('SVG path duration exceeds remaining deadline')
        before = self.observe() if verify_change else None
        for stroke in strokes:
            self.path(stroke, interval=interval)
        if before:
            after = self.observe(since=before['id'])
            self.verify(
                'canvas_pixels_changed',
                after['changes']['pixels_changed'] is True,
                evidence={'before': before['id'], 'after': after['id']},
            )
        return {'status': 'dispatched', 'strokes': len(strokes), 'points': sum(map(len, strokes))}

    def clipboard_get(self):
        self._host_operations('clipboard_get')
        self.check_budget()
        return self.client.clipboard_get()

    def clipboard_set(self, text):
        self._host_operations('clipboard_set')
        self.check_budget()
        return self.client.clipboard_set(text)

    def paste(self, text, *, shortcut='Shift+Insert'):
        from .input import parse_chord

        codes = parse_chord(shortcut)
        self._host_operations('focus', 'key', 'clipboard_set')
        self.client.validate_text(text)
        self._own()
        self.client.focus(self.window_id)
        return self.client.paste(text, shortcut=codes)

    def type(self, text, *, interval=0.0):
        """Agent lane: physical keys from the live layout. Host lane: clipboard paste."""
        if self.lane == 'host':
            if interval:
                raise ValueError('Host typing pastes the whole text; interval is unsupported')
            return self.paste(text)
        return self.write(text, interval=interval)

    def write(self, text, *, interval=0.0):
        """Type text as physical keys resolved against the live keyboard layout.

        Every character is checked before the first key. Characters the layout
        cannot produce without dead keys, compose or an input method are refused;
        use explicit host paste for those. Dispatch is not an outcome; verify it.
        """
        if not isinstance(text, str) or not text or len(text) > WRITE_MAX_CHARACTERS:
            raise ValueError(f'Text must be 1–{WRITE_MAX_CHARACTERS} characters')
        if type(interval) not in (int, float) or not math.isfinite(interval) or interval < 0:
            raise ValueError('interval must be finite and nonnegative')
        self._keyboard_operations()
        if 'keymap' not in self.capabilities.get('operations', []):
            raise ValueError(
                'Connected plugin cannot report its keyboard layout; rebuild and reload it '
                '(ca doctor --build). No host fallback is permitted'
            )
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        keymap = self.client.keymap()
        characters = {**keymap['characters'], '\n': [28], '\t': [15]}
        missing = sorted({c for c in text if c not in characters})
        if missing:
            raise ValueError(
                f'Layout {keymap.get("layout_name") or keymap.get("layout")} cannot type '
                f'{missing[:20]} with physical keys; use explicit host paste instead'
            )
        chords = [characters[c] for c in text]
        if any(
            not isinstance(codes, list)
            or not codes
            or any(type(code) is not int or not 1 <= code <= 247 for code in codes)
            for codes in chords
        ):
            raise ConnectionError('Compositor sent an invalid keymap entry')
        if sum(2 * len(codes) for codes in chords) + 4 > self.client.budget:
            raise ValueError('Text exceeds remaining action budget')
        if time.monotonic() + interval * len(chords) >= self.client.expires:
            raise TimeoutError('Typing duration exceeds remaining deadline')
        self._own()
        if self.lane == 'host':
            self.client.focus(self.window_id)
        for index, codes in enumerate(chords):
            self.check_budget()
            self.client.chord(*codes)
            if interval and index < len(chords) - 1:
                self.sleep(interval)
        return {
            'status': 'dispatched',
            'characters': len(chords),
            'layout': keymap.get('layout_name') or keymap.get('layout'),
        }

    def press(self, chord, *, duration=0):
        from .input import parse_chord

        codes = parse_chord(chord)
        if not math.isfinite(duration) or duration < 0:
            raise ValueError('Key duration must be finite and nonnegative')
        self._keyboard_operations()
        self._own()
        if self.lane == 'host':
            self.client.focus(self.window_id)
        self.client.chord(*codes, duration=duration)

    def _keyboard_operations(self):
        require_keyboard(self.capabilities, self.lane)
        # Let the real controller reuse the already observed capability reply.
        if hasattr(self.client, 'require_keyboard'):
            self.client.require_keyboard(self.capabilities)

    def key_down(self, key):
        from .input import parse_chord

        codes = parse_chord(key)
        if len(codes) != 1:
            raise ValueError('key_down takes one key')
        self._keyboard_operations()
        self._own()
        if self.lane == 'host' and not self.client.held_keys:
            self.client.focus(self.window_id)
        return self.client.key(codes[0], True)

    def key_up(self, key):
        from .input import parse_chord

        codes = parse_chord(key)
        if len(codes) != 1:
            raise ValueError('key_up takes one key')
        self._keyboard_operations()
        self._own()
        return self.client.key(codes[0], False)

    def sleep(self, seconds):
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Invalid wait duration')
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.refresh()
            time.sleep(min(0.1, max(0, end - time.monotonic())))

    def wait_for(self, conditions, *, timeout=5, interval=0.15):
        """Named conditions: changed_since, title_contains, stable_for, or callable(observation)."""
        if (
            not isinstance(conditions, dict)
            or not conditions
            or timeout <= 0
            or interval <= 0
            or not math.isfinite(timeout + interval)
        ):
            raise ValueError('Supply named conditions and positive finite timeout/interval')
        # Check every branch before observation: a matching first branch must not
        # conceal a malformed condition elsewhere in the program.
        for name, predicate in conditions.items():
            if callable(predicate):
                continue
            if not isinstance(predicate, dict) or len(predicate) != 1:
                raise ValueError(f'Unknown condition {name}')
            if set(predicate) in ({'changed_since'}, {'title_contains'}):
                if not isinstance(next(iter(predicate.values())), str):
                    raise ValueError(f'Condition {name} requires a string')
            elif set(predicate) == {'stable_for'}:
                duration = predicate['stable_for']
                if (
                    type(duration) not in (int, float)
                    or not math.isfinite(duration)
                    or duration <= 0
                ):
                    raise ValueError('stable_for must be positive and finite')
            else:
                raise ValueError(f'Unknown condition {name}')
        end = min(self.client.expires, time.monotonic() + timeout)
        baselines = {
            name: self.observations.load(self.window_id, p['changed_since'])
            for name, p in conditions.items()
            if isinstance(p, dict) and set(p) == {'changed_since'}
        }
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
                    if (
                        not isinstance(duration, (int, float))
                        or not math.isfinite(duration)
                        or duration <= 0
                    ):
                        raise ValueError('stable_for must be positive and finite')
                    matched = now - stable_since >= duration
                else:
                    raise ValueError(f'Unknown condition {name}')
                if matched:
                    return {'status': 'observed', 'matches': name, 'observation': observation}
            self.sleep(min(interval, max(0, end - time.monotonic())))
        raise TimeoutError('Expected condition not observed before timeout')

    def snapshot_file(self, path, *, max_bytes=64 * 1024 * 1024):
        """Fingerprint an export destination before acting; never write it."""
        from .artifacts import snapshot_file

        self.check_budget()
        return snapshot_file(path, max_bytes=max_bytes, check=self.check_budget)

    def verify_file(self, name, path, **requirements):
        """Wait for a stable decoded output and record its specific outcome."""
        from .artifacts import wait_for_file

        if not isinstance(name, str) or not name.strip():
            raise ValueError('Verification name must be a nonempty string')
        self.check_budget()
        # Filesystem waiting is independent of app geometry after a save. Shared
        # cancellation/deadline checks continue; this method dispatches no input.
        result = wait_for_file(path, check=self.check_budget, **requirements)
        return self.verify(name, result['passed'], evidence=result['evidence'])

    def verify(self, name, passed, *, evidence=None):
        self.check_budget()
        if not isinstance(name, str) or not name.strip():
            raise ValueError('Verification requires a nonempty check name')
        if type(passed) is not bool:
            raise ValueError('Verification must provide an explicit boolean')
        # Validate and copy evidence before recording it. Caller mutation must
        # not make an otherwise serializable execution record unwritable.
        try:
            evidence = json.loads(json.dumps(evidence, allow_nan=False))
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError('Verification evidence must be finite JSON data') from error
        check = {
            'name': name,
            'passed': passed,
            'evidence': evidence,
            'source': 'module_defined_check',
        }
        self.checks.append(check)
        del self.checks[:-LIMIT]
        if self._check_scopes:
            self._check_scopes[-1].append(check)
            del self._check_scopes[-1][:-LIMIT]
        if not passed:
            self._interrupt(f'Outcome check failed: {name}', 'outcome_check_failed')
        return check

    def yield_to_agent(self, reason):
        self.handoff(reason)

    def handoff(self, reason):
        """Release this context's lease and return control with fresh window evidence."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Handoff requires a nonempty reason')
        # Release before discovery: exhausted budgets or an unavailable backend
        # must not keep held input while a planner decides how to continue.
        self.interrupted = True
        try:
            self.client.release()
        except Exception as error:
            self._interrupt(
                f'{reason}; lease release failed: {error}',
                'handoff_release_failed',
                kind=YieldToAgent,
            )
        self.owned = False
        try:
            self._check_limits()
            windows = self.client.windows()
            window = next((w for w in windows if w['id'] == self.window_id), None)
        except Exception as error:
            self._interrupt(
                f'{reason}; fresh window discovery failed: {error}',
                'handoff_requested',
                kind=YieldToAgent,
            )
        self._interrupt(
            reason, 'handoff_requested', window=window, windows=windows, kind=YieldToAgent
        )

    def call(self, name, values, *, version=None):
        if len(self.stack) >= 16 or name in self.stack:
            raise ValueError('Recursive module call or nesting limit exceeded')
        manifest, source = self.store.fragments.load(name, version)
        arguments = prepare(self, manifest, values)
        record = {
            'module': name,
            'lane': self.lane,
            'version': manifest['version'],
            'arguments': arguments,
            'started': time.time(),
            'checks': [],
        }
        self.events.append(record)
        del self.events[:-LIMIT]
        self.stack.append(name)
        try:
            filename = str(
                self.store.fragments.folder(name) / 'versions' / manifest['version'] / 'module.py'
            )
            result, status = evaluate(
                self, source, arguments, filename=filename, module=True, checks=record['checks']
            )
            record.update(status=status, result=result)
            return result
        except BaseException as error:
            record.update(status='failed', error=str(error))
            raise
        finally:
            record['finished'] = time.time()
            self.last_call = record
            self.stack.pop()
