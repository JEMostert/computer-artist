# Code structure

The public interface is `ca`. Simple input commands call the same `Context`
actions as programs. `execute`, `draw` and `fragments run` use one supervised worker
path. There is no web interface or legacy `main(client)` runner.

| Responsibility | Modules |
| --- | --- |
| CLI parsing/routing | `cli`, `commands` |
| Deadlines, targeted stop, partial evidence | `supervisor`, `worker`, `control`, `journal` |
| Actions and program evaluation | `runtime`, `programs`, `contracts`, `lanes` |
| Transport, leases, heartbeat | `client` |
| Capture, diffs, guards, watch | `observations`, `watch` |
| File checks and JSON inspection | `artifacts`, `records` |
| Names, fragments, retention, files | `workspace`, `fragments`, `storage`, `files` |
| Geometry, keys, configuration/setup | `gestures`, `input`, `config`, `diagnostics`, `setup`, `errors` |

`Context` owns coordinates, preflight, takeover/geometry guards and invocation
verification. `Client` owns transport and low-level dispatch. Nested calls and lane
contexts share a deadline/budget; the plugin arbitrates ownership.

Recent module/check lists cap at 200; merged traces cap at 512. Checkpoints poll
at 250 ms and write only changed evidence. They are best effort, not a complete
action log or outcome. Checkpoints cap at 4 MiB and reviewed final records at 8 MiB;
large user results/evidence can exceed these byte limits.

The package stays flat and acyclic. Core modules do not import CLI/process entry
points. Storage/contracts do not depend on desktop input. Native ownership and the
animated cursor stay in `plugin/main.cpp`; capture, clipboard and independent
keyboard state have dedicated modules. Builds/tests do not update the host.
