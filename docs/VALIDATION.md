# Recorded validation

Recorded on 18 September 2026, using packaged KWin 6.7.5 and the native plugin.

- `outputs/ca-stock-o8gtg6nu/plugin-regression.json`: separate stock KWin fixture
  checks for simultaneous human typing and agent drawing, rendered changes,
  release on disconnect/stale action/takeover/watchdog/unload, and reload.
- `outputs/stock-plugin-host/installation.json`: plugin loaded into the existing
  desktop KWin process; packaged executable/library hashes remained unchanged.
- `outputs/stock-plugin-host/kolourpaint-complete.png`: visually checked drawing
  produced through actual pointer input in the user's already-open KolourPaint.
  Its process/window identity was preserved and ownership returned. The document
  was unsaved at the time of verification.

After removing the fork, a clean plugin build, all 12 Python tests and the full
stock KWin integration regression passed again. Fresh evidence is in
`outputs/ca-stock-gnolsqn0/plugin-regression.json`.

These establish those workflows only. Keyboard input and XWayland support are
not implemented. Run `scripts/test.sh --integration` for a fresh separate stock
KWin regression; each run writes its own evidence directory under `outputs/`.

## Explicit cursor sessions

`outputs/ca-stock-2spy8nyr/plugin-regression.json` records session persistence
after CLI disconnect and app release, cursor visibility through watchdog release,
and session close releasing held input and hiding the cursor. Acquisition without
a session was rejected. The 12 Python tests also passed.

The desktop plugin was updated without restarting KWin (PID 3357 unchanged).
`ca session open`, `status` and `close` were verified across separate CLI processes.
The final session is closed and reports `cursor_visible: false`. No host app input
was sent during this update.

## Computer Memory and execution API

`outputs/ca-stock-h5834i1_/harness-regression.json` records end-to-end tests
against a separate packaged KWin instance. They registered a module via stdin,
reused it with typed arguments, checked canvas pixel changes, invoked a nested
module, used an observation-backed target, and stopped a gesture on a feedback
condition. Compact CLI results and full record inspection were checked.
A one-second hard deadline interrupted a held drag and the fixture received its
button release. Version history, attachment and application-library promotion
were exercised. `plugin-regression.json` in the same directory passed all prior
input isolation/session/lifetime checks.

All 25 Python tests passed, including non-executing registration, invalid inputs,
source version pinning, guarded target refusal, scaled image diffs, bounded
observation retention, shared nested execution, verification failures and
feedback gesture release. The host desktop compositor stayed at PID 3357 with
no active agent session. No host app input was dispatched by these tests.

This verifies the fixture workflows, not Blender/browser semantic automation.
Keyboard, automatic control discovery and accessibility adapters remain absent.

## Two lanes and automatic sessions

The current interface supersedes the earlier explicit `session open` workflow.
All 28 Python tests passed. Separate packaged-KWin evidence is recorded in
`outputs/ca-stock-v7t0so0u/host-lanes-regression.json`, alongside the passing
plugin and Computer Memory regressions. Checks include:

- Read-only commands leave a session closed; input opens one automatically.
- Real pointer movement, application-delivered scrolling, and simultaneous
  host/agent drags in different application connections.
- Same-connection conflicts and old-plugin fallback rejection.
- Interruption by another pointer/keyboard source while independent agent input
  can continue; release on disconnect, watchdog, stale generation and unload.
- Shared parallel execution with lane traces, sibling cancellation on failure,
  and a hard host-worker deadline.
- Explicit host focus recovery after an application-created modal dialog,
  followed by successful agent input to the dialog.
- Session close releases held input in both lanes and hides the agent cursor.

External input used a distinct fake-input device restricted to the private test
display. This exercises source-device discrimination but is not a manual physical
hardware test on the host desktop. Screen-lock and arbitrary app compatibility
are not established by these runs. A text-selection DND attempt was rejected as
unsupported; the failure is preserved in `outputs/ca-stock-6rk6i082/`.

The plugin was then loaded on the actual desktop without restarting KWin; PID
3357 and the packaged executable hash stayed unchanged. Both lane capabilities
were queried and the session remained closed. No host-lane actions were sent to
the user's apps during installation. Installation evidence is in
`outputs/stock-plugin-host/host-lane-update.json`. Keyboard injection and prevention
of the initial agent-dialog focus change remain unimplemented.
