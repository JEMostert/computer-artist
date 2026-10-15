# Windows

Read this only on Windows. The rules in SKILL.md apply as well. The native backend
needs no KWin plugin. Use `ca` (or the checkout's `.venv\Scripts\ca.exe`).

## Private environments: Linux boxes

For development and testing, default to a private environment. On Windows each one
is a Linux box: a Docker Desktop container running its own KWin desktop. It
tests Linux and web applications; Windows applications cannot run inside it.
Docker Desktop with the WSL 2 backend is the expected runtime; if `ca env start`
reports that no runtime is installed or that the daemon is not running, stop and
tell the user (see SKILL.md). Never fall back to the user's desktop. Commands,
paths and recipes are in [Linux boxes](environments.md#linux-boxes-windows-and-macos).

## The user's desktop (only when explicitly requested)

Start with `ca capabilities`, `ca monitors`, `ca windows` and
`ca doctor --window ID`. Bind an exact live ID with `ca set ID --name APP`, then
inspect `ca observe --window APP` and its image. Coordinates are physical client
pixels, including at mixed DPI. When the user chooses a display, pass its device
or EDID ID from `ca monitors` with `--monitor` on every command (or set
`CA_WINDOWS_MONITOR`); targets must fit entirely within it.

### Independent agent lane (default)

The agent has its own animated, click-through ink-pen pointer drawn on screen. It never
calls SendInput, never changes the foreground window and never touches the shared
clipboard. Keep the human's focus in another application; focus entering any
window of the target process stops the lease. Different processes may be
controlled concurrently.

Two deliveries, chosen per element under the agent pointer:

- Standard Edit/RichEdit controls and push buttons get targeted Win32 messages:
  caret placement, whole-text insertion (undoable, Unicode, line breaks),
  Ctrl+A, Ctrl+Z, Backspace, Enter, Tab, unmodified navigation keys, and button
  notification. In RichEdit (including Notepad) a click places the caret through
  UI Automation; the reply says `selected_caret_unchanged` when it could not.
- Every other element goes through UI Automation patterns: a click invokes,
  toggles, selects or expands/collapses it; text and character keys append to its
  value, Backspace removes the last character, Enter invokes; scrolling uses the
  nearest scrollable ancestor. Replies name the `action` or `delivery` used.

`ca a11y --window APP [--role ROLE] [--name TEXT]` lists UI Automation elements
(roles use AT-SPI names such as `push button`, `check box`, `text`, plus
`uia_control_type`). In programs use `ctx.find(role=..., name=...)` and
`ctx.click(element=...)`. `ca read --window APP` returns the sole edit control's
text (or the sole editable element's value) without input.

Not supported in this lane: drawing and drags, canvases and apps that read raw
input or the cursor position, general shortcuts and Alt menus, text insertion at a
caret in UI Automation elements (it appends), and any app that ignores patterns.
Such actions are refused; report the refusal instead of switching to `--host`.
Application compatibility beyond Notepad and test fixtures is unverified: check
each app with a fresh observation and read-back.

Captures render the window's own client area (no overlapping windows, no agent
pointer). When an app cannot render itself the reply says
`may_include_overlapping_windows: true`. `ca window move` changes geometry without
activation; observe again afterwards.

### Explicit host lane

Use `--host` only when the task authorizes real pointer, foreground, keys or
clipboard changes. It uses SendInput and may activate the target. `ca type --host`
pastes the whole text and restores the user's clipboard (all formats) unless the
user copied something new meanwhile; `ca write --host` presses keys one by one.
Foreground or pointer takeover stops input. Secure desktops and higher-integrity
applications remain unavailable. Scroll deltas use the same units as Linux (15 per
wheel notch, positive is down).

## Programs and verification

Use bounded `ca execute` programs and `ctx.verify`/`ctx.verify_file`. `ca stop`
revokes native lanes, `ca runs stop ID` cancels one recorded program, and jobs
enforce hard deadlines and descendant cleanup. For PowerShell pipelines use ASCII
source with Unicode escapes, or a file read explicitly as UTF-8; the console
encoding may otherwise corrupt Unicode. Preserve existing text: read or snapshot it
first and change only task-owned content.

`ca setup --skills-dir ~/.codex/skills` installs a managed copy without
administrator privileges and never replaces a skill it did not install. Re-running
setup or `ca update` refreshes unmodified copies and preserves local edits.
