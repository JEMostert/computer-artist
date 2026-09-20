# Native Computer Artist plugin

This module builds against `/usr/include/kwin` and `KWin::kwin` from the installed
KWin development package. It does not replace any
KWin executable/library. Independent input is **pointer-only**; explicit host
input also supports keyboard and clipboard operations.

It enumerates a chosen native Wayland client's existing `wl_pointer` resources
through libwayland-server and delivers enter/motion/button/axis events directly.
The human seat's pointer position, buttons and keyboard focus are not changed.
Only one existing native toplevel is a pointer target at a time; ownership covers
its connection. There is no extra advertised `wl_seat` and this is not complete
multi-seat support.

Acquisition refuses a connection with human pointer or keyboard focus. Human
pointer entry into its windows or keyboard focus returning to it revokes the
agent lease. Geometry changes, popups, close, lock, controller disconnect and a
5-second watchdog also stop/release input. Rebinding pointer resources during a
lease cancels further actions; applications that require validated compositor
grab serials are outside the demonstrated compatibility scope. No private class
layout access, binary patching, LD_PRELOAD or human-input fallback is used.

Sessions open automatically on successful input acquisition and close with
`ca session close`. They survive CLI disconnects and app lease releases. The agent
cursor appears on first agent movement and remains visible through pauses.
Closing releases input in both lanes, hides the overlay and stops animation.
Read-only commands do not create sessions. A plugin unload ends the session.

An explicit host lane now sends pointer events through KWin's normal pipeline
using a distinct `InputDevice` identity. Other source devices preempt that lane.
The plugin coordinates both lane leases and refuses same-connection conflicts.
Host focus, keyboard and clipboard access are explicit. XWayland input is unsupported.
See [the host-lane contract](../docs/HOST-LANE.md).

The module supports the existing protocol's windows/capabilities, acquire/release,
move/button/scroll, cancel/takeover and ping. `capture` saves a readable main-surface
buffer into a new PNG path; it may fail for unmappable GPU buffers and does not
compose arbitrary subsurfaces, decorations or effects. Independent keyboard/clipboard,
IME, menus/popups, data drag-and-drop and XWayland are explicitly unsupported.
Host text clipboard uses a nonblocking `ext-data-control-v1` client connected to
this KWin instance, with bounded transfers and no external clipboard executable.
Build dependencies include `wayland-client`, `wayland-scanner`, and
`wayland-protocols` containing the ext-data-control protocol.

## Build and load

```bash
./scripts/build-plugin.sh
sudo install -m 755 build/plugin/kwin/plugins/computerartist.so /usr/lib/qt6/plugins/kwin/plugins/computerartist.so
qdbus6 org.kde.KWin /Plugins org.kde.KWin.Plugins.LoadPlugin computerartist
ca capabilities
```

On this machine the plugin has already been installed and loaded. Do not overwrite
a loaded module in place. Stop agent input and unload it before replacing its file.
KWin/Qt can retain the old library mapping after plugin unload. To apply changed
code within the same desktop process, use a distinct versioned filename and load
that filename's plugin ID; simply reloading the old ID may run old code.

Current desktop update: `computerartist-text-3cefad7bfbd9` is loaded for this login.
The canonical `computerartist.so` also contains the new code and remains the
configured plugin for future logins. The versioned copy defaults to disabled.
For this login use `computerartist-text-3cefad7bfbd9` in the unload command below.
KWin's binary plugin version interface requires rebuilding for each KWin release.
The plugin metadata defaults to disabled; `computerartistEnabled=true` in the
`[Plugins]` group of kwinrc enables loading on later logins.

`ca` defaults to `$XDG_RUNTIME_DIR/computer-artist/control`. This endpoint is
created by the plugin with user-only access. `CA_SOCKET` and `--socket` override it.
The permissions and privileges are those of the current user, not a sandbox.

To unload without restarting KWin:

```bash
ca stop
qdbus6 org.kde.KWin /Plugins org.kde.KWin.Plugins.UnloadPlugin computerartist
```

## Tests against packaged KWin

`tests/integration/run-stock-plugin.py` runs **`/usr/bin/kwin_wayland`**, using the plugin
from the build directory. It provides a separate nested test window; `--headless`
selects the virtual backend. It drops executable-added privileges with setpriv
for testing. The test compositor's private environment alone enables fake-input
access to exercise human input; that setting is never applied to the desktop.

`tests/integration/check-stock-plugin.py SESSION` verifies simultaneous pointer drawing and
human typing, changed rendered output, button release on disconnect/stale action/
takeover/watchdog/unload, and reload. `tests/integration/stock-test-input.c` refuses displays
outside the `ca-stock-*/wayland-test` harness. The test runner owns a persistent `hold` helper connection that
keeps the virtual seat's devices present during the headless tests.

`tests/integration/check-host-text.py SESSION` verifies Unicode clipboard paste,
key holds, takeover and cleanup with real Qt Wayland clients, plus a KWrite
paste/save checked from disk. The text suite requires installed `kwrite` and
can be run alone with `run-stock-plugin.py --headless --check-text`.

Evidence: `outputs/ca-stock-o8gtg6nu/plugin-regression.json`. Host installation evidence
is kept in `outputs/stock-plugin-host/`.

## Desktop verification

The plugin was loaded into the existing host KWin PID 3357 on 18 September 2026.
The already-open KolourPaint PID 31404 / window
`1608b76f-126d-4517-8457-0487b36606ff` received real pointer strokes and flood fills
that created a coloured robot. Its process/window identity was preserved, the
lease was returned, and the resulting image was inspected. Hashes of packaged
`kwin_wayland` and `libkwin` were identical before and after. No compositor restart
was performed. `outputs/stock-plugin-host/installation.json` records the evidence;
`kolourpaint-complete.png` captures the result. The document remains unsaved in
KolourPaint. These observations establish this specific workflow, not general
application or toolkit compatibility.
