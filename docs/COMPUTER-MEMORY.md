# Computer Memory and agent programs

Computer Memory stores small Python actions the agent can compose. Each action
has a typed interface, an immutable version, optional preconditions and explicit
outcome checks. It is intended for steps such as drawing a shape or selecting an
object; a module need not encode an entire task.

These commands work with the installed stock-KWin plugin. Keyboard input,
XWayland, automatic visual control detection, accessibility discovery and semantic
Blender/browser adapters are **not implemented**. The runtime reports those
limits; it never silently switches to human input. A Blender cylinder module
will need an implementation compatible with the available input/app interface.
No such Blender workflow has been validated by this change.

## Create and reuse

```bash
ca windows
ca memory create WINDOW_ID move-to-point --description "Position the agent pointer" <<'PY'
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

ca memory list WINDOW_ID
ca memory inspect WINDOW_ID move-to-point
ca memory run WINDOW_ID move-to-point --x .5 --y .4
ca memory run WINDOW_ID move-to-point --args '{"x":0.7,"y":0.6}'
ca session close
```

Creation parses syntax and contracts **without running imports, defaults or the
body**. `run(ctx, ...)` parameters must use `int`, `float`, `str` or `bool` type
annotations. Defaults must be literal values. CLI booleans use `true` or `false`.
Unknown parameters and invalid values are rejected before execution. Parameters
that share names with CLI options (such as `deadline`) can be passed in `--args`.

Registration works offline and does not require a running window. The ID becomes
the module's namespace. Execution resolves that exact window and checks that it
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
    ctx.memory.call('move-to-point', x=.3, y=.4)
    ctx.sleep(.2)
    ctx.memory.call('move-to-point', x=.6, y=.4)
    return {'finished': True}
PY
```

`ca execute` reads Python defining `run(ctx)` from stdin. The same optional
`CONTRACT` and `verify` hook work for inline programs. `ca run task.py` remains the
legacy `main(client)` API; use `execute` or `memory run` for the new context and
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
| `ctx.memory.call(name, **arguments)` | Invoke another module in the same window/context |
| `ctx.verify(name, boolean, evidence=...)` | Record an explicit check; stop on failure |
| `ctx.yield_to_agent(reason)` | Stop and return control with available observation evidence |
| `ctx.type(text)`, `ctx.press('Ctrl+S')` | Capability-gated; rejected on the current pointer-only plugin |

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

Only the newest 15 observations per window are retained. Targets referring to an
evicted observation expire and must be defined again. Observation IDs from other
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

## Versions and reuse across windows

```bash
ca memory update WINDOW_ID move-to-point <<'PY'
def run(ctx, x: float, y: float):
    ctx.move(relative=(x, y))
    return [x, y]
PY
ca memory history WINDOW_ID move-to-point
ca memory show WINDOW_ID move-to-point --version VERSION
ca memory run WINDOW_ID move-to-point --version VERSION --x .4 --y .5
ca memory attach NEW_WINDOW_ID --from OLD_WINDOW_ID
ca memory promote WINDOW_ID move-to-point --app blender
ca memory list blender --app-scope
ca memory attach NEW_WINDOW_ID --from-app blender --name move-to-point
ca memory remove WINDOW_ID move-to-point
```

A window ID is a runtime identity, not a permanent application identity. Closing
a window does not delete its memory. Attach explicitly copies modules into the
new namespace; promotion creates an application-library copy. Neither declares
the new window compatible, nor rewrites code for it. Copies remain `unverified`
and preserve provenance. Preconditions still run on every invocation.

`CONTRACT['window']` supports `title_contains`, `min_width`, `min_height`,
`max_width` and `max_height`. `requires` lists backend operation names. `lane` may require `agent` or `host`;
without it either explicitly selected lane is allowed. Modules
can express additional preconditions directly in Python before acting.

Create and attach refuse collisions. Update creates an immutable version and
atomically switches the current revision. A run pins its top-level version;
nested calls record the versions resolved when invoked. Remove archives the
module under `trash/` so its history survives. Source hashes detect accidental
changes to an immutable revision.

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

Default storage (`CA_MEMORY_DIR` or `--memory-dir` overrides it):

```text
computer-memory/
  windows/WINDOW_ID/
    window.json
    move-to-point/
      module.py -> current/module.py
      manifest.json -> current/manifest.json
      current -> versions/VERSION
      versions/VERSION/{module.py,manifest.json}
    observations/
    targets/
  apps/blender/
  runs/RUN_ID/{request.json,result.json,program.log}
  trash/
```

The example `examples/memory/draw-rectangle.py` can be registered with stdin
redirection. It assumes the correct drawing tool/canvas is already selected and
checks only that pixels changed. Do not treat it as a verified app-specific
rectangle workflow.

Execution records are retained by the [managed storage policy](STORAGE.md):
15 completed unpreserved runs, with a configurable size budget. Reusable module
versions are never pruned by this policy.
