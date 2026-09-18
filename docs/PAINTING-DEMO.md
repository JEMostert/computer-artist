# Two paintings, one desktop

[Download the original KolourPaint document as PNG](assets/kolourpaint-original.png).
The saved canvas is 2451 × 1102 pixels. Both pictures were drawn into the same
native Wayland KolourPaint window through Computer Artist on 18–19 September 2026.
The inset photograph in the supplied reference was omitted.

## Reference reconstruction

The first program reduced the supplied reference to a 224 × 332 grid, simplified
the background, and chose a 48-color palette. Equal horizontal runs were merged
vertically where possible, producing 8,464 planned rectangles in the refined
pass. Earlier attempts and corrective passes added more actual input than this
count. No reference bitmap was imported or pasted into KolourPaint.

Captures exposed an inset in KolourPaint's filled-rectangle behavior. Adjusting
the drag boundaries and repainting removed the gaps between adjacent marks.

## Original interpretation

The second drawing used independently authored geometry: an oversized head and
eyes, angular ivory hair, a peace-sign hand, a teal jacket, a heart graphic, pink
shoes, and a mint oval backdrop. Its 158 planned shape/detail operations included
continuous contour drags, ellipses, opaque polygon fills, and fine linework.
A final set of brush drags added broader highlights. This was programmatic drawing
from an authored design, not pixel sampling of the reference or an image import.

Bucket fills stopped at underlying color boundaries. Polygon fills required an
explicit right-click to finish. Both issues were observed and corrected during
the run. Some transport interruptions also required fresh observation and resume;
the session should not be described as flawless or fully unattended.

## Input and saving

The independent agent lane made the drawing marks. Color dialogs activated
themselves; explicit host focus restored the chat before agent work continued.
This demonstrated recovery in this app, not prevention of focus changes or
general popup compatibility.

The final PNG was saved with KolourPaint's own Save dialog. KDE's clipboard
supplied the absolute filename and the explicit host lane pasted it using a
middle click and pressed Save. The prior text clipboard content was restored,
focus and pointer were returned to the chat, and the CA session was closed.
CA itself still has no keyboard or clipboard injection API.

## README artwork

- `assets/kolourpaint-original.png` is the actual app-saved canvas.
- `assets/kolourpaint-showcase.svg` embeds that PNG in a frame with a tighter
  viewport and captions. It does not repaint or alter the drawing.
- `assets/agent-cursor.svg` is a static illustration derived from the arrow path,
  palette, and effects in `plugin/main.cpp`; it is not a captured animation.
- `assets/hero.svg` and `assets/architecture.svg` are original documentation SVGs.

The visuals use self-contained SVGs without scripts, remote fonts, or network
image dependencies. The original PNG is separately linked for direct viewing.
