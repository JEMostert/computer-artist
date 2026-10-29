# Windows

Requires Windows 10/11 and Python 3.12 or later. No KWin, WSL, administrator access,
native build or Linux D-Bus dependency is needed for the desktop backend. Private
environments additionally need Docker Desktop (WSL 2 backend); Podman is not used
on Windows.

```powershell
git clone https://github.com/JEMostert/computer-artist.git
cd computer-artist
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
& .\.venv\Scripts\ca.exe setup --skills-dir "$env:USERPROFILE\.codex\skills"
& .\.venv\Scripts\ca.exe doctor
& .\.venv\Scripts\ca.exe monitors
& .\.venv\Scripts\ca.exe windows
```

Add `.venv\Scripts` to this terminal's PATH, or call its `ca.exe` explicitly.
The skill is discovered on the next agent turn. Setup uses managed copies without
symlink privileges and refuses to overwrite manually edited or unrelated skills.
The bundled skill asks the agent to detect the operating system first and then
follows the guide for the detected platform only.

## Implemented behavior

Status: **implemented** means code and unit tests exist; **verified** names what
was exercised live. Everything added on 14 October 2026 is implemented but not yet
verified on Windows.

| Feature | Windows scope | Status |
| --- | --- | --- |
| Agent pointer | Animated ink pen in a click-through, per-pixel-alpha overlay drawn by `ca`; never receives input, never activates a window | Overlay verified 13 Oct; ink pen (15 Oct) not yet run on Windows |
| Independent text (standard controls) | Caret-position, undoable Unicode insertion into Edit/RichEdit by window messages; no SendInput, focus or clipboard | Verified 13 Oct |
| Independent buttons (standard) | Push-button notification to the parent | Verified 13 Oct |
| RichEdit caret by click | UI Automation text range at the pointer | Implemented |
| Independent UI Automation | Invoke, toggle, select, expand/collapse, value append, Backspace, scroll for WPF/WinUI/UWP/Qt/Chromium elements | Implemented |
| Accessibility tree | `ca a11y --window`, `ctx.find`, `ctx.click(element=...)` through UI Automation, AT-SPI role names | Implemented |
| Capture | PrintWindow of the client area (no overlapping windows, no agent pointer); screen-region fallback is reported | Implemented |
| Explicit host input | SendInput mouse, scan-code keys, layout text, scroll, foreground, clipboard, with `--host` | Verified 13 Oct |
| Host paste | Whole text at once; the user's clipboard (all bounded formats) is restored unless changed meanwhile | Implemented |
| Host scroll | Same units as Linux: 15 per wheel notch, positive is down/right | Implemented (direction fixed) |
| Window placement | `ca window info/move` with no activation | Verified 13 Oct |
| Monitor scope | `--monitor DEVICE_OR_EDID` or `CA_WINDOWS_MONITOR` restricts capture, input and placement | Verified 13 Oct |
| Private environments | One Linux box container per environment (`containers/linux-box`) in Docker Desktop; commands run inside it with path translation | Box verified on Linux with Podman and the Docker CLI; Docker Desktop on Windows unverified |
| Programs | Conditional programs, fragments, file/outcome checks, bounded evidence, job deadlines and descendant cleanup | Verified 13 Oct |
| Workflow memory | Shared local task/app search, learned source/notes and guarded reuse through the program runtime | Implemented; native Windows reuse unverified; private Linux painting benchmark recorded separately |

The agent lane is not a second keyboard or pointer. Applications that read raw
input, the cursor position or draw their own canvas cannot be driven by it, and
drag gestures, general shortcuts and Alt menus are refused. UI Automation text
entry appends to an element's value; it cannot insert at an arbitrary caret.
Unsupported agent input is refused; there is no automatic host fallback. Host
pointer and foreground changes require task authorization. Secure desktops, UIPI
and app-created activation can restrict control. Focus moving into the agent's
target process (by the human or by the app itself) stops the lease. Input
separation is not a security sandbox.

Input coordinates are physical client-area pixels, including on mixed-DPI
displays. Use the reported capture/window dimensions and keep monitor selection
consistent across commands.

## Private environments (Linux boxes)

`ca env start NAME [--project DIR] [--recipe FILE]` builds the box image from this
checkout on first use (`localhost/computer-artist-box:<source hash>`; rebuilt when
the sources change) and starts container `ca-box-NAME`. Inside it a private KWin
with the plugin, a private D-Bus session and the environment manager run exactly
as on Linux. Every `ca --environment NAME ...` and `ca env exec/logs/browser NAME`
command runs inside the box with `exec`. Host paths in arguments are translated to
box paths and box paths in replies back to Windows paths; a path outside the
project, the mounts and the environment folder is refused. State lives in
`%LOCALAPPDATA%\computer-artist\environments\NAME`; fragments and workflow memories are shared through
the window folder. The box contains KWrite, Konsole and Firefox; recipes add more
with a containerfile starting `ARG BOX_IMAGE` / `FROM ${BOX_IMAGE}`. Selecting
Podman is refused on Windows. The same path can be exercised on Linux with
`CA_ENVIRONMENT_BACKEND=box` (Docker or Podman).

See [workflow memory](../skills/computer-artist/references/memory.md) for learning
and retrieving procedures. Keep `--environment NAME` when learning from a box's
run evidence; the shared library survives that environment's removal.

To check on Windows (not done yet): Docker Desktop bind mounts of
`%LOCALAPPDATA%` (file locks and permissions), build time, KWin software rendering
speed in WSL 2.

## Validation

Run portable/native unit checks with `python scripts/test-windows.py`. They do not
inject desktop input. Live checks require an explicitly chosen display:

```powershell
& .\.venv\Scripts\python.exe tests/integration/check_windows.py --monitor MONITOR_ID
```

This opens two disposable native test windows entirely on that display, checks
independent text/button input, concurrent independent text, pointer and focus
preservation, captures, verified programs and hard deadlines. It closes only its
own fixtures. Add `--host-checks` to explicitly test simultaneous real and agent
input and host clicks; that mode temporarily activates its own host fixture and
restores the original foreground and pointer afterwards.

`tests/integration/check_windows_full.py --monitor MONITOR_ID` covers the features
verified on 13 October (16 checks; 21 with `--host-checks`). `--placement-only`
creates the hidden fixtures, reports their geometry and exits without input.
Before input it re-reads the shown fixtures and stops if they are off the display,
overlapping or resized. Agent connections replace SendInput with an assertion.
Each check is reported as `passed`, `failed`, `skipped` or
`interrupted_by_human`; the last is used only when low-level hooks counted real
(non-injected) keyboard/mouse events during a failed check (counts only, no key
contents). Exit code 0 means all passed, 2 means no failures but incomplete, 1
means a failure. Evidence is retained in `local/windows-validation/`, which is
excluded from git. Results of 13 October 2026 are summarized in
[docs/validation/2026-10-13-windows.json](validation/2026-10-13-windows.json).

The UI Automation lane, PrintWindow capture, host paste restoration, the scroll
fix and Linux boxes still need live checks on Windows; extend
`check_windows_full.py` with a WPF or WinUI fixture for them.

See the bundled [Windows skill reference](../skills/computer-artist/references/windows.md)
for daily command use.
