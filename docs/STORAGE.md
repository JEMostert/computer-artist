# Window data and run output

```text
window/
  names.json
  layout/NAME/
    window.json
    targets/NAME.json
  api-fragmants/
    NAME/
      module.py -> current/module.py
      manifest.json -> current/manifest.json
      current -> versions/VERSION
      versions/VERSION/{module.py,manifest.json}
    trash/
output/
  2026-09-20_14-30-00-123456Z-a1b2c3/
    request.json
    result.json
    trace.json
    program.log
    captures/NAME/
```

`window/layout` stores geometry and named regions with pixel hashes.
`ca set WINDOW_ID --name NAME` records an exact live window ID in `names.json`.
Commands accept either the ID or name. Named windows use `NAME` for their layout
folder and new capture folders; unnamed windows use their ID. Naming a window
moves its existing layout folder. Older captures stay in their original folders
and remain available to `--since` while retained. Rebind the name when the app
reopens with a new ID.
`window/api-fragmants` stores reusable Python code and its versions. Neither is
an image archive or task journal. Layout targets retain their own geometry/hash,
so pruning their source screenshot does not erase the map. Using a target still
requires a fresh capture matching its geometry and pixels.

`output` holds dated UTC run folders. A supervised `execute` or `fragments run`
creates one run; all its contexts share that folder. A standalone `observe` creates
one capture run. Cursor sessions can span several runs and close independently.
Read-only session inspection and fragment registration do not create runs.
Programs can write artifacts under `ctx.output` (a Path).

## Retention

The default is **five unpreserved runs, counting the new run**. At run startup,
if five completed runs exist, the oldest is removed before work begins. Cleanup
also runs at completion. Ordering uses recorded creation time; date-based names
make folders readable and sortable. A random suffix prevents timestamp collisions.

Active and explicitly preserved runs are never removed. The latest eligible
result and a just-finished result are protected, so concurrent/protected runs can
temporarily exceed the limit. A 256 MiB default byte budget can remove older
eligible runs sooner. `over_budget` reports protected data exceeding that budget.
Only folders bearing `.ca-run.json` participate; arbitrary files and symlinks are
not adopted. File locks protect workers and isolated compositor fixtures.

Within a run, each window retains its newest 15 observations (and a 256 MiB
budget by default). Preserving the run protects remaining captures with its logs;
it cannot recover observations already pruned during execution. Cross-run
`--since` comparisons work only while the earlier run and capture remain.

```bash
ca storage status
ca storage clean --dry-run
ca storage clean
ca storage keep RUN_ID
ca storage unkeep RUN_ID
ca runs list
ca runs show RUN_ID
```

These commands work offline. `runs` lists supervised executions; `storage` also
shows standalone capture and experiment runs. Save final documents outside
rotating output, or explicitly preserve the run containing them.

## Locations and configuration

Defaults are the checkout's `window/` and sibling `output/`. Override with
`--window-dir` / `CA_WINDOW_DIR` and `--output-dir` / `CA_OUTPUT_DIR`.
Without an output override, output is a sibling of the selected window root.
`CA_RUN_LIMIT=5` and `CA_RUN_MAX_MB=256` configure positive count/size limits.

The former `computer-memory/`, `ca memory`, `ctx.memory`, and `--memory-dir`
interface is replaced by this layout, `ca fragments`, `ctx.fragments`, and
`--window-dir`. Fragments are shared across windows; immutable fragment versions remain.

Integration tests use separate packaged KWin instances and managed output.
`build/` contains disposable generated build files; removing it does not uninstall
the loaded plugin. `docs/assets/` contains tracked demonstration artwork.
