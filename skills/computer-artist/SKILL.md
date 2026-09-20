---
name: computer-artist
description: Use when a task requires testing or interacting with windowed applications on KDE Wayland, such as Vivaldi, image and video editors, drawing tools, or game engines.
---

# Computer Artist

Use `ca` from PATH or this skill's resolved checkout root. Desktop actions require
the loaded KWin plugin. Follow **observe → map → fragment → execute → verify**.

To install or refresh this skill in Codex, run `./ca setup --codex` from the
checkout. It links `~/.agents/skills/computer-artist` to this checkout, so updates
follow automatically. Keep the checkout in place; reload skills or start a new
agent session after updates. For older hosts, use `--skills-dir ~/.codex/skills`
instead of `--codex`.

1. Run `ca capabilities`, `ca windows`, and `ca observe --window ID`. Open the image.
2. Inspect `window/layout/ID/`. Before task input, map the controls and work area
   needed now. Recheck existing entries against the live window; don't map the
   entire app. Raw UI coordinates are for creating or repairing maps.
3. Check `ca fragments list`. Reuse or register small parameterized fragments in
   `window/api-fragmants/`. Fragments read named layout entries; never duplicate
   toolbar/palette coordinates inside them. Keep task-specific geometry in the task.
4. Compose fragments in bounded `ca execute` programs. Inspect the first intended
   result after a tool/mode change before repeating it. Use local condition waits.
5. On mismatch, inspect and repair the map or its supported UI-state variants.
   Change a fragment only when behavior changes. Never bypass a failed guard.
6. Inspect the result; verify requested saves from the file. Close with
   `ca session close`. A dispatched action is not verified completion.

Map stable controls with `ca target ID NAME --observation OBS --rect X Y W H`;
use `ctx.click(target='@NAME')`. Selection highlights may need separately mapped
states. Map changing canvases by geometry; use canvas-relative drawing coordinates,
not an exact pixel guard on artwork. See [workflow](references/programs.md).

Keep maps/code in `window/`; images, task scripts, and logs in `output/DATE_RUN/`.
Five runs rotate; preserve needed evidence with `ca storage keep RUN_ID`.

Default input is independent; human pointer/focus must be outside the target
connection. Stop on human takeover. Never silently switch to `--host`.
Keyboard, clipboard, XWayland, and popup grabs are unsupported by the current
plugin. Coordinates are logical content pixels; account for screenshot scaling.
Re-observe after geometry changes or uncertain replies.

For drawing, read [techniques](references/pointer-techniques.md); for coordinate
conversion and recovery, read [operating tips](references/operating-tips.md).
