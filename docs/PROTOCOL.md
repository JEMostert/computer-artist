# Local controller protocol, version 3

The plugin serves user-only `$XDG_RUNTIME_DIR/computer-artist/control`. Requests
and replies are newline-delimited JSON, bounded to 64 KiB per request. Input means
dispatched, never independently verified. `lane` is `agent` (default) or `host`.
The host client must negotiate `protocol >= 3`, `lane: host` and `host_pointer: true`
before sending actions; there is no compatibility fallback.

| Operation | Fields | Behavior |
| --- | --- | --- |
| `capabilities` | `lane` | Operations/limits for the chosen lane |
| `windows` | — | Native windows, geometry, ownership and real active window |
| `session_status` | — | Session ID, lane targets/button counts and real pointer position |
| `session_close` | — | Release both lanes, hide agent cursor, close session |
| `acquire` | `window`, `lane` | Acquire visible native target; auto-create session on success |
| `release` | `lease`, `lane` | Release held input and return that lane's app |
| `move` | `x`, `y`, `lease`, `generation`, `lane` | Absolute logical content coordinates; unobscured target only |
| `button` | `code`, `pressed`, lease fields | Evdev mouse button 272–274 |
| `scroll` | `axis`, `delta`, lease fields | Vertical/horizontal scroll |
| `focus` | `window`, lease fields, `lane: host` | Request real desktop focus for that exact target |
| `key` | `code`, boolean `pressed`, lease fields, `lane: host` | Physical Linux key 1–247; exact host target must have keyboard focus |
| `clipboard_get` | `lane: host`, lease fields if acquired | Read UTF-8 text; no focus or session required |
| `clipboard_set` | `text`, `lane: host`, lease fields if acquired | Replace shared clipboard text; at most 8192 UTF-8 bytes |
| `cancel` | `lane` | Agent: release held input, retain lease. Host: release input and lease |
| `takeover` | `lane` | Stop that controller; available to another connection |
| `ping` | `lane` | Status; only the owning connection renews its watchdog |
| `capture` | `window`, absolute new `path` | Save readable main-surface buffer as PNG |

`session_open` is removed. Observation, capture and status do not open sessions.
Session creation is atomic with acquisition in KWin's single event loop. Both
lanes may be owned concurrently, but not for the same Wayland connection. One
controller per lane. A lease has its own UUID and geometry generation; stale
input is rejected. Keyboard and clipboard operations require the host lane.

Ordinary replies include `ok`, `lease`, `generation`, `keyboard_ready`, and `session`.
`keyboard_ready` is true only when a valid host target has real keyboard focus.
Clipboard replies are asynchronous, containing `ok`, `lane`, and `text` for reads;
send one request at a time per connection. Clipboard get/set never renew a lease.
Transfers time out after 1.5 seconds per stage and reject oversized/non-UTF-8 data.
Failures contain `error`. Common observational replies additionally contain
`cursor_visible`, `host_position`, and `lanes`. Agent/host geometry generations
are independent. Host action replies report `lane: host`. A host stop reason is
available under `lanes.host.stop_reason` in session status.
`lanes.host.keys` counts held synthetic keys. Clipboard reads/writes do not create
a session. The clipboard source survives socket disconnect and session close.

Disconnect, watchdog expiry, target changes and session close release held input.
Other devices interrupt host automation; human pointer/focus entry into an
agent-owned connection revokes agent ownership. Agent popup automatic activation
remains a known limitation. Host focus is explicit recovery, never an implicit
agent fallback. Host DND, constrained pointers, popup grabs and XWayland are
unsupported. Input separation is not a security sandbox.
