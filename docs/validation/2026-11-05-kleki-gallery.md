# Kleki gallery: five models paint through `ca browser` (5 November 2026)

Five models each painted one commission in [Kleki](https://kleki.com), each in its
own tab of one Chrome, only through `ca browser`. The works, time-lapses, chart,
diagram and film in the README come from this run. This record covers the setup,
how each artist's run went, the pen footage, and every CA error and Kleki quirk
seen. It verifies these workflows on this setup, not general compatibility.

## Machine and browser

- Fedora 43 (Linux 7.2.9), GNOME Wayland session on the host. Python 3.14, this
  branch's checkout installed with `pip install -e .`; extension **0.9.0**, build
  `f1aa16888399`.
- **Browser:** Chrome for Testing 153.0.8010.12 (Playwright build 1243), started once
  for this run with a disposable `--user-data-dir` in the coordinator's scratch
  folder, `--load-extension` (the installed extension folder), `--password-store=basic`
  and `--ozone-platform=wayland`. The native host was registered only for that
  profile (`ca browser install --user-data-dir DIR`). No user profile, keyring or
  host registration of the user's own browsers was touched.
- **Display:** a private KWin desktop (`ca env start kleki`, 5120×2880), not the
  user's GNOME session, which was active. Six Chrome windows were tiled 3×2 with a
  KWin script over the environment's own session bus: one per artist and one for the
  coordinator. Each window had about a 1700×1291 CSS viewport at scale 1.
  Chrome ran with the host's `XDG_RUNTIME_DIR` and `XDG_DATA_HOME`, so its native
  host published its endpoint in the user's rendezvous folder and `ca browser`
  outside the environment could reach it.
- The 56 px bar above every page was first Chrome's **"Computer Artist" started
  debugging this browser** bar, and later Chrome for Testing's own "only for
  automated testing" infobar in the same place. The viewport stayed 1700×1291 for the
  whole run.

## Probe (before the artists)

In a tab of its own (`--owner kleki-probe`, closed afterwards):

- Kleki loaded in English (`navigator.language` en-US; it was Dutch on the Mac).
- Two screenshots, then a test stroke with `--viewport 1700x1291` landed exactly
  where sent, with the pen and its ink trail in the frame. A drag with a stale
  `--viewport 1700x1347` was refused:
  `{"ok": false, "error": "viewport_changed", "detail": "The viewport is 1700x1291, not 1700x1347: take a new screenshot and measure again", "viewport": {"height": 1291, "width": 1700}}`.
- Hex colours: `type --clear '#1b6ca8'` into the Manual Color Input field and `press
  Enter` set RGB 27/108/168.
- **Pen footage test:** one tab active in each of two windows. Screenshots of both,
  issued at the same time, both returned `method: visible_tab` and `pen: "included"`.
  So every artist got a window of its own, and all five were filmed at once without
  `focus` by the coordinator. Each artist ran `focus` once at the start to make its
  new tab the active tab of its window (authorized for this run).
- **Background rendering failed:** a screenshot of any tab that was not active in its
  window, Kleki or example.com, timed out after 15 s:
  `{"instance": "d0388efa", "ok": false, "error": "capture_unavailable", "detail": "Capturing background tab 1103843354 failed: timed out"}`.
  `chrome.debugger` attached (`debugger_attached: true`), but `Page.captureScreenshot`
  never answered for a hidden tab in this headed Wayland Chrome. Background capture
  was verified on macOS with Chrome 156 (see the
  [debugger session record](2026-11-05-chrome-debugger-session.md)). It does not
  work on this Linux setup.

## The run

Artists were delegated through T3, with one shared brief and their own commission.
Every `ca browser` command went through a logging wrapper (`epoch rc subcommand
ok|error` per line). The numbers below come from those logs, not from the artists'
reports; Sonnet's reported times for its last round did not match its log. Minutes
are painting time: the log span minus pauses longer than 5 minutes (waiting for a
send-back, or a server restart). Input actions are `click`, `move`, `drag`, `scroll`,
`type`, `press` and `select`.

| Artist | Model, effort | Log span | Minutes | Commands | Input actions | Screenshots | Send-backs |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| Terra | GPT-6.1 Terra, xhigh | 19:10:46–20:22:01 | 64 | 1,874 | 1,611 | 170 | 3 (one after a server restart) |
| Astra | GPT-6 Astra, high | 19:11:05–20:50:19 | 99 | 5,994 | 4,762 | 231 | 1 |
| Sol | GPT-6.1 Sol, high | 19:11:01–19:57:33 | 47 | 710 | 520 | 65 | none |
| Opus | Claude Opus 5.5, high | 19:11:15–22:12:26 | 169 | 17,318 | 13,049 | 112 | 2 (one to resume after a background pass) |
| Sonnet | Claude Sonnet 5.5, high | 19:11:05–21:13:46 | 107 | 9,149 | 8,875 | 134 | 1 steer, 2 rounds (the last discarded) |

- **Terra** released its tab after 7.5 minutes with an unfinished study (flat sky
  bands and blob ridges) and said so itself. It was sent back three times: once to
  rebuild with a gradient sky, glow, ridge layers, shimmer and pines; once because a
  T3 server restart cut its turn; and once to fix a hard waterline seam, stray
  marks, reflections and sky banding. Each time it re-claimed the released tab
  (authorized).
- **Astra** finished after 81 minutes. One 15-minute round darkened the right face
  (the commission asks for light top, medium left, dark right), replaced scribbled
  zigzags with strata and stones, and laid the path.
- **Sol** finished in 46.5 minutes with no send-back.
- **Opus** worked 137 minutes before its first release, ending two turns while a
  stroke pass still ran in the background; it was told to run passes in the
  foreground. A 32-minute second round added the knowing smile the commission asks
  for (lifted cheeks, smile folds, crow's feet), a cool shadow glaze on the far side,
  and the scarf's knot and fringed ends.
- **Sonnet** worked about 96 minutes before a first release; the coordinator steered
  it once mid-run when Kleki's Unsaved Work dialog had swallowed a stroke batch. A
  25-minute round fixed the shadows, lemons and cut lemon. A last linen-only round
  made the image worse (v3), so the gallery uses v2, and the counts stop at the v2
  release (log line 9149, 21:13:46).

Infrastructure: the T3 server restarted twice (about 19:46 and 20:30). The children
were resumed. The first restart ended Terra's and Opus's turns, and the second
killed the coordinator's camera process: no camera frames exist from 20:30 to
21:23. The artists' own numbered screenshots cover that span in the time-lapses.

## Pen footage

The coordinator's camera called `ca browser screenshot` on each claimed `kleki-*`
tab about every 8 s. It filmed tabs whose artist was drawing right now (a running
`drag`, `click` or `move`) as fast as Chrome allows, and used no lease, because
reads need only the tab handle. Result: 7,394 captures, every one
`method: visible_tab`, 7,387 with `pen: "included"` and 7 with `pen:
"none"` (taken as a claim began, before its pen was drawn), and no failed capture.
The median capture took 0.76 s; the 90th percentile 0.95 s.

- `captureVisibleTab` allows about two calls a second **per extension**, so all
  agents and the camera share that budget. Filming five tabs at once gives each tab
  well under one frame a second; the camera favoured whoever was drawing.
- Every artist was filmed in at least two bursts at different stages (bursts are
  runs of 8 or more frames at most 3.5 s apart with the pen on the canvas: Terra 3,
  Astra 5, Sol 25, Opus 33, Sonnet 38).
- The README's pen moment is Sonnet's 20:06 burst: 25 frames while the cobalt jug
  fills in under the pen.

## CA observations

Bugs (reported here, not fixed):

1. **Medium: a screenshot to an existing file crashes without JSON.**
   `ca browser screenshot --tab H EXISTING.png` captures the tab, then fails in
   `save_png` (`browser_commands.py`) with a Python traceback, `FileExistsError:
   [Errno 17] File exists: '…'`, and exit status 1. Every other refusal is JSON.
   Sonnet's alpha-lock check wrote each probe capture to the same `_probe.png`, so
   after the first one 28 captures crashed (21 before the v2 release), and the check
   read the stale file instead. That fits the stale alpha-lock state Sonnet reported
   as its own mistake. The help text says existing files are not overwritten; the
   refusal should still be JSON, and it should come before the capture.
2. **Medium: a long drag times out while it keeps drawing.** Astra (a fill drag with
   about 300 `--via` points and `--interval 0`) and Opus (a long fill) got
   `{"ok": false, "error": "timeout", "detail": "The extension did not answer 'drag' within 30 s"}`,
   while the stroke landed in full (Astra) or in part (Opus). The CLI's 30 s default
   is shorter than a long drag in a visible tab, and the reply cannot tell whether
   the action finished. Both artists checked a fresh screenshot and split later
   strokes. This is the same timeout seen on the Mac.
3. **Linux: background tabs cannot be rendered** in headed Chrome for Testing 153 on
   Wayland (see the probe). The extension attaches the debugger, the capture times
   out, and the session stays attached, so the debugging bar appears for every
   window.

Observations:

- **Any same-user caller can capture another owner's claimed tab.** The camera
  captured all five artists' tabs with only their handles, no lease. Reads are not
  leased by design, so this is expected, but worth knowing: owner separation guards
  input, not observation.
- `locate` returns at most 20 elements by default. The reply's `count` gives the
  total (for example 23 against 20 elements), but there is no explicit `truncated`
  flag, and one artist read the list as complete (Low).
- A drag whose first or last point is outside the viewport is refused whole
  (`{"ok":false,"error":"point_outside_viewport","detail":"Point -10.3,1127.6 is outside the 1700x1291 viewport"}`),
  as documented. Artists clamp their points.
- The `open --window` reply reports `group: null` although the tab joins the CA group
  right after (Low, cosmetic).
- The guard worked as designed: one `viewport_changed` (Sonnet, 20:34, around the
  bar swap) and no stroke landed at a stale size.

## Kleki quirks

- **"Unsaved Work" dialog:** "Image was not saved in 40 minutes. Save now to prevent
  eventual loss." with **Save As PSD** and **Browser Storage … Store**. It appeared
  after 40 minutes and again about every 35–50 minutes (Opus saw it at 40, 75 and
  110). It is a full-page modal (`element_obscured`, `"covering": {"rect":
  {"height": 1291, "width": 1700, "x": 0, "y": 0}, "tag": "div"}`) that dims the
  canvas and swallows strokes; its × closes it. Sonnet lost one batch to it before
  the coordinator's steer.
- Number fields drop decimal points while typing: `type --clear 1.5` gave 5, `2.25`
  gave 25 and `1.14` gave 14, because Kleki applies each input event. Use integers.
- Brush sliders need a double tap to open their text field, with quiet time around
  it (Sol, from Kleki's source). The layer opacity slider moves only from a drag that
  starts on its handle; clicks or drags on the track do nothing. Verified by the
  coordinator: dragging the handle set a layer from 100% to 54%. This is a Kleki
  quirk, not a CA bug.
- The tools menu lists the current tool first, so fixed positions misfire. Alpha lock
  is one global toggle, invisible to `read`. Undo also reverts the selected layer.
  Escape does not close the hex dialog. The layer-preview popup stays open until its
  thumbnail is clicked again.

## How the README assets were made

- Gallery JPEGs at native canvas size (Kleki canvases of 960–1429 px; none upscaled).
- SVGs (wall, plates, chart, pipeline, lanes) generated by a Python script, with
  Gloock and Hanken Grotesk (SIL OFL) subset with fontTools and embedded as base64
  woff2, and images as JPEG data URIs, so they render inside GitHub's `<img>`.
- Progress GIFs from the camera frames plus the artists' own numbered screenshots:
  cropped to the canvas, frames kept only when more than 0.4% of the canvas changed
  (ignoring the pen box), frames with Kleki dialogs or the layer-preview popup
  dropped, one palette per GIF (ffmpeg `palettegen`/`paletteuse`), the clean final
  held for 2.8 s.
- The film: Remotion **4.0.537** (19 October 2026), 1920×1080 at 30 fps, rendered
  with `npx remotion render`; motion only from `useCurrentFrame`, `interpolate` and
  `spring`. All footage is real captured frames. The pen-cam segments follow the
  `pen_rect` each capture reported and crossfade between captured frames. The pen
  drawn on the title and end cards is a motion graphic from the pen's own geometry,
  not footage. The project source was kept out of the repository.

## Not verified

- Background tab rendering on Linux (it failed here), branded Chrome on Linux, and the
  user's own desktop browser.
- Whether long drags should time out later or report progress; that is a design
  question for the CLI.
