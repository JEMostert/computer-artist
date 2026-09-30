# KolourPaint techniques

These were observed in a specific painting workflow; verify the current app/layout.

Use held-button `ctx.path` for freehand contours and vertex clicks for polygons.
The tested Polygon tool used fill-only, left-click vertices and right-click the
last vertex to commit; double-click was different. Close clicks can become double
clicks. The demo used about 16 logical pixels spacing or .45-second pauses for
close vertices; adjust timing without discarding geometry.

Bucket fill follows existing color boundaries. Opaque filled polygons/ellipses can
cover earlier art; restore outlines afterwards. Draw back-to-front: background,
rear objects, main forms, foreground, outlines, highlights. Broad brush marks are
smoother than unevenly offset pencil paths.

For reference reconstruction, quantize colors, merge equal-color horizontal runs,
then matching rows into rectangles. Draw through the filled-rectangle tool and
group non-overlapping tiles by color. The demo used 48 colors on a 224×332 grid;
choose resolution for the task. Inspect adjacent tiles early. A one-logical-pixel
extension closed endpoint gaps in the demo; measure at the current zoom first.
Importing a finished bitmap does not demonstrate drawing through app input.

Double-clicking a palette swatch opened Select Color. Observe its exact current
window ID; activation can interrupt the agent and change human focus. Use explicit
host recovery only within the task's authorization. In the tested RGB spinboxes,
positive scrolling lowered values and a large delta clamped to zero; `-10 * value`
raised them to the desired value. Allow processing time and read back fields and
preview. Scroll direction/scale is app-specific. Confirm, then click the edited
swatch to select the drawing foreground; these are separate actions.

For save dialogs, inspect/select the filename field, use explicit host paste and
confirm the pasted name before Save. Paste changes the shared clipboard. Verify
actual saved bytes and decoded artwork; dialog closure alone is not completion.
