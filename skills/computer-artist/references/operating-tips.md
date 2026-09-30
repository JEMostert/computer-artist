# Coordinates and recovery

For full screenshot pixel `(px, py)`, image size `(Pw, Ph)` and logical content
size `(W, H)`, use `x = px * W / Pw`, `y = py * H / Ph`. For a displayed crop
point `(dx, dy)`, displayed size `(Dw, Dh)` and logical region `(rx, ry, rw, rh)`,
use `x = rx + dx * rw / Dw`, `y = ry + dy * rh / Dh`.

Use the actual displayed size. Observation `pixel_size` describes the full capture
even when `image` is cropped; `full_image` is the full-window reference. Canvas
zoom/scroll can invalidate coordinates without changing window geometry.

Batch a coherent layer, color group or interaction through `ca execute`. Inspect
tool changes, dialogs, layers and saves. Prepare geometry before input; paths cap
at 20,000 points and must fit the remaining budget. Group non-overlapping tiles by
color while preserving layer order for overlapping artwork.

Use guarded targets for stable controls and fresh geometry for changing canvases.
Reusable fragments should state tool/color/origin/layout assumptions and check the
new window. After interruption, inspect actual state before retrying: actions after
a checkpoint may have landed. Check the final canvas and details, then verify the
requested saved file exists and decodes; a screenshot does not prove a save.
