# macOS

Requires macOS and Python 3.12 or later. The native backend controls Mac applications
on the current desktop; Docker Desktop is needed only for private Linux boxes.
Native drawing and text workflows were verified in Chrome on macOS 27.0.1
(Apple M1 Pro) on 15 October 2026. Other native apps and OS versions remain
unverified. This is not a claim of parity with KWin.

```bash
git clone https://github.com/JEMostert/computer-artist.git
cd computer-artist
python3.13 -m venv .venv                       # e.g. brew install python@3.13
.venv/bin/python -m pip install -e '.[dev]'
source .venv/bin/activate
ca doctor                                      # native APIs and permissions
ca setup --codex                               # or --skills-dir PATH
ca windows
ca set WINDOW_ID --name editor
ca observe --window editor
ca a11y --window editor --role 'push button'
ca click --window editor --x 120 --y 80
ca write --window editor 'Hello, world!'
ca session close
```

Grant **Accessibility**, **Screen Recording** and **Input Monitoring** in System
Settings > Privacy & Security to the terminal or application context running `ca`.
Permissions can differ between terminals and IDEs. The backend checks existing permission and does not
open permission prompts automatically. `ca doctor` diagnoses the native backend
independently of Docker; `ca env doctor NAME` checks a container environment.
The native transport sentinel is `macos` (`--socket macos`); an explicit environment
uses its own transport.

## Native behavior and limits

The same CLI supports window binding, observation, read-only accessibility,
conditional programs, gestures, watch, handoff, outcome checks and reusable fragments.
Agent pointer events use private SkyLight delivery; physical keyboard events go to
the selected process, with text resolved against the live keyboard layout. The
agent has its own click-through cyan arrow, drawn without moving the human pointer.
The approach is informed by [Cua's macOS window internals research](https://cua.ai/blog/inside-macos-window-internals);
that research is not verification of this implementation.

You can bring the target to the foreground and watch. An initial click that
focuses a background target is allowed. A listen-only Input Monitoring guard stops
on real keyboard input in the target application, clicks in the already-foreground
target window, or drags/scrolling over that window, including background scrolling.
Hovering alone does not revoke control. Attribution uses event type, event-time
location and foreground PID; it does not read key values or text, or log input.
Agent events are tagged and excluded. Listener failure stops input.

One lane owns an application PID at a time. Per-process keys share the app's
keyboard window: another foreground window in the same app causes refusal rather
than rekeying. Simultaneous editing within one app is unsupported. Targets must be
on the current Space; the backend does not switch Spaces. Target loss or changed
geometry stops input. The agent never restores or changes human focus and has no
fallback to shared input. Foreground watching and takeover are covered by portable
regressions; a live human-driven takeover journey remains unverified.

Coordinates are logical points relative to the **whole window frame, including
its decoration**. Captures include that frame and report image pixels; on Retina
displays these differ from input points. Scale screenshot positions using the
reported image and window dimensions before sending input.

Left/right/middle buttons, multi-clicks, pointer paths, drags and scrolling are
implemented. The Chrome workflows below establish a narrower compatibility claim.
IME, dead-key composition and application data drag-and-drop are unsupported. The
agent lane has no clipboard. Explicit `--host` selects the human pointer,
activation and clipboard; host paste uses Cmd+V. Host activation/paste and Retina
coordinate scaling remain live-unverified. Private APIs may change across OS
versions, and apps may reject posted events. Input separation is not a security
sandbox.

## Private Linux environments

Docker Desktop environments are retained for Linux and web application testing;
Mac applications cannot run inside them. They have their own KWin desktop, input,
clipboard and session bus.

```bash
ca env start web --project .
ca env exec web --name editor --wait-window --timeout 120 -- kwrite /project/notes.txt
ca --environment web observe --window editor
ca env doctor web
ca env stop web
```

## Status

Status: **implemented** means the backend code exists; **verified** names a
specific live workflow. On 15 October 2026, Chrome on macOS 27.0.1 (Apple M1 Pro)
was exercised through the native CLI and supervised programs:

- `ca click` selected Rectangle; a 119-point drag drew a visible rectangle.
- Physical `ctx.write` entered `Computer Artist on macOS` with correct case.
- A 159-point SVG Bézier produced a visible curve and passed `verify_change`.
- Right-click opened Chrome's actual context menu. Scrolling by 160 and a middle
  button drag changed the canvas, passed change checks and were reversed.
- Capture and AX exact window-ID/frame mapping were verified. A forced 1.5-second
  deadline while holding Shift left the cleanup ledger's keys/buttons empty.

The foreground app stayed unchanged across these actions. The human cursor stayed
unchanged during the click, rectangle, text, Bézier, right-click and scroll checks;
the user moved it independently during later commands, so no broader cursor claim
is made. Native tests include portable takeover regressions; they do not verify
live human takeover, host input, Retina scaling or other applications.

The container results below are separate from native application evidence.

| Feature | macOS scope | Status |
| --- | --- | --- |
| Private environments | Linux box per environment in Docker Desktop; state in `~/.local/state/computer-artist/environments`; host/box path translation | Verified 15 Oct (Docker Desktop 4.95, Apple silicon) |
| Apple silicon | The Arch-based box runs as `linux/amd64` under Docker Desktop's emulation; first build about 3 minutes, later starts about 20 seconds | Verified 15 Oct |
| Apps in the box | KWrite type/save with a fresh-file check, Firefox with a private profile, recipe-derived images, startup apps, AT-SPI tree | Verified 15 Oct |
| Host dev servers | `http://host.docker.internal:PORT` reaches a Mac server listening on all interfaces | Verified 15 Oct; loopback-only servers unverified |
| Container runtime | Docker Desktop only, as on Windows; selecting Podman is refused | Implemented |
| Native Mac desktop | Pointer drawing, physical text, right-click, scroll, middle drag, capture/AX and deadline cleanup | Verified 15 Oct in Chrome, macOS 27.0.1, Apple M1 Pro; see workflows above |
| Native takeover | Event-time input attribution, foreground watching and same-app keyboard-window refusal | Implemented; portable regressions; live human takeover unverified |
| Native host input and Retina | Explicit host activation/Cmd+V; capture pixel-to-point scaling | Implemented; live unverified |

The box image is about 5 GB. Docker Desktop's default memory limit is enough for
one or two environments; raise it in Docker Desktop's settings for more. `network
= "host"` depends on Docker Desktop's host networking setting and is untested. The
existing container results of 15 October 2026 are summarized in
[docs/validation/2026-10-15-macos.json](validation/2026-10-15-macos.json).
