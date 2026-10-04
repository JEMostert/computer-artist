# Capabilities

The goal is independent agent input in existing applications on the user's actual
desktop while the human works elsewhere. Private desktops and Xvfb do not fulfill
that goal. Separate KWin instances are development test harnesses.

| Capability | Implemented scope |
| --- | --- |
| Private environments | Per-agent KWin desktop, seat, clipboard and session bus; rootless Podman apps, recipes, startup apps, private browser profiles |
| Independent input | Animated pointer and physical keys with separate modifier state; native Wayland |
| Human takeover | Pointer/focus return revokes the lease; cleanup on target loss, disconnect, lock and watchdog |
| Explicit host input | Real pointer/focus, physical keys and shared UTF-8 clipboard; requires `--host` |
| Conditional programs | CLI-fed Python, branches, loops, waits, shared budgets and hard process deadlines |
| Observation | Composited client captures, pixel/geometry diffs and bounded continuous JSONL watch |
| Precise gestures | Paths, SVG lines/Bézier curves/arcs, complete preflight and feedback predicates |
| Handoff | Release and yield with evidence; inspect candidates and continue in a fresh run |
| Verified outcomes | Invocation-owned checks and stable, optionally fresh file/text/JSON/image/hash checks |
| Reusable work | Typed fragments, contracts, immutable versions, nested calls and explicit restore |
| Diagnosis | Read-only doctor, refusal details, JSON inspection, checkpoints and targeted stop |

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
App dialogs can activate themselves and change human focus; prevention is absent.
XWayland, independent clipboard/IME, popup grabs and app data drag-and-drop are
unsupported. There is no fallback and input separation is not a security sandbox.

Next: broaden native app/toolkit/scaling tests; investigate keyboard and popup
compatibility separately; add validated semantic app/accessibility APIs and
visual/temporal observers where they measurably help. Automatic control discovery,
model observers and remote transport are not implemented. Preserve conditional
programs, continuous observation, precise gestures, handoff, verified outcomes
and reusable procedures when simplifying.

Evaluate resettable workflows for verified success, false success, input
interference, cancellation latency, completion time and resource use. Count failures
and report native Wayland/XWayland separately.
