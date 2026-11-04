# Chrome precise pointer input, 4 November 2026

Host: macOS 27 (Apple Silicon), Python 3.13. Extension 0.6.0. Browsers: the user's
Google Chrome (instance `5cb4db89`, 2x HiDPI) and disposable headless Chrome for
Testing 153.0.8010.12.

## The failure

An agent was asked to edit an open structure on LabServant's structure edit page.
Its centre click on the editor did not change the drawing: `click` always targeted an
element's centre, and the editor (Marvin JS on that branch) lives in a same-origin
iframe, so the click reached the iframe element rather than the canvas inside it.
There was no way to address a point, hover, drag or hold a modifier.

## Synthetic or trusted input

Measured before building anything, with Marvin JS loaded alone in Chrome for Testing:
the same tool-then-atom edit was driven with trusted DevTools input and with
synthetic DOM events, in all four combinations, each in a fresh browser.

| Tool click | Atom click | Result |
| --- | --- | --- |
| trusted | trusted | atom changed |
| synthetic | trusted | unchanged |
| trusted | synthetic | atom changed |
| synthetic | synthetic | unchanged |

The canvas accepted synthetic events; the toolbar did not. Its buttons, like GWT
toggle buttons, only activate after a hover (`mouseover`) that the old click never
sent. With pointer and mouse boundary events added, synthetic tool and atom clicks
and a synthetic drag drew the same bonds as trusted input. Trusted input (the
`debugger` permission, with its browser-wide banner) is therefore not used.

## Implementation

- `click` (with `--x/--y`, `--button`, `--count`, `--modifiers`), `move`, `drag`
  (`--from`, repeatable `--via`, `--to`) and `scroll` (wheel) use one mouse model:
  boundary events, a move before each press, pointer then compatibility mouse events,
  focus, click on the common ancestor, `dblclick`, `contextmenu`, `auxclick`, and
  compatibility-event suppression after a cancelled `pointerdown`.
- Points are CSS pixels relative to an element or the viewport. Hit testing descends
  into same-origin and CSS-scaled iframes and open shadow roots. `--frame` scopes any
  lookup to a same-origin iframe; targets found only in a frame are reported with the
  flag to pass. Elements named only by `title` or `aria-label` match `--text`.
- Drags move in steps of at most 8 px, paced at frame rate in visible tabs and by
  unthrottled message yields in background tabs. Human input mid-drag sends
  `pointercancel` and refuses with `ownership_revoked`, `gesture: cancelled`.
- Screenshots report `viewport` and `scale` and hide the pen during capture.
- `tabs` marks pages Chrome forbids (`scriptable: false`, `restriction`); `claim`
  refuses them with the reason.
- The CLI refuses the new options as `extension_outdated` while an older extension
  runs, instead of letting it click a centre. `ca browser` builds only its own parser.

## Real Chrome regression

`tests/integration/check-browser.py` passed all 50 checks against two disposable
Chrome for Testing 153 instances (`TMPDIR=/tmp`). The new checks, on a fixture with a
hover-gated toolbar and a canvas inside a same-origin iframe scaled 1.5x below a tall
header:

- The frame hint named `--frame '#pad'`; the hover-gated tool activated through it.
- An element-relative drag through three points recorded `line 20,30 150,120` with
  35 moves after scrolling; a viewport point through the scaled frame recorded
  `dot 50,40`.
- Double click, right click, Shift click and canvas wheel were each recorded; a page
  wheel scrolled the page by exactly 300 px, and a wheel over an exhausted panel
  scrolled the page instead (0 to 120 in the panel, then 0 to 200 on the page).
- A toolbar button that cancels `pointerdown` was clicked while a field had focus;
  its handler still saw the field focused.
- A panel with `overscroll-behavior: contain` stopped at its end instead of scrolling
  the page, and a viewport click on a button in a disabled fieldset was refused.
- A cross-origin frame (by `--frame` and by point), a point outside the viewport and a
  missing target were refused with `cross_origin_frame`, `point_outside_viewport` and
  `invalid_target`.
- A trusted DevTools click during a paced drag cancelled it after 17 to 18 moves:
  `pointercancel` was seen, no stroke was recorded.
- A `chrome://version` tab was listed `scriptable: false`, `restriction:
  browser_page`, and its claim was refused with that reason.
- A screenshot after a visible click contained 0 pen-tag pixels; with hiding
  disabled, the same check found 890 and failed.

Driven through `ca browser` alone, Marvin JS in Chrome for Testing went from empty to
`CC` (one bond drag), then `CCNCC` after tool clicks by tooltip and a three-point drag.

## The user's Chrome

The extension was updated from 0.5.3 to 0.6.0 during a maintenance window agreed
with the other agents using that browser; the user pressed the card's reload button
(no tab-scoped tool can reach `chrome://extensions`). Same instance ID, new host
process, doctor clean. Open, read, status, locate and close passed with this branch's
CLI and with the unchanged main checkout's CLI.

A browser operator then worked only in two new tabs, during a visible-tab window the
user approved, and saved nothing. That LabServant branch now uses a canvas structure
editor in the page itself; `--frame '#sketch'` correctly answered `frame_not_found`.

- Structure search: a bond drag and an element-tool click drew H3N-CH3; the search ran
  (exact matches 0, substructure matches 1) and the drawing survived the page update.
- Structure edit (acetone, never saved): a viewport-point click with the nitrogen tool
  replaced the oxygen, and a drag from a carbon added a bond. Screenshot scale was 2.
- Medians in the visible tab, including the pen glide: click 0.44 s, drag 0.53 s,
  screenshot 0.17 s. In background tabs: click 0.09 s, drag of 35 moves 0.14 to 0.17 s.

## Performance

Per command, discovery took 1.7 ms, the request 0.8 ms and the `tabs` reply 2 KB;
the CLI's time was Python start-up and building every command's parser. Routing
`ca browser` to its own parser cut `ca browser tabs` from 102 ms to 60 ms (median of
9). A drag of any length is one request and one script run in the tab.

## Not done

- `chrome.runtime.reload()` from the extension unloaded it in Chrome for Testing
  rather than reloading it, so a self-reload command was not added; updates still
  need the reload button.
- Cross-origin frames, native drag and drop, CSS `:hover` and trusted-only gestures
  remain out of reach of synthetic events. Browser zoom was exercised only through
  device scale (2x) and CSS scaling, not the zoom menu.
- The live editor screenshots in this record predate hiding the pen in captures,
  which was added afterwards and verified in Chrome for Testing.
