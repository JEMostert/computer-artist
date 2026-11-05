# Browser tabs (`ca browser`)

`ca browser` operates tabs in the user's running Chrome/Chromium through the
Computer Artist extension, on any operating system and without a desktop backend.
Actions are DOM events inside the page, with a full mouse model for canvases: no
OS pointer, no keyboard device, and no tab switching unless you ask for `focus`. It is
the first choice for web pages: work in the Chrome the user already runs, in your own
tabs next to theirs. Use a private environment browser (`ca env browser`) only when
the user asks for an isolated browser.

## Steps

1. `ca browser instances` first, every time. When an instance is connected, work
   there. Never start another Chrome, a new profile, Chrome for Testing or a private
   browser for this work. With none connected, run `ca browser doctor` and tell the
   user plainly that no Chrome with the extension is running; start a new browser
   only after saying so and with their agreement, never silently. Installing or
   reloading the extension needs the user (Developer mode, Load unpacked, the card's
   reload button); tell them, never work around it. With several instances, pass
   `--instance` or full `INSTANCE:TAB` handles every time.
2. Open your session: `ca browser open URL --owner NAME` returns a background tab
   `handle` and a `lease`. Use a distinct owner per agent. Add `--agent-window` to
   keep agent tabs in their own unfocused window. Claim an existing tab
   (`ca browser claim --tab H`) only when the user asked you to work in that tab.
   Never act in, capture or close the user's own tabs.
3. Observe, then act: `read`, `locate` (returns `ref`s) and `wait`; then `click`,
   `type`, `select`, `press`, `drag`, `move`, `scroll` or `navigate` with
   `--tab H --lease L`. Re-locate after any navigation; refs from an earlier page are
   refused as `stale_element`. `tabs` marks pages Chrome forbids (`scriptable: false`).
4. Verify the outcome by reading it back, after `navigate --reload` when it must
   persist. A dispatched click is a claim, not proof.
5. Close your session as soon as you are done: `close` tabs you opened and
   `release` tabs you claimed. If you lost a lease, `ca browser cleanup --owner NAME`
   ends your own recorded claims.

## Sessions, screenshots and the pen

A claim is your session on the tab. While it lasts, your teal pen with your owner
name stays on the page, so the user knows an agent works there; no pen means the tab
is theirs again.

`ca browser screenshot --tab H out.png` works on your background tabs without
switching to them (`method: debugger`), so routine visual checks need neither
`focus` nor the user's attention. The first one attaches Chrome's debugger for the
rest of the session, which shows "Computer Artist started debugging this browser" in
every window until the last agent's session ends; that is expected and appears once.
Screenshots taken before and after the bar appears can differ in viewport height:
use each reply's `viewport` and `scale`. Keep
sessions short and close them: an idle session detaches after 5 minutes without a
request, but the bar moves the user's page while it shows. Unclaimed background tabs
are refused (`claim_required`).

A screenshot of a tab on screen includes your pen (`pen: included`, `pen_rect` gives
its box); ignore those pixels. Background renderings leave it out (`pen: excluded`).
Use `focus` only when the user should look.

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

Verify each edit with a fresh screenshot (mind `pen: included`) or page text that
reflects it, such as search results. Do not save shared records to test.

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
  (another debugger is attached, or `stop_reason: debugging_cancelled` because the
  user pressed Cancel on the debugging bar; do not retry for that claim). Use
  `read`/`locate`, or `focus` if the user agrees.
- `claim_required`: background screenshots need your claim on the tab.
- `no_instances`: no Chrome with the extension runs; tell the user (step 1).
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
