# Popup lifetime regression, 19 October 2026

The desktop crash evidence is preserved under
`output/benchmark-crash-2026-10-19/`: `findings.txt`, `kwin-coredump.txt` and
`previous-kernel.txt`. The original host stack reaches
`XdgPopupInterface::sendPopupDone()` through `Artist::release()` and the
`Artist::observe()` callback during `XdgToplevelInterface` destruction.

## Source finding and fix

KWin 6.8's [XDG window implementation](https://invent.kde.org/plasma/kwin/-/blob/Plasma/6.8/src/xdgshellwindow.cpp)
marks a window deleted before emitting `Window::closed`. Deleted windows can
outlive their XDG roles while effects retain them. `XdgPopupWindow::popupDone()`
uses its role and destroys the window without an idempotency guard. KWin also
connects parent close to child popup dismissal. CA previously retained only
`QPointer<Window>` and called `popupDone()` synchronously from its close handler:
it could reuse an already deleted popup or interfere with KWin's pending
parent-close callbacks.

CA now records weak references to the window, XDG popup role and connection.
Release immediately stops the watchdog, releases pointer buttons and keyboard
state, clears ownership and revokes the controller as appropriate. A zero-delay
timer dismisses remaining live popups after the current signal/Wayland dispatch
unwinds, in reverse creation order (nested child before parent). Each dismissal
rechecks that the window is not deleted, the role still exists, and the client
is not tearing down. Client destruction discards tracked popups; its
`aboutToBeDestroyed` signal precedes the `tearingDown` flag. Plugin unload stops
the timer and drains guarded pending dismissals synchronously after input release,
so no callback is left behind in an unloaded module.

No popup dismissal runs inside the ordinary synchronous release path. This also
prevents popup-close callbacks from reentering a partially released lease or
overwriting its stop reason.

## Reproduction and validation

`tests/integration/check-popup-lifetime.py` runs only through the isolated
`run-stock-plugin.py --headless --check-popup-lifetime` harness. It opens two real
Qt Wayland menus under an agent lease and checks:

- Live popup dismissal with a held modifier, with and without a held button.
- Abrupt controller socket disconnect and human pointer takeover, including stop reasons.
- Isolated plugin unload/reload with nested popups and held input.
- Destroying popup roles, then closing their parent; closing a parent with live popups.
- Killing the application connection while popups and input are held.
- Empty held-input/ownership state, absent popups, compositor responsiveness and
  preserved human keyboard focus after each case.

An untouched plugin built from `0e326a1` crashed the isolated KWin (PID 23353,
SIGSEGV) in the destroyed-popups/parent-close case. The captured stack again has
`Artist::release`, `Artist::observe` and `XdgToplevelInterface` destruction; this
reproduction faults in QObject disconnect during repeated KWin window teardown,
rather than the original host's `sendPopupDone` frame. Logs and symbolized core
metadata are in `build/crash-fix-baseline.log` and
`build/crash-fix-baseline-coredump.txt`. This proves the unsafe teardown path; the
original inaccessible core does not establish which popup object caused its fault.

The fixed build passes all eight cases. The regression is included in
`scripts/test.sh --integration`. The final complete invocation exited 0:
216 Python tests ran (207 passed, 9 native-platform skips), the plugin build and
`ldd -r` check passed, and all isolated integration checks passed (pointer,
programs/fragments/gestures, both lanes, host text/clipboard, independent KWrite
keyboard/save, recovery, popup lifetime, Qt menu selection and accessibility).
`scripts/check.sh` passed lint and formatting for 109 Python files;
`git diff --check` passed. Full output is `build/crash-fix-final.log`; retained
reports are under `build/crash-fix-final/` (normal run retention pruned some older
JSON reports, whose printed results remain in the full log).

These checks use packaged KWin 6.8.0 with its virtual backend and native Qt
Wayland clients. They do not replay the original KolourPaint Save dialog, verify
XWayland popup support, or establish compatibility with all toolkits/KWin versions.
The live host plugin was neither installed nor reloaded; the host compositor was
not restarted. The drawing benchmark was not resumed. Baloo is outside this fix.
