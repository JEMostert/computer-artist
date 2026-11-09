# Chrome: frames from other origins, and Word for the web (9 November 2026)

Hosts: macOS, extension 0.12.0.

- Headless Chrome for Testing 153.0.8010.12 with two disposable profiles, driven by
  `tests/integration/check-browser.py`: 76 of 76 checks passed.
- The user's Vivaldi (reported as "Google Chrome 150"), instance `79b4ade8`, against
  Word for the web on the user's SharePoint (`hrnl-my.sharepoint.com`), with the
  user's agreement. Only a new blank document (`Document.docx`, created through
  `word.new`) was edited. The user's own document tab was not claimed, read or changed.

## Problem

`ca browser read` on a Word for the web tab returned empty text. `--frame iframe`
was refused as `cross_origin_frame`, naming the SharePoint origin, which is the
page's own. The extension reached only frames whose document the top frame can
access, and its message took the frame's origin from the iframe's `src` attribute.
SharePoint loads Word by posting a form into a named iframe with no `src`, so the
attribute resolved to the page's own URL. The frame's document comes from
`euc-word-edit.officeapps.live.com`.

## Decisions

- **Enter the frame instead of refusing.** The extension already has host access to
  every web origin, so `chrome.scripting` can run an action in any frame by ID. When
  `--frame`, a point, or the focused element leads into a frame the current world
  cannot reach, the page returns an internal `enter_frame` step, and the worker runs
  the same action in that frame. Each further frame from another origin is one more
  step, up to 8.
- **Name frames by window path, not `chrome.runtime.getFrameId`.** That API is not
  available in scripts run through `chrome.scripting.executeScript` (measured: `not a
  function` in Chrome for Testing 153). `window.parent` and `frames[i]` work across
  origins, so a page names the frame by its index path from the top window. The
  worker runs a small script in all frames to map that path to Chrome's frame ID. No
  new permission is needed.
- **Keep one coordinate system.** The parent passes the frame's box in the top
  viewport (`origin`) and the top viewport's size. Rects, points, `--viewport` and
  event coordinates stay top-viewport CSS pixels. Actions scroll the frame into view
  first; reads and hints never scroll.
- **Refs carry their frame** (`ref@FRAME`). The worker measures that frame's box
  through its parents, so a ref from `locate --frame` works without `--frame`.
  Actions scroll each frame on the way into view first, as `--frame` does.
- **The pen stays in the top frame.** An action in an entered frame relays pen moves
  through the worker. The relay also reports human input in the top frame since the
  command began, and a claim that ended or changed lease, so a glide holds the action
  back and a drag stops, as in the top frame. Before each step into a frame, the
  worker also refuses with `human_active` (`performed: false`) if the human used the
  tab meanwhile.
- **Hints cover every frame.** `locate` without a match, and `element_not_found`,
  search frames from other origins too and name the full `--frame` route. A `read` of
  a whole document lists its visible frames with `same_origin`.
- **No trusted input.** Typing and clicks are still untrusted DOM events. The
  debugger is still used only for background screenshots.

## Fixture checks (headless Chrome for Testing 153)

The fixture mimics SharePoint: a page on `127.0.0.1` posts a form into
`iframe[name="editor_frame"]` (no `src`), and the editor arrives from `localhost`.
Inside it are a contenteditable document, find and replace fields, and a further
frame from `127.0.0.1`.

| Check | Result |
| --- | --- |
| `editor_frame_listed_and_read` | top `read` lists the frame (`same_origin: false`); `read --frame` returns the editor text and its `localhost` URL |
| `foreign_frame_hints` | `locate --text 'Replace all'` names `[frame]`; a target two frames down is refused with `--frame '…' --frame '#inner'` |
| `type_and_click_in_foreign_frame` | typing into both fields, clicking **Replace all** and typing into the contenteditable all land |
| `ref_and_point_in_foreign_frame` | a `@FRAME` ref clicks without `--frame`; a top-viewport point clicks the field inside the frame |
| `focused_element_in_foreign_frame` | `press Backspace` with no target edits the focused field inside the frame |
| `frame_inside_foreign_frame` | a click two origins deep lands |
| `foreign_frame_ref_below_fold_and_scroll` | from the top of the drawing fixture, a `@FRAME` ref in the below-the-fold `localhost` frame is clicked, and `scroll --frame` with no target scrolls at that frame's centre |
| `foreign_frame_and_refusals` | the same frame is scrolled into view and clicked by `--frame`, and by point |

The other 68 checks are unchanged. Human input during the step into a frame, and a
release during a drag in a frame, were reviewed but not exercised: both depend on
timing that the harness cannot set up reliably. On macOS the harness now puts its rendezvous
folder under `/tmp`. Under the per-user temporary folder the native host's socket
path exceeded the 104-byte limit for Unix sockets, so no browser connected, on
`main` as well.

## Word for the web (user's Vivaldi)

| Step | Result |
| --- | --- |
| `read` of the Doc.aspx tab | empty text; `frames: [#WacFrame_Word_Inline, same_origin: false]` |
| `read --frame '#WacFrame_Word_Inline'` | ribbon and document text; URL `https://euc-word-edit.officeapps.live.com/we/wordeditorframe.aspx?…` |
| `type` into `#WACViewPanel_EditingElement`, background tab | text appeared in the DOM, but Word showed "We're still loading the document"; after a reload the document was empty. Not accepted. |
| same, after `open --agent-window` and full load | Word counted "5 words"; after a reload the document still read `Ths is a speling test.` **Accepted and saved.** |
| point click on **Find Show More Options**, then **Replace** | Word's menu and Replace pane opened |
| `type` into the find and replace fields, click **Replace All** | Word reported `"speling" was replaced with "spelling" in 1 places.`; after a reload the document read `Ths is a spelling test.` **Saved.** |

Word reacted to ca's untrusted clicks and `type` (which inserts through
`execCommand('insertText')`) once its editor had loaded. Trusted input through the
debugger was not needed for these steps, so it was not added. Not tested: keyboard
shortcuts such as ⌘+H, Word's spelling suggestions (Editor), and documents longer
than one page.

The throwaway `Document.docx` remains in the user's OneDrive.
