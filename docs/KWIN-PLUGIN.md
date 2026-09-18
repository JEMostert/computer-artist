# Plugin architecture

Computer Artist builds against the installed KWin headers and `KWin::kwin`.
There is no KWin source checkout, compositor replacement, private object-layout
access or binary patching. KWin's versioned plugin ABI requires rebuilds after
KWin updates.

The plugin enumerates an existing native Wayland client's `wl_pointer` resources
using libwayland-server and sends enter, motion, button and axis events directly.
It does not advertise another seat. Human pointer position and keyboard focus
remain under stock KWin control. This is pointer ownership, not full multi-seat.

A lease targets one visible native toplevel and guards the entire client
connection. Acquisition refuses human pointer/keyboard focus on that connection,
held human input, lock and unsupported grabs. Returning human focus or pointer
entry revokes control. Geometry changes, target loss, resource rebinding, socket
disconnect and a five-second watchdog cancel input and release held buttons.
Observer traffic cannot renew another connection's lease.

The animated cursor is a separate scene overlay. Capture reads the target's main
surface buffer; it can fail for unmappable GPU buffers and excludes composed
subsurfaces, decorations and effects. Menus needing compositor-validated grab
serials, keyboard, clipboard, IME, drag-and-drop and XWayland are unsupported.

The user-only Unix socket serves newline-delimited JSON. The Python client
handles ownership, heartbeat, deadlines, action budgets and bounded traces.
Python programs are not sandboxed. See [protocol](PROTOCOL.md) and
[recorded validation](VALIDATION.md).
