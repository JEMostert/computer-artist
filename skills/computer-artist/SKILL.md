---
name: computer-artist
description: Operate existing KDE Wayland applications with Computer Artist (ca), using an independent agent pointer, bounded Python action programs, screenshots, and reusable small actions. Use for desktop interaction and drawing through CA.
---

# Computer Artist

Use `ca` to interact with applications on the user's actual desktop. If it is not
on PATH, resolve this skill directory's symlink: the checkout containing
`skills/computer-artist` has an executable `ca` at its root. Quote its absolute
path when invoking it from elsewhere. Setup installs instructions only; a loaded
Computer Artist KWin plugin is required for desktop commands.

## Observe, act, verify

Start with `ca capabilities`, `ca windows`, and `ca observe --window WINDOW_ID`.
Select the live ID from window metadata, never a remembered ID. Open the returned
PNG with your image-viewing tool; the JSON response does not give you vision.
Check `ca memory list WINDOW_ID` for useful actions before writing another.

Use `ca execute` for a bounded sequence of related actions, loops, and local
condition checks. Inspect at meaningful checkpoints such as tool changes,
dialogs and completed drawing layers. Prefer `ctx.wait_for` to guessed long
sleeps. Read [programs.md](references/programs.md) for examples, coordinates,
memory registration and explicit host control.

For practical planning, batching, coordinate mapping and recovery, read
[operating tips](references/operating-tips.md) before a substantial desktop task.
For drawing tasks, also read [pointer techniques](references/pointer-techniques.md):
they include the tested polygon completion, color-dialog and fill techniques
from the project's two-painting demonstration. Start with these known methods;
check the current layout instead of rediscovering the interaction from scratch.

Default input uses the independent agent pointer. The human's pointer and
keyboard focus must be outside the target application's Wayland connection.
Multiple windows can share a connection. Human takeover is a stop signal;
re-observe before resuming, and do not repeatedly reacquire against human input.

Sessions open automatically on input and persist between commands. Keep the
session while carrying out the task; use `ca session close` when done or handing
back control. This stops both lanes and hides the agent cursor. `ca stop` releases
only the selected lane without ending the whole session.

## Limits that affect decisions

- Check live capabilities. The current plugin is pointer-only for native Wayland
  windows: keyboard injection, clipboard injection, XWayland, popup grabs and
  application drag-and-drop are unsupported. A command appearing in help does
  not mean the backend implements it.
- `--host` explicitly moves the real pointer or changes desktop focus. Use it
  only within the user's authorized host-control scope; never silently switch
  lanes to bypass an agent refusal. A dialog may activate itself even when the
  user did nothing. Re-list windows and inspect state; with authorized host
  control, focus another application and move the real pointer outside the
  target connection before continuing. Otherwise explain the specific block.
- Captures read main-surface buffers, excluding decorations/subsurfaces. Some
  GPU buffers cannot be read. Report that limit or use an available, authorized
  screenshot tool; do not act using an invented image.
- Coordinates are logical window-content pixels. Screenshot pixels may be
  scaled. Convert using returned image dimensions and logical window geometry;
  include crop offsets. Geometry changes invalidate planned coordinates.
- A dispatch or `returned_unverified` result is not task success. Inspect the
  outcome or check a saved artifact. `verified` means only the program's named
  checks passed; changed pixels alone do not prove a correct drawing or save.
- After an interruption or uncertain reply, inspect the actual state before
  repeating a mutation. Stop or yield if the UI no longer matches the plan.

Keep reusable memory focused on parameterized steps, not entire fixed tasks.
Programs run as ordinary local Python with the user's permissions. Temporary
observations and run records rotate; save requested artwork to a durable path.
Use `ca storage keep RUN_ID --scope runs` when a run's evidence must survive.
