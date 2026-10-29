# Code structure

The public interface is `ca`. The CLI routes connected commands directly; `commands`
handles program/fragment/memory arguments and offline operations. Simple input
commands call the same `Context` actions as programs. `execute`, `draw`,
`fragments run` and `memory run` use one supervised worker path. There is no web
interface or legacy `main(client)` runner.

| Responsibility | Modules |
| --- | --- |
| CLI parsing/routing | `cli`, `commands` |
| Deadlines, targeted stop, partial evidence | `supervisor`, `worker`, `control`, `journal` |
| Actions and program evaluation | `runtime`, `programs`, `contracts`, `lanes` |
| Transport, leases, heartbeat | `client` |
| Capture, diffs, guards, watch | `observations`, `watch` |
| Read-only AT-SPI accessibility trees | `accessibility` |
| File checks and JSON inspection | `artifacts`, `records` |
| Names, fragments, retention, files | `workspace`, `fragments`, `storage`, `files` |
| Learned tasks, retrieval, guarded procedure snapshots | `workflows` |
| Private environments: definitions/lifecycle, apps/browsers, service, CLI | `environments`, `environment_apps`, `environment_service`, `environment_commands` |
| Linux boxes on Windows/macOS, container transport and startup | `box`, `box_service`, `containers` |
| Native Windows input, capture and accessibility | `windows`, `windows_agent`, `windows_pointer`, `windows_capture`, `windows_uia` |
| Native macOS input, capture and accessibility | `macos`, `macos_native`, `macos_pointer`, `macos_activity`, `macos_capture`, `macos_accessibility` |
| Geometry, keys, configuration/setup | `gestures`, `input`, `config`, `diagnostics`, `setup`, `update`, `errors` |

`Context` owns coordinates, preflight, takeover/geometry guards and invocation
verification. `Client` owns transport and low-level dispatch. Nested calls and lane
contexts share a deadline/budget; the selected backend arbitrates ownership.
Native and socket replies share trace recording and rejection handling. Fragment
write and restore share atomic revision selection: JSON pointers on Windows and
relative symlinks elsewhere, with immutable versions under `window/api-fragmants`.

`workflows` stores local JSON memories under the shared root's `workflows/`:
task/app/notes, a source snapshot, initial-window preconditions, outcome summaries
and the latest twenty supervised reuse attempts. Search returns compact matches; show returns
the full procedure. Learn inspects a completed managed run. `execute --remember`
learns or updates only after a verified successful run; learning errors are reported
separately from the execution result. These are agent-authored notes and snapshots,
not passive desktop observation or automatic UI adaptation. See
[workflow memory](../skills/computer-artist/references/memory.md).

Recent module/check lists cap at 200; merged traces cap at 512. Checkpoints poll
at 250 ms and write only changed evidence. They are best effort, not a complete
action log or outcome. Checkpoints cap at 4 MiB and reviewed final records at 8 MiB;
large user results/evidence can exceed these byte limits.

The package stays flat and acyclic. Core modules do not import CLI/process entry
points. Storage/contracts do not depend on desktop input. Linux input ownership
and the KWin cursor stay in `plugin/main.cpp`; the Windows and macOS overlays draw
the same ink pen with `ink_cursor`, so change both renderers together. Capture, clipboard and
independent keyboard state have dedicated modules. Builds/tests do not update the host.
`environment_service` runs inside each environment's systemd unit; the CLI talks
to it over the private runtime's manager socket and never to the user's desktop.
