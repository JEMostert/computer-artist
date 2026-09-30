# Socket protocol

User-only `$XDG_RUNTIME_DIR/computer-artist/control`; newline-delimited JSON,
64 KiB limit. `lane` is `agent` (default) or `host`. Replies report dispatch only.
Host negotiation requires `protocol >= 3`, `lane: host`, `host_pointer: true`.

| Operation | Fields/behavior |
| --- | --- |
| `capabilities`, `windows` | Lane operations/restrictions, geometry, ownership |
| `session_status`, `session_close` | Inspect or release both lanes and hide cursor |
| `acquire`, `release` | Exact `window` to acquire; owning `lease` to release |
| `move` | Absolute logical desktop `x`, `y` within unobscured target content |
| `button`, `scroll` | Evdev `code` 272–274/boolean `pressed`; `axis`/signed `delta` |
| `focus` | Exact leased host `window`; real desktop focus |
| `keyboard_begin`, `key` | Agent initialization; physical Linux `code` 1–247/boolean `pressed` |
| `clipboard_get`, `clipboard_set` | Host UTF-8 only; `text`, at most 8192 bytes |
| `cancel`, `takeover` | Release held input; revoke lane from another connection |
| `ping` | Status; only owner renews five-second watchdog |
| `capture` | Exact `window`, absolute new `path`; composited client/subsurface PNG |

Input carries `lease` and `generation`; stale ownership/geometry is rejected.
Replies include `ok`, lease/generation, session and lane `keyboard_ready`; failures
include `error`. Clipboard replies are asynchronous; send one request at a time.
Clipboard operations neither open sessions nor renew leases. Agent cancel keeps
its lease; host cancel releases it.

One owner per lane; lanes cannot own one Wayland connection together. Human
pointer/focus return, target changes, disconnect, screen lock and watchdog expiry
reconcile held input. Status/capture do not open sessions; acquisition does.
Sessions outlive leases and close explicitly.

Independent keyboard requires `keyboard: true`, operation `key` and ready target
resources. Existing keyboard resources receive separate XKB state; there is no
additional advertised seat or change to human modifiers. Pointer-only acquisition
sends no keyboard enter. Python sends `keyboard_begin` before keys so Qt can process
activation. Changed resources/keymaps revoke readiness; verify effects before release.

Window acquisition fields snapshot real checks but can become stale; older plugins
may omit them. Capture publishes by atomic no-replace hard link; existing paths,
symlinks and unsupported hard-link filesystems are refused. XWayland, independent
clipboard/IME, popup grabs and data drag-and-drop are unsupported. Dialogs can
activate themselves. Input separation is not a sandbox.
