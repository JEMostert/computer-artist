# Browser tabs (`ca browser`)

`ca browser` operates tabs in the user's running Chrome/Chromium through the
Computer Artist extension, on any operating system and without a desktop backend.
Actions are DOM events inside the page, with a full mouse model for canvases: no
OS pointer, no keyboard device, and no tab switching unless you ask for `focus`. Use it when the user wants work done in
their own browser. For testing a web app in isolation, a private environment
browser (`ca env browser`) remains the default.

## Steps

1. `ca browser instances`. With none connected, run `ca browser doctor` and report
   its remedy. Installing or reloading the extension needs the user (Developer
   mode, Load unpacked, the card's reload button); tell them, never work around it.
   With several instances, pass `--instance` or full `INSTANCE:TAB` handles every time.
2. Work in a tab you open: `ca browser open URL --owner NAME` returns a background
   tab `handle` and a `lease`. Add `--agent-window` to keep agent tabs in their own
   unfocused window (they render at full speed there). Claim an existing tab
   (`ca browser claim --tab H`) only when the user asked you to work in that tab.
3. Observe, then act: `read`, `locate` (returns `ref`s) and `wait`; then `click`,
   `type`, `select`, `press`, `drag`, `move`, `scroll` or `navigate` with
   `--tab H --lease L`. Re-locate after any navigation; refs from an earlier page are
   refused as `stale_element`. `tabs` marks pages Chrome forbids (`scriptable: false`).
4. Verify the outcome by reading it back, after `navigate --reload` when it must
   persist. A dispatched click is a claim, not proof.
5. Finish: `close` tabs you opened and `release` tabs you claimed. If you lost a
   lease, `ca browser cleanup --owner NAME` ends your own recorded claims.

Screenshots work on background tabs too: `ca browser screenshot --tab H out.png`
renders the tab without switching to it (`method: debugger`), so routine visual
checks need neither `focus` nor the user's attention. Use `focus` only when the user
should look.

Done when the outcome reads back as expected and every claim you made is closed
or released.

## Canvases, drawing and coordinates

Coordinates are CSS pixels: `--x/--y` (and `drag --from X Y [--via X Y] --to X Y`)
are offsets from an element's top-left, or viewport points without an element.
For a canvas, take a `screenshot` (no `focus` needed) and divide image pixels by its
`scale` to get viewport points, or use offsets from the canvas `locate` rect. Pick
tools by their tooltip (`--text 'Single bond'`), then click or drag on the canvas.
Hover-gated toolbars work because clicks send the hover events first.

Editors inside a same-origin iframe: pass `--frame '#id'` to find their controls;
points reach into the frame by themselves. When a target is only inside a frame,
`locate` and the refusal name the `--frame` to pass. Cross-origin frames are refused.

Verify each edit with a fresh screenshot (the pen is left out of captures) or page
text that reflects it, such as search results. Do not save shared records to test.

## Native selects

`locate` on a `<select>` lists each option's exact value, label, and selected and
disabled state. `ca browser select --tab H --lease L --selector '#colour' VALUE`
chooses one value without opening the OS popup, firing `input` then `change` only
when the selection changes. Multiple selects and disabled options or controls
are refused.

## The user watching and taking over

The user may switch to your tab or focus Chrome at any time to watch; that keeps
every lease. A real click, key, scroll or touch inside a claimed page ends only
that tab's lease (`ownership_revoked`, `stop_reason` `human_input`). Stop acting
there and reclaim only if they ask.

`ca browser focus --tab H --lease L` brings your tab and its window to the front
and takes the user's keyboard focus. Use it only when the user asked to watch,
when you need their attention (a question, a handoff, a finished result), or for
visible testing; never for background work.

## Refusals

- `ownership_revoked`: see above. If the pen was still moving, the action did not
  happen.
- `ambiguous_target` lists candidates: choose a `ref`, or pass `--first` only when
  any match is acceptable.
- `capture_unavailable`: Chrome would not render that tab in the background
  (another debugger is attached). Use `read`/`locate`, or `focus` if the user agrees.
- `page_unavailable`: browser pages, the Web Store and (without file access)
  `file://` URLs cannot be scripted; `restriction` says which.
- `point_outside_viewport`, `cross_origin_frame`, `frame_not_found`: fix the point
  or frame; viewport points are not scrolled.
- `extension_outdated`, or a `doctor` warning `extension_reload_needed`: run
  `ca browser install`, then `ca browser reload` (it keeps claims; it refuses while
  requests run). `manual_reload_needed` means the running extension predates 0.7.0:
  ask the user once to press reload on the Computer Artist card.

Events are untrusted (`isTrusted: false`). Sites that demand real gestures (file
pickers, clipboard, popups, native drag and drop) refuse them; tell the user instead
of retrying. Every
reply is JSON; refusals use `{"ok": false, "error": CODE, "detail": ...}` and exit
with status 1. See [docs/BROWSER.md](../../../docs/BROWSER.md) for installation,
transport and platform limits.
