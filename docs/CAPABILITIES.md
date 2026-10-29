# Capabilities

The table and validation below describe the Linux (KWin) backend. Windows has a
separate backend whose agent lane uses window messages and UI Automation instead
of a second input device; see [Windows](WINDOWS.md). It is narrower than the KWin
lane: no drawing, drags or canvas input. macOS has an implemented native backend
using private SkyLight pointer delivery and per-process physical keys, with its own
ink pen, capture and AX reader. Drawing, physical text, right-click, scrolling,
middle drag and deadline cleanup were verified in Chrome on macOS 27.0.1 (Apple
M1 Pro). Foreground watching is allowed; event-time input takeover and same-app
keyboard-window refusal have portable regressions. Live human takeover, other
apps/OS versions, host input and Retina scaling remain unverified. These limits
describe the independent lane; opt-in borrowed drags (`--host-drags`) were verified
separately with Pinta and temporarily use the human's mouse. The shared program,
observation, gesture, handoff, checks, fragment and memory workflows apply. See
[macOS](MACOS.md) for permission, Space, app-wide ownership and Retina coordinate
limits. Both platforms also retain private Linux boxes. The Linux evidence below
does not verify either native backend.

The goal is independent agent input in existing applications on the user's actual
desktop while the human works elsewhere. Private desktops and Xvfb do not fulfill
that goal. Separate KWin instances are development test harnesses.

| Capability | Implemented scope |
| --- | --- |
| Private environments | Per-agent KWin desktop, seat, clipboard and session bus; Podman or Docker apps, recipes, startup apps, private browser profiles; Linux boxes on Windows and macOS |
| Independent input | Animated pointer and physical keys with separate modifier state; layout-resolved text entry; multi-clicks, drags, complete wheel frames; native Wayland |
| Accessibility | Read-only AT-SPI trees: roles, names, states, text and window-content bounds; locate and click controls by role/name |
| Human takeover | Pointer/focus return revokes the lease with a reported stop reason; cleanup on target loss, disconnect, lock and watchdog |
| Explicit host input | Real pointer/focus, physical keys and shared UTF-8 clipboard; requires `--host` |
| Conditional programs | CLI-fed Python, branches, loops, waits, shared budgets and hard process deadlines |
| Observation | Composited client captures, pixel/geometry diffs and bounded continuous JSONL watch |
| Precise gestures | Paths, SVG lines/Bézier curves/arcs, complete preflight and feedback predicates |
| Handoff | Release and yield with evidence; inspect candidates and continue in a fresh run |
| Verified outcomes | Invocation-owned checks and stable, optionally fresh file/text/JSON/image/hash checks |
| Reusable work | Typed fragments, contracts, immutable versions, nested calls and explicit restore |
| Workflow memory | Local task/app search, semantic notes, durable learned program/evidence summaries, guarded reuse and recent attempt timings; the calling agent teaches and verifies flows |
| Diagnosis | Read-only doctor (including stale/mismatched plugin builds), refusal details, JSON inspection, checkpoints and targeted stop |

Workflow memory is agent-assisted: the agent searches local task/app/notes,
inspects the live UI and chooses a compatible stored procedure or adapts it. A
verified completed run with usable initial-window evidence can supply an executable
fast path. Memories preserve source and outcome summaries, not all original
captures or exported files. Existing guarded targets and versioned fragments
remain supported. See [workflow memory](../skills/computer-artist/references/memory.md)
and the [dated painting benchmark](WORKFLOW-MEMORY-BENCHMARK.md). The benchmark
verifies a private Linux-box workflow, not native macOS/Windows compatibility or
automatic recovery from changed layouts.

Native pointer workflows were demonstrated in KolourPaint, Qt fixtures and scrcpy.
Independent keyboard save/concurrent typing was reported for KWrite in separate
KWin. These are specific workflows, not general toolkit compatibility;
see [validation](VALIDATION.md).

Private environments are a development and testing mode, not the product goal:
they let several agents test apps in parallel without the user's desktop. Their
own seat is driven through the host lane; XWayland windows there are observable
only. Four concurrent container environments were exercised on 4 October 2026.

Human pointer/focus must stay outside the target connection. Simultaneous editing
within one application is not required. Two lanes cannot own the same connection.
Apps cannot activate themselves with agent input serials (Qt fixture and Vivaldi in
separate KWin, 25 Oct 2026); new app windows and dialogs can still take human focus.
XWayland, independent clipboard/IME, popup grabs and app data drag-and-drop are
unsupported. There is no fallback and input separation is not a security sandbox.

Layout-resolved text and accessibility-located clicks were verified with Qt6 and
KWrite fixtures in separate KWin on 11 October 2026; GTK, browsers and other
toolkits' accessibility trees are untested. Next: broaden native app/toolkit/scaling
tests; investigate keyboard and popup compatibility separately; add
visual/temporal observers where they measurably help. Automatic control discovery,
model observers and remote transport are not implemented. Preserve conditional
programs, continuous observation, precise gestures, handoff, verified outcomes
and reusable procedures when simplifying.

Evaluate resettable workflows for verified success, false success, input
interference, cancellation latency, completion time and resource use. Count failures
and report native Wayland/XWayland separately.
