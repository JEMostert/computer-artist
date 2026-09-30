# KWin plugin

Build with `./scripts/build-plugin.sh` against the installed KWin headers and
`KWin::kwin`. It does not replace compositor executables/libraries. See
[installation](../docs/INSTALL.md) for dependencies and host update precautions.

`main.cpp` owns lanes, leases, takeover, lifetime and the animated cursor.
`capture.cpp` renders client/subsurface/GPU content. `keyboard.cpp` delivers agent
keys with separate XKB state to existing resources. `clipboard.cpp` uses
ext-data-control for bounded host UTF-8 transfers. See [protocol](../docs/PROTOCOL.md).

This is client-specific delivery, not an extra advertised seat. Human pointer/focus
return revokes agent ownership. Host input uses KWin's normal pipeline; physical
input preempts it. Lanes cannot own one connection together. Sessions start on
acquisition and close with `ca session close`. Independent clipboard/IME, XWayland
and popup grabs are unsupported.

`./scripts/test.sh --integration` uses a separate packaged KWin virtual instance.
Fake input refuses displays outside that harness. Reports cover pointer/capture,
programs, both lanes, host text, independent keys and recovery. Keep compositor
experiments separate from the running host.
