# Computer Artist — capabilities

## Product and current status

Computer Artist is a local `ca` CLI for applications on the user's actual desktop.
The implementation is a native plugin built against packaged KWin. The patched
KWin repository and its deployment machinery have been removed.

Implemented host lane: explicit `--host` pointer input and window focus on native
Wayland, separate lane leases/watchdogs, same-connection conflict checks, and
interruption on other source-device input. Sessions auto-create on input; read-only
commands leave them closed. Parallel programs share deadlines/budgets. Host
keyboard injection and prevention of agent-dialog automatic focus remain absent.
See [host-lane scope](docs/HOST-LANE.md).

Implemented in the CLI harness: typed, versioned modular Python fragments; stdin
registration; nested calls; a shared fragment library;
supervised deadlines; observations and pixel diffs; agent-defined guarded regions;
condition-based waits and feedback-controlled paths; named outcome checks and
execution records. See [the programming API](docs/WINDOW-API.md).
Automatic control discovery, accessibility/app adapters and model observers remain
future work. A module's successful checks do not establish general compatibility.

Implemented: independent pointer delivery to an existing native Wayland app,
animated agent cursor with automatic sessions and explicit close, connection ownership, human takeover, bounded Python
programs, traces, and capture of readable main-surface buffers. A drawing workflow
in an already-open KolourPaint window was verified on the desktop; see
[validation](docs/VALIDATION.md). This does not establish general app compatibility.

The human pointer and keyboard focus must be outside the target app before
acquisition. Returning to it revokes agent control. Working simultaneously inside
the same app is not required; preserving the human's own cursor is the priority.
Keyboard delivery, XWayland, clipboard, IME, menus/popups and data drag-and-drop
are unsupported. There is no fallback to human input or focus stealing.

Dedicated harness integrations and remote desktop transport are out of scope.
Private desktops and Xvfb do not meet the product goal. A separate stock KWin
instance is only a development test harness. Input isolation is not a security
sandbox; programs run with the user's permissions.

## Intended capabilities and evidence of success

| Capability | Intended behavior | Concrete acceptance example |
| --- | --- | --- |
| Independent agent input | Agent has its own pointer, keyboard focus, modifiers, and held buttons; human input remains independent | Human types a known string in a terminal while the agent draws and saves in another app; no missing or misplaced keys, cursor jumps, or stolen focus |
| Work in the existing desktop | Assign supported running applications to the agent and return them to the human | Assign an already-open document, edit it, and hand it back without restarting the app or losing state |
| Visible activity and handoff | Show the agent's actual cursor and ownership; stop, pause, resume, and take over | Take over during a drag; pending agent actions stop, held input is reconciled, and resume uses fresh state |
| Continuous observation | Maintain fresh frames, window geometry, useful accessibility/app events, and a bounded recent history | Detect a new dialog or resized canvas while a program is running; memory stays bounded |
| Conditional action programs | Execute short Python programs with loops, branches, waits, deadlines, and typed failures through the local CLI | Handle two known save-dialog variants locally and return an unknown dialog to the planner with evidence |
| Precise drawing and gestures | Lines, Bézier curves, SVG paths, and parametric paths with explicit coordinates and controlled timing | Draw a specified curve through actual app input and independently compare the saved result to the requested shape |
| Feedback during actions | Adjust or stop using observed state rather than relying entirely on fixed trajectories | Drag until a snap indicator appears; stop on target loss or unexpected geometry changes |
| Semantic actions | Use validated app APIs, browser automation, and accessibility alongside pointer input | Address a supported control by identity and verify its effect without disturbing human focus |
| Visual and temporal understanding | A compact observer answers focused questions from images or short clips and emits relevant events | Distinguish an export progressing from one stalled; recognize a transient notification with timestamped evidence |
| Verified completion and recovery | Separate dispatch, observed effect, verified outcome, and uncertainty | Confirm the exported file decodes and contains the expected result; inspect an uncertain save before retrying |
| Reusable interactions | Store parameterized procedures with preconditions, outcome checks, and version/context limits | Reuse an export procedure with a new filename and layout; invalidate it when the UI no longer matches |
| Diagnosis and evidence | Report capabilities by app/backend; provide traces, event queries, and inspection | Explain why an action stopped with relevant frames and state; inspecting a trace does not replay input |

Pressure input, simultaneous work inside the same application, and general
remote-desktop use are possible extensions. They need separate compatibility
and coordination work; two seats alone do not make concurrent document editing
correct. Remote transport is not required for the first local demonstration.

## Build sequence

1. Maintain the working pointer plugin and CLI. Keep builds independent of a KWin
   source checkout and test lifetime, takeover and existing-app compatibility.
2. Broaden native Wayland app testing, including scaling, geometry changes and
   capture limits. Record Qt, GTK and browser results separately; treat XWayland
   as a separate investigation.
3. Investigate keyboard input and popup/grab behavior against packaged KWin.
   These are proposals, not implemented features. Never substitute human focus.
4. Add continuous observation, bounded recent evidence, condition-based waits and
   adaptive gestures. Re-resolve stale targets before acting.
5. Add outcome verification and reusable procedures with explicit preconditions,
   recovery and version/context limits. Keep CLI delivery central.

The broader capabilities above remain a roadmap. A dispatched action or returned
Python program is not proof of task completion. Prefer deterministic app events
and frame differences before model inference, and measure benefit on held-out
workflows. Ownership must account for shared documents and app-global resources.

## Evaluation and longer-term experiments

Use resettable tasks covering text entry, forms, menus, file dialogs, exports,
canvas drawing, drag/drop, loading, and transient notifications. Include resizing,
scaling, occlusion, popups, held modifiers, crashes, timeouts, and human takeover.
Publish an app/version compatibility matrix and repeat tests with recorded seeds.

Compare against both screenshot/action loops and conditional programs with
semantic actions and condition-based waiting. Measure verified success, false
success, completion time, planner calls, observer cost, input interference,
cancellation/correction latency, and CPU/GPU/memory load. Count failed runs.

Later experiments preserve the original ambitions: adaptive observation, expected
UI-effect checks, temporal understanding, learned procedures, feedback-controlled
gestures, structured state-transition prediction, speculative planning without
speculative side effects, and optionally a distilled local controller. Each needs
a measured benefit against a simpler baseline and evaluation on held-out tasks.
