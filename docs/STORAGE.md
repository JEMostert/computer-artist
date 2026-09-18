# Storage and cleanup

Temporary history is bounded; reusable work and published assets are not disposable.

| Location | Policy |
| --- | --- |
| `outputs/` | Managed experiment runs: newest 15 completed, unpreserved runs |
| `computer-memory/runs/` | Managed execution records: same policy |
| `computer-memory/windows/*/observations/` | Newest 15 observations per window, including associated crops |
| Computer Memory modules, versions, targets, and app libraries | Kept; never deleted by run retention |
| `docs/assets/` | Published artwork, tracked in Git; never automatically deleted |
| `build/` | Generated plugin/build/test files; ignored, clean manually when necessary |

Each managed run store has a default **256 MiB** byte budget. Observation folders
use the same byte budget per window. The oldest eligible complete run folders
are removed until count and size fit. Active and explicitly preserved runs do not
count against the 15 unpreserved runs, but their size counts toward the budget.
The newest eligible result is retained even if it alone exceeds the budget; a
just-finished run is also protected during its own final cleanup. Protected data
can therefore exceed the limit. `clean` reports `over_budget` in that case.

```bash
ca storage status
ca storage clean --dry-run
ca storage clean
ca storage keep RUN_ID
ca storage unkeep RUN_ID

ca storage status --scope runs
ca storage clean --scope runs
ca storage keep RUN_ID --scope runs
```

`outputs` is the default scope. `runs` follows `--memory-dir`. These are local
commands and do not connect to KWin or open a cursor session.

Set `CA_RUN_LIMIT=15` and `CA_RUN_MAX_MB=256` to customize the run count and byte
budget. Values must be positive integers. Observation count is 15 per window.
Cleanup runs when a managed run starts and ends; no background service is needed.
Explicit cleanup is useful when changing the policy without running a task.

## Ownership and crash handling

Only directories with CA's `.ca-run.json` marker participate. File locks protect
active runs across processes, including child workers and compositor fixtures.
If a process exits unexpectedly and all holders release their locks, its run is
eligible as abandoned. Preservation is independent of run activity.

Unmarked folders, symlinked run directories, arbitrary `ca capture` destinations,
user-specified trace exports, and final documents are not adopted or deleted by
cleanup. A preserved record may still refer to a separately expired observation;
copy important evidence into the preserved run or save it explicitly.

Experiment authors can opt into managed output:

```python
from pathlib import Path
from computer_artist.storage import managed_run

with managed_run(Path('outputs')) as folder:
    # Write this experiment's temporary captures and logs inside folder.
    ...
```

The integration runner uses this API automatically. Keep important final artwork
outside temporary stores, as the painting demo does in `docs/assets/`.

## Development layout

- `computer_artist/`: the CLI, client, runtime, memory, and storage implementation.
- `plugin/`: native KWin plugin source and installation documentation.
- `scripts/`: convenient build/test entry points.
- `tests/integration/`: isolated KWin runner, fixtures, protocol helpers, checks.
- `examples/`: demonstrations and sample memory modules.

Deleting `build/` removes generated files only. It does not uninstall the plugin
already installed in the system plugin directory. The next build recreates it;
do not remove it during an active build or integration run.
