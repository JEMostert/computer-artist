---
name: computer-artist
description: Use the ca CLI to interact with supported windowed applications on KDE Wayland, including drawing, conditional programs and verified saves.
---

# Computer Artist

Use `ca` from PATH or this skill's checkout. The CLI is the interface; Python
`run(ctx)` programs are submitted through it, not a separate SDK. Desktop work
requires the loaded KWin plugin. Skill setup does not deploy or update that plugin.

Inspect `ca capabilities`, `ca windows`, `ca doctor --window APP` and a fresh
`ca observe --window APP` image. Name an exact live ID with `ca set ID --name APP`.
After reopening, rebind explicitly; `--title TEXT` must match one window.
Human pointer/focus must stay outside the target connection. If either is inside,
use explicit `ca --host focus --window OTHER` and `ca --host move --window OTHER
--x X --y Y` to park them in another visible application (prefer the agent's chat).
Observe that application's geometry and choose a safe content point. The user
has requested this recovery; do not ask them to move the pointer manually. Then
retry the task through the agent lane. Do not use host input to perform a refused
agent action. Takeover during work still stops input; inspect before retrying.
XWayland and popup grabs are unsupported;
dialogs can still activate themselves and change human focus.

Inspect `window/layout/APP/`; map the controls/work area needed now. Stable controls
use `ca target APP NAME --observation OBS --rect X Y W H` and guarded
`ctx.click(target='@NAME')`. Recheck old maps; do not bypass failed guards.
Changing canvases use verified geometry, not artwork pixel hashes. Coordinates
are logical content pixels; account for capture/display scaling.

Reuse or register small typed fragments under `window/api-fragmants/`, then compose
bounded `ca execute` programs with conditions and loops. Inspect actual transitions
and correct unexpected results before continuing. Task-specific geometry belongs
in the task; reusable fragments read maps. See [programs](references/programs.md).

Independent physical keys require advertised keyboard support and ready target
resources. Use `ctx.press`, `key_down`, `key_up`; verify effects before release.
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
