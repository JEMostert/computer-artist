# Composited client capture — 20 September 2026

Previously, capture mapped only the toplevel Wayland surface buffer. The existing
SDL/scrcpy phone mirror failed with `capture_unavailable_or_path_exists`.
Main-surface reads cannot represent a general subsurface tree.

Capture now renders the window item through KWin's renderer, clipped to
`clientGeometry()` at `targetScale()`. OpenGL renders into an offscreen texture
and reads it back. A QPainter path is implemented but not separately verified.
Capture does not change input focus or the pointer. New-file and screen-lock
checks are retained; invisible windows are rejected. Observations record the
backend's capture source.

Validation and preserved evidence:

- All 45 existing unit tests and the stock KWin pointer, harness, host-lane and
  host-text integration suites passed in a separate headless compositor:
  `output/2026-09-20_18-40-14-983019Z-4705b0/`.
- A native Wayland scrcpy 4.1 mirror using default rendering in the separate
  compositor produced a correctly oriented 1280×590 Clash of Clans victory screen:
  `output/2026-09-20_18-41-12-367829Z-827ac3/`.
- After adding the QPainter branch and source-metadata test, the plugin build/link
  check and all 13 runtime tests passed.
- The final plugin loaded as `computerartist-capture-b22f9f0a7203` into the existing
  desktop without restarting KWin. The original scrcpy window, PID 60643, was
  preserved. CA captured its village screen at 1895×873 for a logical
  1263⅓×582 client rectangle (150% display scale):
  `output/2026-09-20_18-42-12-633028Z-566708/`.
- The canonical plugin was atomically replaced for future logins. The previous
  versioned plugin remains available for rollback.
- Before pushing, the final build passed all 46 Python tests, the build/link
  check, and all four isolated KWin integration suites again:
  `output/2026-09-20_18-49-54-015145Z-cff661/`.

No game input was sent. Clicking, scrolling, Waydroid composition, separate popup
windows and non-OpenGL compositors are not established by these capture checks.
