# Practical operating tips

These are starting strategies derived from CA's API and desktop work. Specific
Drawing interaction lessons are in [pointer-techniques.md](pointer-techniques.md). They reduce
exploration; they do not guarantee identical behavior in another application.

## Establish enough state, then act

Read capabilities once at task start, list windows, and inspect a fresh image.
Record the chosen window ID, logical geometry, canvas/content region, active
tool, zoom, and any visible modal window. Avoid repeatedly calling capabilities
or enumerating the whole desktop between ordinary strokes. Refresh when the UI
changes or an action is refused.

The first observation is for locating the current interface, not for discovering
the API by trial and error. Use the documented context methods. Start with a
small useful part of the requested work and inspect it before scaling up; avoid
unrequested test marks in an existing document.

## Keep coordinate spaces explicit

For a full capture with pixel size `(Pw, Ph)` and logical window size `(W, H)`,
map image point `(px, py)` to content coordinates:

    x = px * W / Pw
    y = py * H / Ph

For a displayed crop whose logical region is `(rx, ry, rw, rh)` and displayed
size is `(Dw, Dh)`, map displayed point `(dx, dy)` as:

    x = rx + dx * rw / Dw
    y = ry + dy * rh / Dh

Use the dimensions of the image actually being viewed. A viewer may resize a
capture again. Observation `pixel_size` describes the full capture, even when
`image` points to a crop; use `full_image` when a full-window reference is needed.

Keep drawing geometry in its own design coordinates. Convert once through a
canvas origin and scale before calling `ctx.path`. Window-relative coordinates
include toolbars and palettes; they are not automatically canvas-relative.
Canvas zoom and scrolling change the document-to-window mapping without changing
window geometry, so CA's geometry checks alone cannot detect every stale plan.

## Batch predictable work, inspect transitions

Use one bounded `ca execute` for a coherent group of strokes or controls.
Thousands of separate shell commands add overhead; one enormous unobserved
program makes recovery difficult. Good boundaries include one drawing layer,
one color group, a dialog opening, and a completed save.

Prepare geometry and an ordered operation list before acquiring input. Estimate
the request budget from the number of points plus tool changes, clicks and
observations; one stroke consumes multiple requests. A path is limited to 20,000
points and the remaining request budget. Resample curves to the precision the
canvas needs instead of oversampling them. Split oversized paths at deliberate
stroke boundaries because each path call releases the button.

For many opaque, non-overlapping reconstruction tiles, grouping by color reduces
dialog overhead. For overlapping artwork, preserve back-to-front layer order;
globally sorting by color can paint foreground details underneath later shapes.

Use local waits for observable transitions. Very short delays can help an app
process tool changes, but fixed timing is not confirmation. Slow down when marks
are missed rather than repeatedly dispatching the same full batch.

## Recover from the state you actually have

Save an operation index after completed batches and keep a corresponding
observation. On resume, restore the intended tool, fill mode and color, and
inspect the canvas. A checkpoint is evidence of progress, not a transaction:
some actions after it may already have landed.

On a connection error, inspect before replaying. In the desktop demonstration,
focus/dialog transitions sometimes completed despite an interrupted reply.
Blind retries can duplicate clicks, close a new dialog or alter the wrong field.
Distinguish automatic dialog activation from deliberate human takeover.

Named targets are useful for stable buttons, but costly or brittle for changing
canvas pixels. Use fresh local coordinates for planned strokes in a stable
canvas. After a target refusal, inspect and redefine only if the intended control
is still present; do not bypass a meaningful mismatch.

## Make reusable actions carry their assumptions

Useful modules include a stroke, polygon, tool selection, or color selection.
Parameterize geometry and values. Document required tool state, coordinate
origin, app/layout assumptions and what the outcome check proves. Keep palette
coordinates in app-specific configuration rather than generic drawing helpers.

Registration does not execute or test a module. Attaching one to a new window
does not validate the new layout. Reuse confirmed behavior, with a fresh window
ID and current geometry. Prefer a small composition of these actions over a
single hard-coded whole-task replay.

## Preserve useful evidence

Capture completed phases, not every pointer event. Inspect the final full canvas
as well as detail crops so clipping and composition errors are visible. Preserve
important run records explicitly; temporary observations expire. A screenshot
proves what was visible, not that the document was saved. For a requested saved
image, confirm the app save and, when possible, that the resulting file decodes
with the expected dimensions. Do not replace an app save with a screenshot
without saying so.
