# Windows operation

Use `ca` (or the checkout's `.venv\Scripts\ca.exe`). Start with `ca capabilities`,
`ca monitors`, `ca windows` and `ca doctor --window ID`. Bind an exact live ID with
`ca set ID --name APP`, then inspect `ca observe --window APP` and its image.
Coordinates are physical client pixels on Windows, including at mixed DPI.

When the user chooses a display, use its exact device or EDID ID with `--monitor`.
Keep that selector on every command or set `CA_WINDOWS_MONITOR` for the task.
Targets must fit entirely within it. Private KDE environments are unavailable;
never replace them with the user's desktop without the task's authorization.

## Independent input

The default agent lane sends bounded messages to standard Edit/RichEdit controls
and push buttons. It uses its own animated click-through pointer and local
modifier state, and does not call SendInput, set OS focus or use the shared
clipboard. Keep the human's focus in another application. Returning to any window
of the target process stops the lease. Different processes may be controlled
concurrently; another lane cannot own the same process.

```powershell
ca --monitor PHL093F set ID --name editor
ca --monitor PHL093F observe --window editor
ca --monitor PHL093F write --window editor 'Hello'
ca --monitor PHL093F read --window editor
ca --monitor PHL093F window move --window editor --x 450 --y 180
```

Select one supported control by moving/clicking inside it when the window has
multiple edit controls. Text insertion is undoable and supports Unicode and
line breaks. Ctrl+A and Ctrl+Z, unmodified navigation, text and
standard push-button notifications are supported. General shortcuts, shared
clipboard, checkboxes/radios, scrolling, drawing and arbitrary canvas input are
not implemented in this lane. Do not infer browser or Ninja-terminal compatibility
from Notepad: WinUI Notepad and disposable standard controls were verified;
Chromium/web-terminal independent input has not been verified.

`ca read` returns the sole standard edit control's text, up to 65535 characters,
without input. `ca window move` changes outer geometry without activation; bind
and observe again afterwards before using old layouts. Screenshots are captures
of the visible client region and can include overlapping windows. They do not
prove that an obscured app rendered its own content.

## Explicit host input and verification

Use `--host` only when the task authorizes real pointer, foreground, keys or
clipboard changes. It uses SendInput and may activate the target. Secure desktops
and higher-integrity applications remain unavailable. Foreground or pointer
takeover stops input. General application-created dialog activation cannot be
prevented. The agent lane never falls back to this mode.

Use bounded `ca execute` programs and `ctx.verify`/`ctx.verify_file` for exact
outcomes. On Windows, `ca stop` revokes native lanes, `ca runs stop ID` cancels
one recorded program, and jobs enforce hard deadlines and descendant cleanup.
Leases and pointer overlays end when their process ends. Verify read-back rather
than treating successful message dispatch as successful editing.

For PowerShell pipelines use ASCII source with Unicode escapes, or a source file
explicitly read as UTF-8. A console's encoding may otherwise corrupt Unicode
before the backend receives it. Preserve existing text: read/snapshot it before
editing, append or select only task-owned content, and check for concurrent edits.

`ca setup --skills-dir ~/.codex/skills` installs a managed copy on Windows without
administrator privileges. The `.ca-install.json` record identifies its source.
Re-running setup or `ca update` refreshes unmodified copies and preserves local
edits. A new agent turn discovers the installed skill.
