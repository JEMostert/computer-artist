# Window layouts, API fragments, and programs

The fragment library stores small Python actions the agent can compose. Each action
has a typed interface, an immutable version, optional preconditions and explicit
outcome checks. It is intended for steps such as drawing a shape or selecting an
object; a module need not encode an entire task.

These commands work with a matching stock-KWin plugin build. Independent keyboard input,
XWayland, automatic visual control detection, accessibility discovery and semantic
Blender/browser adapters are **not implemented**. The runtime reports those
limits; it never silently switches to human input. A Blender cylinder module
will need an implementation compatible with the available input/app interface.
No such Blender workflow has been validated by this change.

## Create and reuse

```bash
ca windows
ca fragments create move-to-point --description "Position the agent pointer" <<'PY'
CONTRACT = {
    'requires': ['move'],
    'parameters': {
        'x': {'min': 0, 'max': .99, 'unit': 'window fraction'},
        'y': {'min': 0, 'max': .99, 'unit': 'window fraction'},
    },
}

def run(ctx, x: float, y: float):
    ctx.move(relative=(x, y))
    return {'position': [x, y]}
PY

ca fragments list
ca fragments inspect move-to-point
ca fragments run move-to-point --window WINDOW_ID --x .5 --y .4
ca fragments run move-to-point --window WINDOW_ID --args '{"x":0.7,"y":0.6}'
ca session close
```

Creation parses syntax and contracts **without running imports, defaults or the
body**. `run(ctx, ...)` parameters must use `int`, `float`, `str` or `bool` type
annotations. Defaults must be literal values. CLI booleans use `true` or `false`.
Unknown parameters and invalid values are rejected before execution. Parameters
that share names with CLI options (such as `deadline`) can be passed in `--args`.

Registration works offline in a shared fragment library. Execution selects a
live window with `--window` and checks that it
is native Wayland and visible. The human pointer and keyboard focus must be
outside the application before the first input action.

Input acquisition opens a cursor session automatically. Modules reuse it, acquire an app
only when needed, and return app ownership at run completion. Nested calls share
one context, lease, deadline and action budget. Modules never automatically close
the session; a human takeover or failure still releases input. Pure observation
and pure calculation do not acquire an application.

See [the two-lane API](HOST-LANE.md) for explicit host control, parallel tasks and
lane-aware module calls. No module silently escalates to host control.

## Compose modules and write conditional programs

```bash
ca execute --window WINDOW_ID --deadline 8 --budget 300 <<'PY'
def run(ctx):
    ctx.fragments.call('move-to-point', x=.3, y=.4)
    ctx.sleep(.2)
    ctx.fragments.call('move-to-point', x=.6, y=.4)
    return {'finished': True}
PY
```

`ca execute` reads Python defining `run(ctx)` from stdin. The same optional
`CONTRACT` and `verify` hook work for inline programs. `ca run task.py` remains the
legacy `main(client)` API; use `execute` or `fragments run` for the new context and
supervised hard deadline.

Available context methods:

| Method | Behavior |
| --- | --- |
| `ctx.window` | Fresh window metadata and logical content geometry |
| `ctx.observe(since=None, region=None)` | Capture plus state, observation ID and optional image diff/crop |
| `ctx.target(name, observation=id, rect=[x,y,w,h])` | Define a named, visually guarded region |
| `ctx.move(...)`, `ctx.click(...)` | `target='@name'`, `relative=(x,y)` or content-local `x=, y=` |
| `ctx.scroll(delta, ..., axis='vertical')` | Position pointer using the same targeting arguments, then scroll |
| `ctx.path(points, relative=False, interval=.016)` | Validate the path before pressing; draw through real input |
| `ctx.path(..., until=predicate, observe_every=10)` | Observe during a drag and stop when `predicate(observation)` returns true |
| `ctx.wait_for(conditions, timeout=5, interval=.15)` | Poll named conditions locally; return the first matching name and observation |
| `ctx.sleep(seconds)` | Wait while checking deadline, geometry and ownership |
| `ctx.fragments.call(name, **arguments)` | Invoke another module in the same window/context |
| `ctx.verify(name, boolean, evidence=...)` | Record an explicit check; stop on failure |
| `ctx.yield_to_agent(reason)` | Stop and return control with available observation evidence |
| `ctx.paste(text, shortcut='Shift+Insert')`, `ctx.type(text)` | Host only; paste Unicode text through the shared clipboard (`type` is an alias) |
| `ctx.clipboard_get()`, `ctx.clipboard_set(text)` | Host only; UTF-8 text, at most 8192 bytes; no focus change |
| `ctx.press('Ctrl+S', duration=0)` | Host only; focus the target and press a chord, optionally holding it |
| `ctx.key_down('W')`, `ctx.key_up('W')` | Host only; hold/release one physical key for app interactions |

Relative coordinates are fractions in `[0,1)` of **window content**, not of a
particular canvas or screenshot crop. Pixel coordinates are logical pixels.
Resizing interrupts a running context; the agent must re-observe and start a new
run. Moving a window also invalidates coordinates. A new window from the target
PID interrupts high-level actions; KWin handles additional ownership/grab limits.
These checks do not provide a universal modal-dialog detector.

## Observe and reference

```bash
ca observe --window WINDOW_ID
ca observe --window WINDOW_ID --since OBSERVATION_ID
ca observe --window WINDOW_ID --region 20 40 300 200
ca target WINDOW_ID add-button --observation OBSERVATION_ID --rect 20 40 80 30
```

Observations include a PNG path, full image path, image dimensions, logical
window geometry, timestamp, named targets and source labels. With `--since`,
changed pixels produce a bounding-box crop by default. `--region` overrides that
crop. Full images remain available for context. Screenshot paths are artifacts;
the calling harness must read/render the image when the model needs vision.

Named targets are **agent-defined regions**, not automatically recognized
controls. Before using one, the runtime captures again and checks the original
window geometry and exact pixels in its region. Any change refuses input.
This deliberately conservative check can reject animated controls. It does not
prove semantic identity or that the rest of the page is unchanged, and there
remains a capture-to-action race. KWin also validates the actual input target.

Only the newest 15 observations per window within a run are retained. Targets
store geometry and pixel hashes independently of their source observations. Observation IDs from other
windows cannot be used. Captures are main-surface buffers: decorations and
subsurfaces are excluded, and GPU buffers may be unreadable.

## Conditions and verification

```python
before = ctx.observe()
ctx.click(target='@add-button')
result = ctx.wait_for({
    'changed': {'changed_since': before['id']},
    'error': {'title_contains': 'Error'},
}, timeout=5)
if result['matches'] == 'error':
    ctx.yield_to_agent('Application reported an error')
```

Conditions support `changed_since`, `title_contains`, `stable_for` (seconds), or
a Python callable receiving the current observation. For example, a callable can
inspect a crop using Pillow. Polling runs in the local worker, without another
LLM invocation per iteration. Stable pixels and changed pixels are observations,
not proof that an application completed the intended task.

A module may provide a verification hook:

```python
def verify(ctx, result):
    return {
        'check': 'Specific assertion about the outcome',
        'passed': result['expected_value'] == 3,
        'evidence': result,
    }
```

Statuses distinguish `returned_unverified`, `verified`, `yielded`, `interrupted`
and `failed`. `verified` means named **module-defined checks** passed; their
quality depends on the module author. Input dispatch alone never implies success.
Nested modules can contribute checks, and the run records exactly what they
checked. Use an outer verification hook for the larger composition's outcome.

## Versions and reuse

```bash
ca fragments update move-to-point < fragment.py
ca fragments history move-to-point
ca fragments show move-to-point --version VERSION
ca fragments run move-to-point --window WINDOW_ID --version VERSION --x .4 --y .5
ca fragments remove move-to-point
```

Fragments are shared code, not owned by a window or application namespace.
Select a live window when executing; its layout supplies named targets. Check
layout and tool assumptions before reuse. Preconditions still run every time.

`CONTRACT['window']` supports `title_contains`, `min_width`, `min_height`,
`max_width`, and `max_height`. `requires` lists backend operations; `lane` can
require `agent` or `host`. Other checks can run in Python before acting.

Create refuses name collisions. Update creates an immutable version and switches
the current revision atomically. Top-level runs pin their version; nested calls
record resolved versions. Remove archives the fragment under `api-fragmants/trash/`.
Source hashes detect changes to immutable revisions.

## Records and limits

```bash
ca runs list --window WINDOW_ID
ca runs show RUN_ID
```

Default execution output omits the full input trace and detailed nested call
arguments. `runs show` reads the complete record without replaying input.
Records contain arguments, versions, returned values, checks and trace. Programs'
prints go to a separate log. `--trace PATH` exports the full result record.

`--deadline` is a wall-clock limit enforced by the supervising process, including
CPU loops; `--budget` bounds client requests shared by nested modules. Timeout or
interruption terminates the worker process group. Socket closure releases held
input, with KWin's watchdog as backup. Failure observations are best effort; a
hard-killed worker may have no final screenshot or detailed trace. A cursor
session deliberately remains open until explicitly closed.

These are ordinary local Python programs with the user's permissions, **not a
security sandbox**. Budget accounting covers the provided client API. Direct
network/filesystem calls and separately detached processes are not mediated by
that API. Run records, logs and observation history follow the managed storage
limits below. There is no model inference service hidden inside the runtime.

Storage separates maps, reusable code, and output:

```text
window/layout/WINDOW_ID/{window.json,targets/}
window/api-fragmants/NAME/
output/DATE_RUN/{request.json,result.json,trace.json,program.log,captures/}
```

Use `--window-dir` / `CA_WINDOW_DIR` and `--output-dir` / `CA_OUTPUT_DIR`
to override locations. `ctx.output` is the current run directory for artifacts.

The example `examples/fragments/draw-rectangle.py` can be registered with stdin
redirection. It assumes the correct drawing tool/canvas is already selected and
checks only that pixels changed. Do not treat it as a verified app-specific
rectangle workflow.

Execution records are retained by the [managed storage policy](STORAGE.md):
five unpreserved runs, counting the active new run, with a configurable size budget. Reusable module
versions are never pruned by this policy.
