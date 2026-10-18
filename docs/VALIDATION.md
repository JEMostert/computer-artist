# Validation

Source, recorded workflow evidence and the loaded host plugin are separate. Python
tests do not prove desktop compatibility. This simplification does not deploy or
reload the host plugin; inspect its actual build with `ca capabilities`.

On 4 October 2026, 222 Python tests and all separate-KWin suites, now including a
Qt menu popup check, passed with the rebuilt plugin. Private environments were
exercised with rootless Podman: Qt and terminal apps in containers, four concurrent
environments, a private portal and host Vivaldi. See the
[environment record](validation/2026-10-04-environments.json) for limits.

On 11 October 2026, 310 Python tests and all separate-KWin suites passed, adding
layout-resolved agent text in KWrite, accessibility-located clicks in a Qt6 form,
agent stop reasons, wheel frames and window identity. A private environment read a
Qt6 accessibility tree and verified typed text through it. See the
[improvement record](validation/2026-10-11-improvements.json); non-US layouts, GTK
and browser trees remain untested.

On 13 and 14 October 2026, 360 Python tests and all separate-KWin suites passed
both before (main at the Windows merge) and after Docker support, Linux boxes and
clipboard-restoring paste. Linux environments ran with rootless Podman and through
the Docker CLI (against Podman's Docker API, as no Docker daemon was available).
Linux boxes ran with both: KWrite and Firefox launched, a pasted line appeared in
KWrite with the clipboard restored, and captures and box writes reached the
project folder. Docker Desktop on Windows and the new Windows UI Automation, capture
and scroll code were not run; see [Windows](WINDOWS.md).

On 19 October 2026, an isolated baseline reproduced the popup teardown crash;
the fixed build passed the nested-popup lifetime regression. See the
[crash finding and regression record](validation/2026-10-19-popup-lifetime.md).
The live host plugin remains unchanged.

Earlier records (29–30 September 2026: KolourPaint, scrcpy, KWrite and the
removed HTML export) live in git history before this branch; they prove those
revisions, not current source.

Fresh checks: `scripts/check.sh`, Python unittest discovery and
`scripts/test.sh --integration`. Integration builds and launches separate packaged
KWin with the virtual backend. Results cover the exact Qt/KWrite workflows tested,
including ownership, cleanup, verification and recovery. GTK, browsers, XWayland,
IME and popup grabs remain unproven/unsupported. The runner prints report locations.
