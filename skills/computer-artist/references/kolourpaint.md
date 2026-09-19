# KolourPaint: lessons from the desktop drawing run

These techniques were used in the project's September 2026 two-painting demo.
They are observations from that configuration, not promises about every Qt or
KolourPaint version. Rediscover current widget positions from the image; do not
reuse the old window IDs, monitor offsets, canvas size or reference subject.
The durable evidence is `docs/PAINTING-DEMO.md` and
`docs/assets/kolourpaint-original.png` in the resolved CA checkout.

## Accurate reconstruction through drawing input

The tested approach reduced the reference to a grid, quantized it without
dithering, merged equal-color horizontal runs, then merged identical runs on
adjacent rows into taller rectangles. A 224 by 332 grid with 48 colors produced
8,464 rectangles in the refined pass. These are useful scale examples, not
required settings; choose resolution and palette for the new image's detail.

Draw those rectangles using KolourPaint's filled-rectangle tool and actual
pointer drags. Source-image analysis may supply geometry when the user asks for
reconstruction; importing a rendered bitmap does not demonstrate drawing input.
Exclude an inset or background only when requested for the current reference.

Filled rectangles initially left thin gaps because drag endpoints and painted
coverage differed. The successful final pass extended the far x/y endpoint by
one logical pixel in that setup. Inspect adjacent tiles early; apply a measured
correction rather than assuming this exact adjustment works at every zoom.
Keep the fill-only style and intended foreground color selected.

## An original interpretation

For the second painting, independently authored silhouettes and curves supplied
the geometry. Continuous pencil drags drew contours, opaque shapes established
layers, and brush drags added broad highlights. Recognizable features and a
deliberate silhouette mattered more than recreating every reference pixel.

A useful starting order is backdrop, rear limbs/hair, body/clothing, head and
face, foreground hands/accessories, outlines, then highlights and small details.
Design the curves in a convenient local coordinate system and scale them into
the available canvas. Sample smooth curves for `ctx.path`; retain intentional
corners. Use the brush's actual width for broad marks. The demo also used small
offset pencil paths for thicker contours, but that technique can introduce
directional unevenness.

## Polygon completion and bucket-fill failure

The tested filled-polygon interaction was:

1. Select Polygon and the filled-only style.
2. Left-click each vertex.
3. Right-click at the final vertex to finish the polygon.
4. Observe the committed shape before changing tools.

With the context API, the last operation is
`ctx.click(x=last_x, y=last_y, button='right')`. Do not substitute a double click:
that did not reliably finish the polygons in our run. Polygon vertices are
clicks; a freehand contour uses a held-button `ctx.path` drag.

Rapid clicks at very close vertices can be interpreted as a double click. The
working demo simplified fill contours to roughly 16 logical pixels between
vertices and paused about .45 seconds for remaining close pairs. Those are
observed workarounds, not minimum spacing rules: preserve fine geometry by
slowing nearby clicks or choosing an appropriate tool rather than deleting
important features.

Bucket fill stopped at pre-existing color boundaries inside the intended shape.
A closed outline alone does not mean the entire interior is one fillable region.
For opaque clothing or face shapes covering earlier artwork, a filled polygon
or ellipse worked better. Avoid repeatedly bucket-filling fragments without
understanding the existing boundaries. Reapply outlines/details after the fill
when needed to preserve their visibility.

## Custom colors without injected keyboard input

Double-clicking a palette swatch opened Select Color in the tested UI. Locate
the resulting dialog by current window metadata and application PID, then use
its own window ID and geometry. It may activate itself and acquire human keyboard
focus even though the user has not touched anything.

If host control is already authorized, release agent ownership and use explicit
host focus on a suitable other application, moving the real pointer out of the
target connection if needed. Re-list windows and continue on the actual dialog.
Closing the dialog may reactivate KolourPaint, requiring the same recovery.
Do not label this as prevention of focus stealing or general popup support.

The RGB spinboxes accepted scroll input. In this setup, positive scroll lowered
the value: a large positive delta clamped a channel to zero, and `-10 * value`
raised it to the desired channel value. A roughly .1-second pause after each
change gave the widget time to update. This direction and scale are app-specific;
read the displayed values and color preview before confirming, and adjust to
the actual behavior if they differ. Scope large deltas to the spinbox, never
an arbitrary area of the window.

After accepting the dialog, clicking the edited swatch once selected it as the
drawing foreground. Verify the foreground indicator; editing a palette entry
and selecting the active drawing color are separate operations. Group safe
operations by color to avoid reopening the dialog for every mark.

## Saving and capture limitations

The demo saved through KolourPaint's own Save dialog. Because CA has no keyboard
or clipboard injection, an explicitly authorized external KDE clipboard action
supplied the filename; a host middle-click pasted it into the active filename
field, and the Save button completed the operation. The prior text clipboard
was restored afterward. This is an environment-specific workaround, not a CA
API feature or an automatic permission to change clipboard/focus. If a task
requires CA-only actions and the filename cannot be entered, report that limit.

For this workaround, inspect the focused field and resulting filename before
Save. Preserve prior clipboard content privately; do not print it in logs or
responses. Text restoration does not preserve arbitrary non-text clipboard
formats, so do not claim full clipboard restoration in that case.

CA captured KolourPaint's main surface successfully. Another tested application,
Vivaldi, had an unreadable GPU buffer; the separately available Spectacle tool
provided a read-only screenshot. That fallback is not a generic CA capture fix.
Use an available screenshot mechanism within the task's scope and account for
its different window borders, scaling and coordinate origin.
