# Validation

Source, recorded workflow evidence and the loaded host plugin are separate. Python
tests do not prove desktop compatibility. This simplification does not deploy or
reload the host plugin; inspect its actual build with `ca capabilities`.

The CLI simplification passed **205 Python tests**, lint/format, skill validation,
a fresh wheel installed outside the checkout and all six separate-KWin suites:
pointer/capture, programs, host lanes, host text, independent keys and recovery.
See the [fresh check record](validation/2026-09-30-simplification.json).
These checks cover the exercised native Qt/KWrite workflows, not general app compatibility.

On 4 October 2026, 222 Python tests and all separate-KWin suites, now including a
Qt menu popup check, passed with the rebuilt plugin. Private environments were
exercised with rootless Podman: Qt and terminal apps in containers, four concurrent
environments, a private portal and host Vivaldi. See the
[environment record](validation/2026-10-04-environments.json) for limits.

Historical evidence from 29 September 2026:

- [Pointer/capture](validation/2026-09-29-gem/plugin-regression.json)
- [Programs/guards/recovery](validation/2026-09-29-gem/harness-regression.json)
- [Lane ownership](validation/2026-09-29-gem/host-lanes-regression.json)
- [Host keyboard/clipboard and KWrite](validation/2026-09-29-gem/host-text-regression.json)
- [Saved artwork](assets/kolourpaint-original.png)

Recorded native pointer scope includes Qt fixtures, KolourPaint and scrcpy.
Independent KWrite save/concurrent typing was reported for the 30 September source
iteration in separate KWin; that iteration did not deploy it to the host.
Historical captures/JSON prove their tested revisions, not current source. Archived
HTML is historical evidence; HTML export and the web interface have been removed.

Fresh checks: `scripts/check.sh`, Python unittest discovery and
`scripts/test.sh --integration`. Integration builds and launches separate packaged
KWin with the virtual backend. Results cover the exact Qt/KWrite workflows tested,
including ownership, cleanup, verification and recovery. GTK, browsers, XWayland,
IME and popup grabs remain unproven/unsupported. The runner prints report locations.
