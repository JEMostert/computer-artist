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

Earlier records (29–30 September 2026: KolourPaint, scrcpy, KWrite and the
removed HTML export) live in git history before this branch; they prove those
revisions, not current source.

Fresh checks: `scripts/check.sh`, Python unittest discovery and
`scripts/test.sh --integration`. Integration builds and launches separate packaged
KWin with the virtual backend. Results cover the exact Qt/KWrite workflows tested,
including ownership, cleanup, verification and recovery. GTK, browsers, XWayland,
IME and popup grabs remain unproven/unsupported. The runner prints report locations.
