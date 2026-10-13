# Windows

Requires Windows 10/11 and Python 3.12 or later. No KWin, WSL, administrator access,
native build or Linux D-Bus dependency is needed.

```powershell
git clone https://github.com/wmostert76/computer-artist.git
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

## Implemented behavior

| Feature | Windows scope |
| --- | --- |
| Independent text | Targeted Edit/RichEdit selection and undoable Unicode insertion; no SendInput, host focus or shared clipboard |
| Independent pointer | Animated click-through overlay; supported standard edit selection and push-button notification |
| Concurrency | Separate application processes can receive independent input while the real keyboard works in another app; per-process ownership prevents conflicting lanes |
| Explicit host input | SendInput mouse, physical scan-code keys, layout-based text, scroll, foreground and bounded Unicode clipboard, with `--host` |
| Observation | Visible client-region screen capture, standard-control text read-back, pixel/geometry comparisons, bounded watch |
| Window placement | `ca window info/move` with no activation; physical outer geometry verified after movement |
| Monitor scope | `--monitor DEVICE_OR_EDID` or `CA_WINDOWS_MONITOR`; capture/input/placement outside the selected display is refused |
| Programs | Conditional programs, fragments, file/outcome checks, bounded evidence, Windows job deadlines and descendant cleanup |

Independent standard controls and concurrent separate text were verified in
disposable Win32 applications. Actual Windows 11 Notepad (`RichEditD2DPT`) was
verified for exact multiline Unicode write/read and window movement on a Philips
346P1C (3440x1440), with human pointer and foreground preserved. This proves those
workflows; it does not prove universal application compatibility.

Independent physical keyboard injection, generic accelerators, scrolling,
checkbox/radio state, drawing/canvas gestures, browser/web terminals, private KDE
environments and Windows UI Automation are not implemented. Unsupported agent
input is refused; there is no automatic host fallback. Host pointer and foreground
changes require task authorization. Capture includes overlapping windows and is
not an offscreen-rendering API. Secure desktops, UIPI and app-created activation
can restrict control. Input separation is not a security sandbox.

## Validation

Run portable/native unit checks with `python scripts/test-windows.py`. They do not
inject desktop input. Live checks require an explicitly chosen display:

```powershell
& .\.venv\Scripts\python.exe tests/integration/check_windows.py --monitor PHL093F
```

This opens two disposable native test windows entirely on that display, checks
independent text/button input, concurrent independent text, pointer and focus
preservation, captures, verified programs and hard deadlines. It closes only its
own fixtures. Add `--host-checks` to explicitly test simultaneous real and agent
input and host clicks; that mode temporarily activates its own host fixture and
restores the original foreground and pointer afterwards.
Evidence is retained in `local/windows-validation/`, which is excluded from git.

See the bundled [Windows skill reference](../skills/computer-artist/references/windows.md)
for daily command use. The original KDE workflow continues to use its KWin plugin.
