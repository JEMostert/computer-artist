# macOS

Read this only on macOS. The rules in SKILL.md apply as well.

The native backend was verified for Chrome canvas drawing, physical text, right
click, scroll and middle drag on macOS 27.0.1 (Apple M1 Pro). Other apps/OS versions
remain unverified. It uses private SkyLight pointer delivery and per-process
physical keys, with text resolved from the live keyboard layout. It draws its own click-through
ink pen. No agent operation falls back to the human pointer, focus or clipboard.
This is not a security sandbox or a claim of KWin parity.

```bash
ca doctor                                      # native APIs and OS permissions
ca windows
ca set WINDOW_ID --name app
ca observe --window app
ca a11y --window app --role 'push button'
ca click --window app --x 120 --y 80
ca write --window app 'Hello, world!'
ca session close
```

Grant Accessibility, Screen Recording and Input Monitoring to the terminal/app
context running `ca` in System Settings > Privacy & Security. The backend does not
prompt automatically. `ca doctor` does not require Docker. Native transport uses the
`macos` sentinel (`--socket macos`).

Foreground watching and the initial click to focus a background target are allowed.
The listen-only guard stops on real keys in the target application, clicks in its
already-foreground target window, and drags/scrolling over the target even while
backgrounded. Hovering alone is allowed. Attribution uses event type, event-time
location and foreground PID without reading key values/text or logging input;
tagged agent events are ignored. Listener failure stops input. These rules have
portable regressions; live human-driven takeover remains unverified.

One lane owns each application PID. Keys share that app's keyboard window;
another foreground window in the same app is refused rather than rekeyed. Do not
attempt simultaneous editing in one app. Targets must be on the current Space;
target loss or geometry changes stop input. Do not switch Spaces or change human
focus to bypass a refusal. There is no host fallback.

Input coordinates are logical window-frame points, including decoration. Captures
are pixels; Retina images need scaling to the reported window dimensions before
input. Inspect the capture first. Read-only AX trees use the same frame origin.
Left/right/middle buttons, multi-clicks, paths, drags and scrolling are implemented;
verified compatibility is limited to the Chrome workflows above.
IME, dead keys and app data drag-and-drop are unsupported. Only explicit `--host`
uses shared pointer/focus/clipboard; host paste uses Cmd+V. Host activation/paste
and Retina scaling remain live-unverified. Programs, gestures, watch, checks,
fragments and handoff use the shared CLI workflow.

Private Linux boxes remain available through **Docker Desktop** (not Podman).
They test Linux and web applications; Mac applications cannot run inside them.

```bash
ca env start TASK --project .                  # first start builds the box image
ca env exec TASK --name app --wait-window TITLE --timeout 120 -- program args
ca --environment TASK observe --window app
ca env doctor TASK
ca env stop TASK
```

If environment diagnosis reports no Docker runtime or a stopped daemon, stop and
tell the user (see SKILL.md). On Apple silicon boxes use amd64 emulation, so builds
and starts take longer. See [Linux boxes](environments.md#linux-boxes-windows-and-macos)
for paths and recipes, and [Linux](linux.md) for box input with `--environment TASK`.
See [macOS implementation status](../../../docs/MACOS.md) for evidence and limits.
