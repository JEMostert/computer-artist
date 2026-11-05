# The user's desktop on Windows

Use this only when the user explicitly asked you to work on their own desktop;
otherwise use a [private environment](environments.md), which runs Linux and web
apps (Windows apps cannot run there). The agent lane has its own on-screen ink pen
and never calls SendInput, changes the foreground window or touches the clipboard.
Use `ca`, or the checkout's `.venv\Scripts\ca.exe`.

## Steps

1. **Start.** Run `ca capabilities`, `ca monitors` and `ca windows`, then
   `ca doctor --window ID`. Bind the exact window with `ca set ID --name app`. When
   the user chose a display, pass its ID from `ca monitors` as `--monitor` on every
   command (or set `CA_WINDOWS_MONITOR`); the target must fit inside it.
2. **Work.** Search `ca memory search "TASK"` first and reuse a fitting memory
   ([workflow memory](memory.md)). Keep the user's focus in another application.
   Then observe, act and observe again:
   - `ca observe --window app`, and read the image. Coordinates are physical client
     pixels, also at mixed DPI.
   - `ca a11y --window app [--role ROLE] [--name TEXT]` lists UI Automation
     elements; in programs use `ctx.find(role=..., name=...)` and
     `ctx.click(element=...)`. `ca read --window app` returns the sole edit
     control's text without input.
   - Act with `click`, `type`, `press`, `scroll`, or a `ca execute` program
     ([programs](programs.md)). Standard Edit/RichEdit controls and push buttons get
     window messages (caret, whole-text insertion, Ctrl+A, Ctrl+Z, Backspace, Enter,
     Tab, navigation keys). Other elements go through UI Automation: a click invokes,
     toggles, selects or expands; text appends to the value; Enter invokes; scrolling
     moves the nearest scrollable ancestor (15 per wheel notch, positive is down).
     Replies name the `action` or `delivery` used.
   - `ca window move` changes geometry without activating the window; observe again
     afterwards.
3. **Verify.** Read text back (read or snapshot existing text first and change only
   what the task owns), check saved files with `ctx.verify_file`, and look at a fresh
   `observe`. A dispatched action is not proof.
4. **Finish.** Learn a reusable workflow with `ca memory learn NAME --run RUN_ID
   --task "TASK" --app "APP" --notes "PROCEDURE"`. Run `ca stop` to revoke your
   native lanes.

## If something goes wrong

- **Refused as unsupported:** drawing, drags, canvases, apps that read raw input,
  general shortcuts, Alt menus and caret insertion in UI Automation elements are
  not on this lane. Report the refusal; do not switch to `--host`.
- **Focus entered the target process:** the lease stops. Inspect before retrying.
- **`may_include_overlapping_windows: true` in a capture:** the app could not
  render itself, so other windows may show in the image.
- **The host lane is really needed:** only when the user authorized real pointer,
  foreground, keys or clipboard in this task. `--host` uses SendInput and may
  activate the target; `ca type --host` pastes and restores the clipboard, and
  `ca write --host` presses keys one by one. Takeover stops it.
- **Unicode breaks in PowerShell:** pipe ASCII source with Unicode escapes, or a
  file read explicitly as UTF-8.
- **A run must stop:** `ca runs stop ID` cancels one program; jobs enforce hard
  deadlines and clean up child processes.

See [Windows](../../../docs/WINDOWS.md) for what was verified, setup and the backend
design.
