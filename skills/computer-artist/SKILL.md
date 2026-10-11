---
name: computer-artist
description: Use the ca CLI to test and interact with GUI applications, preferring private agent environments; control the actual KDE Wayland desktop only when explicitly requested.
---

# Computer Artist

Use `ca` from PATH or this skill's checkout. The CLI is the interface; Python
`run(ctx)` programs are submitted through it, not a separate SDK. Desktop work
requires the loaded KWin plugin. Skill setup does not deploy or update that plugin.

## Select the environment first

For development and testing, default to a private environment: its own KWin
desktop, seat, clipboard, D-Bus session (with desktop portals) and optionally a
rootless Podman container for the app. Use distinct names for concurrent agents
and separate worktrees for concurrent edits. Mounted folders stay shared; a
private desktop is not a security sandbox. Stop what you started; never stop
another agent's environment. If an environment fails, report the blocker; never
fall back to the user's desktop or main browser.

```bash
ca env start TASK --image IMAGE --project .          # or --containerfile FILE
ca env start TASK --recipe ca-env.toml               # image, mounts, limits, startup apps
ca env exec TASK --name app --wait-window TITLE -- program args
ca env exec TASK --wait -- npm test                  # one-off command, returns status/output
ca env browser TASK --family chromium --executable chromium --name web
ca --environment TASK windows                         # every ca command takes --environment
ca env doctor TASK; ca env logs TASK a1; ca env status TASK
ca env stop TASK                                      # evidence and definition are kept
```

Without `--image`/`--containerfile`, apps run from the host in the private desktop
(`ca env exec --on-host` does this inside container environments). Container apps
see mounts at their host paths; their HOME is `status.home`, readable on the host,
so write test evidence there or into the project. `start` is idempotent and reports
the next command; flags update the stored definition (`ca env show TASK`).
`ca env exec` reports `ok: false`, the exit status and log tail when an app dies.
Read [environments](references/environments.md) for recipes, browsers and limits.

## Observe and interact

All commands below and in references take `--environment TASK` when working
privately. Inside an environment the default lane is that desktop's own seat (no
human uses it); targets are raised automatically. Never drop the selector to
recover from a refusal. XWayland windows can be observed but not driven.

Inspect `ca capabilities`, `ca windows`, `ca doctor --window APP` and a fresh
`ca observe --window APP` image. Name an exact live ID with `ca set ID --name APP`.
After reopening, rebind explicitly; `--title TEXT` must match one window.
Check live capabilities and verify menus/dialogs in the actual app. Agent-lane
menus opened during a lease are observable and selectable in the tested Qt fixture;
do not infer general compatibility. Report native Wayland and XWayland separately.

Actual-desktop control remains available when the user explicitly requests it.
Only in that mode omit `--environment`. Human pointer/focus must stay outside the
agent target connection. Host focus/pointer recovery requires authorization in the
current task; this skill itself does not grant it. Never silently move the human's
pointer or steal focus. Do not use host input to perform a refused agent action.
Human takeover stops input; inspect before retrying. App dialogs can change human
focus; do not promise prevention.

When `ca a11y --window APP` lists the controls you need, locate them by role and
name with `ctx.find` instead of mapping pixels; it is read-only and still needs
outcome checks. Otherwise inspect `window/layout/APP/`; map the controls/work area
needed now. Stable controls
use `ca target APP NAME --observation OBS --rect X Y W H` and guarded
`ctx.click(target='@NAME')`. Recheck old maps; do not bypass failed guards.
Changing canvases use verified geometry, not artwork pixel hashes. Coordinates
are logical content pixels; account for capture/display scaling.

Reuse or register small typed fragments under `window/api-fragmants/`, then compose
bounded `ca execute` programs with conditions and loops. Inspect actual transitions
and correct unexpected results before continuing. Task-specific geometry belongs
in the task; reusable fragments read maps. See [programs](references/programs.md).

Independent physical keys require advertised keyboard support and ready target
resources. Use `ctx.press`, `key_down`, `key_up` and `ctx.write`/`ca write` for
layout-resolved text; verify effects before release.
Unicode paste uses explicit host focus/clipboard within the task's authorization.
Snapshot the destination before saving and use `ctx.verify_file` afterwards.
Dispatch, a screenshot or a child's passing checks do not verify the outer task.

Use `ca watch` for bounded JSONL observation, `ca runs inspect ID` for evidence,
`ca runs stop ID` for targeted stopping and `ctx.handoff(reason)` to release/yield.
Inspect related-window candidates and choose an exact ID in a fresh run; interrupted
contexts cannot resume. Checkpoints can miss recent actions and never prove completion.
Keep required evidence with `ca storage keep ID`; five eligible runs rotate and
captures are separately bounded. Close the cursor session with `ca session close`.

For drawing read [pointer techniques](references/pointer-techniques.md); for
scaling/recovery read [operating tips](references/operating-tips.md).
