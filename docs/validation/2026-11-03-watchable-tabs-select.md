# Watchable Chrome tabs and native selects, 3 November 2026

Assigned checkout: `fix/watchable-parallel-browser-tabs`, macOS 27, Python 3.13.
No commit, push, PR or changes to the root checkout are authorized by this run.

## Diagnosis

The existing `onActivated` handler ended the claim belonging to the activated tab
with `human_activated_tab`. The open path separately refused a tab activated while
it loaded. Neither path attributed activation to human page input. There is no
window-focus takeover handler. Grouping follows claiming and can produce browser
activation notifications without proving human interaction. The prior two demo
tabs retained that stop reason, but their historical event initiators were not
recorded and cannot be reconstructed. Two fresh sequential background opens did
not reproduce spontaneous activation.

A Terra delegate reproduced the native select limitation in actual installed
Chrome: click, ArrowDown and Enter returned, but a fresh locate showed the
unchanged initial option. Untrusted keyboard events have no native select default
action; `type` also correctly refuses non-editable select elements.

## Implementation

Tab activation is observation and preserves leases, including during open.
Trusted pointerdown, keydown, wheel and touchstart page events still end only the
sender tab's claim. Hover/focus are not takeover. Real page input during opening
is tracked separately so removing activation refusal does not permit overwriting
a human-edited loading tab. Browser toolbar/address-bar input is outside this
page-input guard. No global takeover protection is disabled.

`ca browser select --tab H --lease L --selector '#colour' blue` selects one exact
native single-select value. Locate reports options. The action shares lease and
pen interruption checks, uses the native DOM value setter and emits bubbling
input then change only for a changed selection. Disabled controls/options and
optgroups, duplicate/missing option values, multiple selects and non-selects are
refused. Events remain untrusted. No OS popup, pointer/focus borrowing, arbitrary
script command or debugger permission is introduced.

## Runtime and evidence

Installed extension 0.5.2 from this worktree with `ca browser install --browser
chrome`; launcher points to this worktree and the existing Python environment.
Previous installed files are backed up in `output/runtime-backup`. Installed
background.js and manifest.json were checked byte-for-byte against source.
A browser extension-card reload is still needed to load copied files; installation
alone is not live proof.

Portable suite: `PATH=/Volumes/Dev/code/ai/computer-artist/.venv/bin:$PATH
/Volumes/Dev/code/ai/computer-artist/.venv/bin/python -m unittest discover -s tests`
passed 232 tests (10 platform skips); log `output/unit-suite.log`.
The initial run without the venv on PATH failed subprocess CLI tests because the
system Python lacks tomllib; rerunning with the correct PATH resolved those failures.

Real Chrome regression: all 35 checks passed in disposable headless Chrome for
Testing 153 on macOS. `TMPDIR=/tmp` is required because default macOS temporary
paths exceed the Unix socket path limit. Command: the same Python with
`tests/integration/check-browser.py --chrome <CFT-153-binary> --idle 0 --evidence
output/watchable-regression.json`. Verified select value/options/event order and
unchanged suppression, invalid/disabled/optgroup/multiple/non-select refusals,
activation during ownership and loading, real input during loading, relevant-tab
only takeover, iframe input, lease checks, reconnect and cleanup. Human input was
DevTools-emulated. Idle keepalive was intentionally not rerun.

The actual-user-Chrome three concurrent delegates and human tab viewing are
pending the extension reload. No claim of three saved submissions is made yet.

## Live three-agent run and focus (extension 0.5.3)

User's Chrome 156 on macOS, instance `5cb4db89`, after the user reloaded 0.5.3.
Three GPT-6.1 Terra delegates each opened their own tab on a localhost form
(`tests/integration/parallel_browser_fixture.py`) and waited on a shared start
gate. Their form actions overlapped between 15:24:01 and 15:24:43 UTC. During
that window the coordinator ran `ca browser focus` on agent-a, b and c in turn,
nine times about 7 s apart. Every focus returned `active: true` and the focused
lease stayed held. No run logged `human_activated_tab` or `human_input`.

The server stored three records with the expected unique name, note,
`agree=on`, and colour red/green/blue, each with exactly `input` then `change`
from `ca browser select`. agent-a finished in one pass. agent-b and agent-c each
made their own scripting mistakes: a nonexistent `button[type=submit]` selector,
and repeating the sequence so that Agree was unchecked and the form's required
check blocked Save. Follow-up delegates finished in the same tabs under the
original leases. CA itself refused nothing. The coordinator, not the user, drove
the viewing with `focus`; user-initiated tab switches were not separately
recorded in this run.
