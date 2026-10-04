# CLI programs

```bash
ca set ID --name APP
ca observe --window APP
ca target APP tool --observation OBS --rect X Y W H
ca fragments create select-tool <<'PY'
def run(ctx):
    ctx.click(target='@tool')
PY
ca execute --window APP --deadline 15 --budget 1000 <<'PY'
def run(ctx):
    ctx.fragments.call('select-tool')
    return ctx.observe()
PY
```

Define a synchronous `run(ctx)`; stored fragment parameters require `int`, `float`,
`str`, `bool` annotations and literal defaults. Optional literal `CONTRACT` declares
`requires`, `lane`, window constraints and parameter bounds/choices. Registration
parses without executing. Update with `fragments update NAME < code.py`; inspect
`show`/`history`; pin nested calls with `version=REVISION`. Restore validates a
historical revision without deleting newer work.

For parameter names reserved by the wrapper (`name`, `lane`, `version`), use
`ctx.call(NAME, values, version=REVISION)`. All calls share deadlines/budgets.
Use `ctx.observe(since=id)`, `ctx.wait_for({'changed': {'changed_since': id}})`,
`ctx.path(points, until=predicate, observe_every=10)` and `ctx.svg_path(...)` for
local feedback. Every SVG moveto starts a separate stroke. `verify_change=True`
checks pixel changes, not exact geometry. Select/map the app's drawing tool first.

Keep custom canvas geometry in `ctx.store.layout(ctx.window_id)/'areas.json'` and
check against `ctx.window` before reuse. This is a fragment convention, not automatic
control discovery. Rebound guards require a fresh observation and explicit
`ca target APP NAME --observation OBS --revalidate`; changed geometry/pixels need
new regions. Never bypass a refused guard with copied coordinates.

Independent keys use `ctx.press('Ctrl+S')` or paired `key_down`/`key_up` when the
loaded plugin supports them. KWrite has a demonstrated workflow; verify other apps.
Explicit host contexts use real focus. `ctx.paste(text, shortcut='Shift+Insert')`
and `ctx.clipboard_get/set` are host-only; text caps at 8192 UTF-8 bytes.

```python
before = ctx.snapshot_file(path)
ctx.press('Ctrl+S')
ctx.verify_file('saved document', path, kind='text', after=before,
                expected_text=text)
```

Use the app's actual save/export procedure and destination; unknown dialogs need
explicit inspection. File checks support exact UTF-8 text/JSON, image decoding/size
and SHA-256. `after` requires freshness; without it old output may pass. Default
wait is five seconds with 0.2-second stability; reads cap at 64 MiB and decoded
images at 16 million pixels. Decoding does not certify artwork.

`ctx.verify(name, passed, evidence=...)` or optional `verify(ctx, result)` with
`{check, passed, evidence?}` records a finite JSON claim. Only an invocation's own
checks establish `verified`; unchecked parents remain `returned_unverified`.
Write task artifacts under `ctx.output`. Inspect through `ca runs inspect ID`,
preserve with `ca storage keep ID`. Recent module/check lists cap at 200 and traces
at 512; only fifteen recent captures per window remain in a run. Preservation
cannot recover already-pruned data. Save final documents outside rotating output.

`ctx.handoff(reason)` releases/yields. `ctx.related_windows()` lists same-process
candidates without acquiring them. Inspect current captures and select an exact ID
in a fresh run. App-created dialogs can change human focus. `ca runs stop ID`
requests cancellation; inspect the final acknowledgment before retrying. Partial
checkpoints can miss later actions and never establish completion.

Keep `--environment TASK_NAME` on every private-environment command, including
host-lane operations. Never drop it to recover from an acquisition failure.
For explicitly requested actual-desktop work, parking human pointer/focus with
`ca --host focus` or `ca --host move` requires authorization in the current task;
this reference does not grant it. Use an observed safe point only when authorized,
then return to agent input. Never use host recovery to bypass a refused action.
