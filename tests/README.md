# Test scope

The 15 October 2026 reduction starts at `0d88ee5`: **379 discovered tests,
370 passed and 9 native Windows skips** on macOS/Python 3.13. It deletes 156
methods (41%), leaving **223 tests: 214 passed and the same 9 skips**. Retained
test bodies are unchanged; no assertions were consolidated or new tests added.
These are revision-specific counts, not a target or a coverage percentage.

After integrating main through `8296af5` (including the five ink-pen tests from
`31dab1a`), the equivalent count is **384 -> 228 discovered**, with **219 passed
and 9 native Windows skips** in 19.215s. All incoming pen tests and Windows fixture
changes are preserved. The portable Windows runner selection passes **81 tests
with 9 native skips** on macOS; lint and formatting pass for 108 Python files.

Tests are kept for their failure consequence: unwanted input, held resources,
false success, lost/corrupt work or crossing a platform boundary. A unique
assertion alone does not justify a permanent regression test.

| Failure consequence / files (`test_*.py`) | Before | After | Retained protection and deletion rationale |
| --- | ---: | ---: | --- |
| Native/platform: macos, macos_activity, windows, box, containers | 55 | 47 | All native backend tests stay; retain routing, user mapping, path translation and no fallback. Remove command-word, build-wrapper and message permutations. |
| Input ownership/preflight: cli, client, agent_keyboard, runtime, input | 66 | 43 | Keep real socket/CLI actions, explicit host gate, key cleanup, deadlines/budgets, poisoned transport, stale targets, shared leases and full SVG preflight. Remove overlapping wrappers, notification internals, aliases and argument variants. |
| Cancellation/handoff: control, recovery, supervisor, watch | 24 | 17 | Keep all hard-worker and targeted-cancel tests, release failures, fresh handoff and XWayland refusal evidence. Remove copied error details, duplicate discovery and polling/budget/retention variants. |
| False export/program success: artifacts, programs | 31 | 22 | Keep fresh-generation checks, malformed output, exact text/images, bounded reads, cancellation, invocation-scoped verification and inline/stored parity. Remove cache call counts, filename/debug formatting and repeated error/report checks. |
| Lost/corrupt run history: storage, records, journal | 38 | 21 | Keep active/preserved runs, cross-process locks, retention races, failed initialization, unsafe markers/files, partial evidence and checkpoint thread cleanup. Remove repeated lifecycle, read-only getter and individual malformed-record variants. |
| Lost/corrupt reusable work: fragments, fragment_integrity | 23 | 16 | Keep non-executing registration, typed constraints, immutable versions, containment, tampering/contract checks, atomic rollback and recovery. Remove listing/history presentation, redundant restore and basic getters. |
| Stale image evidence: observations | 10 | 6 | Keep scaled diffs, concurrent pruning, expired evidence, disappearing targets and revalidation against live pixels/geometry. Remove source labels, lock implementation assertions, legacy migration and a second pruning route. |
| Wrong selection/gesture: accessibility, gestures, text_and_gestures | 66 | 23 | Keep window identity/ambiguity, tree bounds/deadline/bus cleanup, geometry, ordered text preflight, sampled curves/arcs/loops, bounded work, multi-click/drag/button release and unique visible matches. Remove status/getter formatting, forwarding mocks, aliases, scalar button codes and extended syntax variants. |
| Environment boundaries/lifecycle: environments | 29 | 13 | Keep recipes/mounts, declared socket exposure, deletion confirmation, unresponsive-manager refusal, lane/socket selection, app exit/logs and registry startup/cleanup. Remove prototype migration, duplicate definition validation, message wording and registry-path permutations. |
| Maintenance: diagnostics, update, setup, architecture | 37 | 15 | Keep lane restrictions, stale/mismatched plugin detection, busy/reload races and rollback, protected plugin files, safe Git/pip behavior and user skill preservation. Delete architecture/import-shape tests entirely and trim plan/getter/default/message permutations. |
| **Total** | **379** | **223** | **156 deleted methods; one deleted module (`test_architecture.py`).** |

The Windows runner drops only the deleted architecture module. The native macOS,
macOS activity, Windows and supervisor suites, plus every live integration
harness, remain unchanged by this reduction. Existing compatibility exports
remain production API even though their import-shape test was removed.

## Deliberately reduced coverage

Dedicated checks no longer cover every key alias, text newline normalization,
SVG shorthand/arc variant, accessibility status/filter representation,
prototype migration, diagnostic string, update-plan permutation or output
translation format. Some lock internals and rare recovery/reporting branches
also lose individual tests; representative race, corruption, cleanup and
false-success scenarios remain. This accepts later discovery of lower-consequence
edge regressions in exchange for a smaller suite.

## Running

Activate the project venv (Python >=3.12) before `scripts/test.sh` and
`scripts/check.sh`: CLI subprocesses use the `python` on `PATH`. On Windows use
`python scripts/test-windows.py`; executing that selection on macOS verifies only
its portable cases. Portable fakes do not prove live KWin/Wayland/XWayland or
native desktop compatibility. Live integration harnesses require their platform
and were not run for this test-only reduction. No desktop/browser/DB was changed.
