# Map → fragment → execute

## 1. Map what the task needs

Find a live ID, observe it, and open the image. Inspect `window/layout/ID/`.
Convert screenshot pixels to logical content coordinates before defining regions.

```bash
ca observe --window ID
ca target ID polygon --observation OBS --rect X Y W H
```

Map tools, modes, and palette entries needed now. Use meaningful names. A selected
button can look different: record idle/selected variants from observed states;
accept only those states, not arbitrary mismatches. Bootstrap coordinates belong
in mapping code, never copied into reusable actions.

For changing work areas, record geometry separately in `window/layout/ID/areas.json`:

```json
{"canvas": {"geometry": [100, 200, 800, 600], "rect": [50, 40, 700, 500]}}
```

These numbers are illustrative: geometry is window `[x,y,width,height]`; rect is
content-local `[x,y,width,height]`. Verify zoom/scroll before reusing a canvas map.
This JSON is a convention read by Python fragments, not automatic control discovery.

## 2. Register reusable actions

```bash
ca fragments list
ca fragments create select-polygon <<'PY'
def run(ctx):
    ctx.click(target='@polygon')
PY
```

For a drawing fragment, load its work area and validate geometry before input:

```python
import json

def run(ctx, x: float, y: float):
    area = json.loads((ctx.store.layout(ctx.window_id)/'areas.json').read_text())['canvas']
    current = ctx.window
    if area['geometry'] != [current[k] for k in ('x','y','width','height')]:
        ctx.yield_to_agent('Canvas geometry changed; remap')
    ox, oy, width, height = area['rect']
    if not (0 <= x < width and 0 <= y < height):
        raise ValueError('Point outside mapped canvas')
    ctx.fragments.call('select-polygon')
    ctx.click(x=ox+x, y=oy+y)
```

Use typed parameters (`int`, `float`, `str`, `bool`) and literal defaults.
For nested calls, avoid fragment parameters named `name` or `lane`: the call
wrapper reserves them. `CONTRACT` may declare `requires`, `lane`, `window`, and parameter bounds.
Create parses without executing. Update with `ca fragments update NAME < code.py`;
inspect with `show`/`history`. Fragments are shared; choose `--window ID` only at run
time. Check their layout assumptions on each new window.

## 3. Execute and inspect

```bash
ca fragments run select-polygon --window ID
ca execute --window ID --deadline 15 --budget 1000 <<'PY'
def run(ctx):
    ctx.fragments.call('select-polygon')
    # Compose mapped fragments for the first intended shape, then inspect.
    return ctx.observe()
PY
```

Task-specific shape coordinates belong in the task program. Use canvas-local
coordinates with drawing fragments; `ctx.path` itself uses window-content pixels.
Inspect the first actual result before a large batch. Stop on a new dialog,
geometry change, human takeover, or unexpected state. Repair the map after inspecting
it; do not substitute unguarded coordinates to make a refused action go through.

Useful APIs: `ctx.observe(since=id)`, `ctx.wait_for({'changed': {'changed_since': id}},
timeout=4)`, `ctx.path(points, until=predicate, observe_every=10)`,
`ctx.verify(name, passed, evidence=...)`, `ctx.yield_to_agent(reason)`.
`verified` means only the named checks passed. Programs run with user permissions.

## Output and handoff

Write task code and artifacts under `ctx.output`. `ca runs show RUN_ID` inspects
records; `ca storage keep RUN_ID` preserves them. Captures and executions currently
create separate runs, so preserve earlier evidence needed across the five-run rotation.
Close with `ca session close`. `--host` controls the human pointer/focus and requires
authorized scope; it is never an automatic fallback. Neither lane types text.
