# KolourPaint techniques

Observed in the desktop painting demo; verify against the current app and layout.

## Shapes and strokes

- Freehand contours: held-button `ctx.path`. Polygons: individual vertex clicks.
- In the tested Polygon tool, select fill-only, left-click vertices, then
  right-click the final vertex to commit. Double-click is not equivalent.
- Close, rapid vertex clicks can become double-clicks. The demo used roughly
  16 logical pixels spacing or .45-second pauses for close pairs. Adjust timing
  without discarding needed geometry.
- Bucket fill respects existing color boundaries. Use opaque filled polygons or
  ellipses to cover earlier artwork, then restore outlines/details.
- Draw back-to-front: background, rear objects, main forms, foreground, outlines,
  highlights. Use brush width for broad marks; offset pencil paths can look uneven.

## Reference reconstruction

Quantize the reference, merge equal-color horizontal runs, then merge matching
runs across rows into rectangles. Draw them with the app's filled-rectangle tool.
The demo used 48 colors and a 224×332 grid; choose resolution for the current task.
Group non-overlapping tiles by color to reduce dialog work.

Inspect adjacent tiles early. The demo needed a one-logical-pixel extension at
far drag endpoints to close gaps; measure at the current zoom before applying it.
Importing a finished bitmap does not demonstrate drawing through app input.

## Colors and dialogs

1. Double-click a palette swatch to open Select Color. Find the dialog's current ID.
2. It may activate itself and interrupt agent control. If host control is authorized,
   focus another app and move the real pointer outside the target connection.
3. In the tested RGB spinboxes, positive scroll lowered values. A large positive
   delta clamped to zero; `-10 * value` raised to the desired value. Allow roughly
   .1 seconds and read back the fields/preview. Direction and scale are app-specific.
4. Confirm, then click the edited swatch to select the drawing foreground.
   Closing the dialog may require focus recovery again.

Palette editing and foreground selection are separate actions. Group compatible
work by color. Scope large scroll deltas to the correct spinbox.

## Saving

CA has no keyboard or clipboard injection. The demo used an authorized external
clipboard operation followed by explicit host middle-click into the filename
field and a click on Save. This is environment-specific; inspect the pasted name.
Preserve prior clipboard contents privately. Text restoration does not restore
arbitrary clipboard formats. For CA-only tasks, report blocked filename entry.

Verify the saved file, not just a screenshot. If CA cannot read a GPU buffer,
use an available authorized capture method and account for its borders/scaling.
