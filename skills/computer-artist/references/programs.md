# Programs and reusable actions

Replace `WINDOW_ID` with a current ID from `ca windows`. Use `ca COMMAND --help`
for exact CLI options. All examples presume the relevant canvas/tool is already
selected and the indicated region is safe for the requested action.

## Draw and observe in one call

```bash
ca execute --window WINDOW_ID --deadline 15 --budget 1000 <<'PY'
def run(ctx):
    before = ctx.observe()
    ctx.path([(120, 180), (160, 220), (200, 180)], interval=.02)
    after = ctx.observe(since=before['id'])
    return after
PY
```

`ctx.path` holds the left button during the stroke. Points are logical pixels
relative to the window content; `relative=True` accepts window fractions in
`[0,1)`. `ctx.click(x=..., y=..., button='left')`, `ctx.move(...)`, and
`ctx.scroll(delta, x=..., y=...)` use the same origin; they also accept
`relative=(x,y)` or `target='@name'`. Positive scroll is down/right; test a small
delta because applications interpret its magnitude differently.

Use `ctx.window` for fresh geometry. A moved/resized window or a new window from
the target PID interrupts high-level actions: observe again and start a new run.
Do not switch to raw input to evade that interruption.

`ca run file.py` is the older `main(client)` interface, with desktop-global
coordinates. Prefer `execute` and `memory run` for context-local coordinates,
hard deadlines, shared budgets and recorded checks.

## Conditional actions and guarded targets

```bash
ca target WINDOW_ID button --observation OBSERVATION_ID --rect 20 40 80 30
ca execute --window WINDOW_ID --deadline 10 <<'PY'
def run(ctx):
    before = ctx.observe()
    ctx.click(target='@button')
    result = ctx.wait_for({
        'changed': {'changed_since': before['id']},
        'error': {'title_contains': 'Error'},
    }, timeout=4, interval=.15)
    if result['matches'] == 'error':
        ctx.yield_to_agent('Application reported an error')
    return result
PY
```

Targets are agent-defined pixel regions, not semantic control detection. Exact
pixels and geometry are checked again; animation or expired observations can
invalidate them. Conditions also accept `stable_for` seconds or a callable
receiving an observation. `ctx.path(..., until=predicate, observe_every=10)` can
stop a drag using feedback. Use `ctx.verify(name, boolean, evidence=...)` for a
specific outcome assertion, not merely to label dispatched input as success.

## Register a small reusable step

```bash
ca memory create WINDOW_ID stroke --description 'Drag between two canvas points' <<'PY'
CONTRACT = {'requires': ['move', 'button'], 'lane': 'agent'}

def run(ctx, x1: float, y1: float, x2: float, y2: float):
    ctx.path([(x1, y1), (x2, y2)], interval=.02)
PY
ca memory inspect WINDOW_ID stroke
ca memory run WINDOW_ID stroke --x1 120 --y1 180 --x2 200 --y2 220
```

Arguments use `int`, `float`, `str`, `bool` annotations and literal defaults.
CLI booleans are `true`/`false`; `--args '{"x1":120,...}'` also accepts parameters.
Creation parses source without running it. Use `memory update` with stdin to
create a new immutable version; `memory show` and `memory history` inspect it.
Compose with `ctx.memory.call('stroke', x1=120, y1=180, x2=200, y2=220)`.
Avoid embedding old window IDs or screenshot dimensions in reusable steps.

Window IDs are temporary namespaces. Reuse explicitly:

```bash
ca memory attach NEW_ID --from OLD_ID --name stroke
ca memory promote WINDOW_ID stroke --app drawing-app
ca memory attach NEW_ID --from-app drawing-app --name stroke
```

Attachment copies code; it does not establish compatibility with the new window.
Check tools, layout and preconditions again. `CONTRACT` can declare `window`
size/title requirements and `parameters` bounds. `verify(ctx, result)` can return
`{'check': 'specific outcome', 'passed': boolean, 'evidence': result}`.

## Explicit host lane

When the task authorizes real-pointer/focus control:

```bash
ca --host focus --window OTHER_APP_ID
ca --host move --window OTHER_APP_ID --x 100 --y 100
```

Focus does not move the pointer. The host lane also has no keyboard injection.
Concurrent lanes must target different Wayland connections. For a parallel
program, invoke `ca --host execute --window HOST_WINDOW_ID`, then:

```python
def run(ctx):
    paint = ctx.agent.window('PAINT_WINDOW_ID')
    browser = ctx.host.window('HOST_WINDOW_ID')
    def draw(c):
        c.path([(100, 200), (140, 240)])
    def point(c):
        c.move(x=300, y=200)
    with ctx.parallel() as tasks:
        a = tasks.start(draw, paint)
        b = tasks.start(point, browser)
    return [a.result(), b.result()]
```

Same-lane tasks serialize; sibling tasks cancel on failure. The deadline and
request budget are shared. Physical input preempts host control. Don't retry
automatically after human takeover.

## Evidence and lifecycle

`ca runs list --window WINDOW_ID` and `ca runs show RUN_ID` inspect records without
replaying input. `--trace /path/result.json` exports a record outside automatic
retention. `ca observe --window WINDOW_ID --since OBSERVATION_ID` returns a diff
and paths to images. Only 15 observations per window are kept; old targets expire.
Managed unpreserved runs retain the newest 15 with a configurable size budget;
memory modules and explicitly saved artwork are not pruned. Close the cursor
session once the task is finished: `ca session close`.
