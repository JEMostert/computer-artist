# CLI programs

Inspect `ca capabilities`, `ca windows` and a fresh `ca observe --window APP` image.
`ca set ID --name APP` binds an exact ID; `--title TEXT` requires a unique match.
Reopening requires explicit rebind and fresh mapping. Coordinates are logical
window-content pixels; convert screenshot pixels using the capture/window sizes.

Map stable controls: `ca target APP NAME --observation OBS --rect X Y W H`.
Use `ctx.click(target='@NAME')`; guards recheck exact ID, geometry and pixels.
After a rebind, `--revalidate` adopts an unchanged guard from a fresh observation.
Changed pixels/geometry need a new `--rect`. Map changing canvases by geometry,
not exact artwork hashes. Maps live under `window/layout/APP/`.

```bash
ca execute --window APP --deadline 15 --budget 1000 <<'PY'
def run(ctx):
    ctx.click(target='@tool')
    ctx.path([(100, 100), (180, 120)], interval=.02)
    return ctx.observe()
PY
```

Define one synchronous `run(ctx, ...)`. Fragment parameters use `int`, `float`,
`str`, `bool` annotations and literal defaults. Registration parses without running.
Literal `CONTRACT` fields: `requires` operation names; `lane` (`any`, `agent`, `host`);
`window` (`title_contains`, `min_width`, `max_width`, `min_height`, `max_height`);
`parameters` with `min`, `max`, `choices` constraints.

```bash
ca fragments create stroke <<'PY'
CONTRACT = {'requires': ['move', 'button']}
def run(ctx, x: float, y: float):
    ctx.path([(x, y), (x + 40, y + 20)])
PY
ca fragments run stroke --window APP --x 100 --y 120
ca fragments update stroke < revised.py
ca fragments history stroke
ca fragments restore stroke --version REVISION
```

Versions are immutable under `window/api-fragmants/NAME/versions/`. Removal archives
in `trash/`; restore validates without deleting newer work. Call with
`ctx.fragments.call('stroke', x=100, y=120, version=REVISION)` or
`ctx.call('stroke', {'x': 100, 'y': 120}, version=REVISION)`.

| Program operation | Use |
| --- | --- |
| `window`, `observe(since=None, region=None)` | Fresh guarded geometry, capture and optional diffs |
| `click`, `move`, `scroll(delta, axis='vertical', ...)` | `x=, y=`, `relative=(x,y)` in `[0,1)`, or `target='@NAME'` |
| `path(points, relative=False, interval=.016, until=None, observe_every=10)` | Held-button stroke, optional observed stopping predicate |
| `svg_path(data, origin=(0,0), scale=1, spacing=2, interval=.016, verify_change=False)` | Preflight every SVG stroke; pixel change is not shape verification |
| `press(chord, duration=0)`, `key_down(key)`, `key_up(key)` | Independent physical keys when supported; host uses real focus |
| `paste(text, shortcut='Shift+Insert')`, `type(text)` | Host clipboard paste, at most 8192 UTF-8 bytes |
| `focus`, `clipboard_get`, `clipboard_set(text)` | Explicit host actions |
| `sleep(seconds)`, `wait_for(conditions, timeout=5, interval=.15)` | Named callable conditions or `changed_since`, `title_contains`, `stable_for` dictionaries |
| `verify(name, passed, evidence=None)` | Boolean outcome with finite JSON evidence; failure interrupts |
| `handoff(reason)`, `related_windows()` | Release/yield and inspect same-process candidates without acquiring them |
| `agent.window(ID)`, `host.window(ID)`, `parallel()` | Shared deadlines/budgets; host requires outer `--host` |

Workers enforce hard deadlines even for CPU loops. Takeover, changed geometry,
new app windows and connection loss stop guarded input. Inspect fresh evidence and
choose an exact window in a new run; interrupted contexts cannot resume. Unknown
dialogs are candidates, not automatically safe targets.

Verify an actual save/export:

```python
before = ctx.snapshot_file(path)
ctx.press('Ctrl+S')
ctx.verify_file('saved text', path, kind='text', after=before,
                expected_text='requested text\n')
```

`verify_file` supports `kind='file'|'text'|'json'|'image'`, `expected_text`,
`expected_json`, `image_size=(w,h)`, `sha256`, byte limits, `timeout`, `stable_for`,
`interval`. Default: five-second timeout, 0.2-second stability. `after` requires
fresh output; without it an old file can pass. Reads cap at 64 MiB and images at
16 million decoded pixels across frames. Decoding alone does not verify artwork.
`ca files inspect PATH --kind image` and `files snapshot PATH` work offline.

Optional `verify(ctx, result)` returns `{check, passed, evidence?}`. Only an
invocation's own checks establish `verified`; otherwise `returned_unverified`.
A verified child does not verify the parent. Checks are program claims; choose
checks that substantiate the user's requested outcome.

`ca watch --window APP --duration 10 --changes-only` streams read-only JSONL and
keeps fifteen recent captures per window. Programs write under `ctx.output`.
`ca runs list`, `show ID`, `inspect ID`, `stop ID` provide terminal supervision.
Inspection works offline and never replays input. Partial checkpoints can miss
recent actions and never prove completion. Recent module/check lists cap at 200;
traces cap at 512. Inspect current state before retrying.

Runs rotate at five eligible runs/256 MiB (`CA_RUN_LIMIT`, `CA_RUN_MAX_MB`). Active
and preserved runs are protected. `ca storage keep ID` preserves a run; `status`,
`clean --dry-run`, `clean`, `unkeep ID` manage retention. Preservation cannot recover
pruned captures. Save final documents outside rotating output.
