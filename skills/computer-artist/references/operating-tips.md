# Operating tips

## Coordinates

For full screenshot pixels `(px, py)`, image size `(Pw, Ph)`, and logical
content size `(W, H)`:

```text
x = px * W / Pw
y = py * H / Ph
```

For a displayed crop `(dx, dy)`, displayed size `(Dw, Dh)`, and logical crop
region `(rx, ry, rw, rh)`:

```text
x = rx + dx * rw / Dw
y = ry + dy * rh / Dh
```

Use the size actually displayed. Observation `pixel_size` describes the full
capture, even when `image` is cropped. `full_image` is the full-window reference.
Canvas zoom/scroll can invalidate drawing coordinates without resizing the window.

## Batch and recover

- Batch one coherent layer, color group, or interaction in `ca execute`.
- Inspect transitions: tool changes, dialogs, completed layers, saves.
- Prepare geometry before input. Paths allow at most 20,000 points and must fit
  the remaining request budget. Resample to useful precision.
- Group non-overlapping tiles by color; preserve layer order for overlapping art.
- Record completed operation indices and evidence. After interruption, inspect:
  actions after the checkpoint may already have landed.
- Use guarded targets for stable controls, fresh coordinates for changing canvases.
- Keep fragments small and parameterized. Record required tool, color, coordinate
  origin, and layout assumptions. Reusing code requires checking the new window.
- Inspect the final full canvas and details. A screenshot is not proof of a save;
  check that the requested file exists and decodes correctly.
