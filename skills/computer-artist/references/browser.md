# Chrome tabs (`ca browser`)

`ca browser` works in the Chrome the user already runs, through the Computer Artist
extension, on any operating system. Actions are DOM events inside the page: no OS
pointer or keyboard, and no tab switching. You work in your own tabs next to the
user's. Every reply is JSON; refusals are `{"ok": false, "error": CODE, "detail": ...}`.

## Steps

1. **Find the browser.** Run `ca browser instances`. If a Chrome with the extension
   is listed, work there. If none is, run `ca browser doctor`, tell the user that no
   Chrome with the extension is running, and start a browser only after they agree.
   If several are listed, ask the user or coordinator which one, and pass
   `--instance ID` on every command.
2. **Open your own tab.** `ca browser open URL --owner OWNER` returns a `handle`
   and a `lease`; keep both. OWNER is the name the coordinator gave you; working
   alone, use two or three words of your task joined by hyphens, such as
   `note-check`. This starts your session: your green pen with your owner name
   appears on the page and stays until the session ends.
3. **Read, act, read back.**
   - Read with `ca browser read --tab H`, find elements with
     `ca browser locate --tab H --text 'Save'` (or `--role button --name Save`,
     `--selector CSS`), and wait with `ca browser wait --tab H --text 'Saved'`.
     `locate` returns `ref`s.
   - Act with `click`, `type`, `select`, `press`, `drag`, `move`, `scroll` or
     `navigate`, always with `--tab H --lease L`, choosing the element by `--ref`.
     For example: `ca browser type --tab H --lease L --ref REF 'hello'`.
   - Read the result back with `read`, `locate` or `wait`; after `navigate --reload`
     when it must persist. A dispatched click is not proof. Locate again after any
     navigation.
4. **Screenshot only your own tabs, and measure on the latest one.** Run
   `ca browser screenshot --tab H FILE` with a new numbered file each time
   (`01-start.png`, `02-...`) in a `ca-OWNER` folder inside your scratch or output
   folder, or else inside `${TMPDIR:-/tmp}` (`$env:TEMP` on Windows); ca creates it.
   Never capture the user's tabs.
   - Your first screenshot brings up Chrome's bar "Computer Artist started
     debugging this browser" until your session ends. The bar can shrink the
     viewport (by 56 pixels in one test), so take a second screenshot before you
     measure.
   - Measure only on your latest screenshot, and pass its `viewport` as
     `--viewport WIDTHxHEIGHT` to every `click`, `move`, `drag` or `scroll` with
     coordinates. When a reply's `viewport` or `scale` differs from it, or ca
     refuses with `viewport_changed`, take a new screenshot and measure again.
5. **Finish every tab.** You are done when the result reads back as expected and
   every tab you opened or claimed is finished one of these ways, as soon as you
   are done:
   - **Nobody needs to see it:** close tabs you opened (`ca browser close --tab H
     --lease L`) and release tabs you claimed (`ca browser release --tab H --lease
     L`). This ends your session, and the pen disappears.
   - **The user should see the result:** keep the tab open and keep its lease. Tell
     the user which tab holds it, focus it if they need to look now (see
     Details), and close it, or release a tab you claimed, when they say they are
     done.
   - **Handing a tab over for good:** release it. It stays open as the user's tab
     and leaves the CA group.

## If something goes wrong

- `stop_reason: target_closed`: your tab was closed outside ca, for example by the
  user. Stop, and do not open it again without asking. Report it with `stopped_at`
  from `ca browser status --tab H`, then run `ca browser cleanup --owner OWNER`.
- `viewport_changed`: see step 4. The reply's `viewport` is the current size.
- `human_active`: the user is using your tab. Your lease holds and nothing
  happened (`performed: false`) after ca waited `human_waited_ms` for them to pause.
  Wait at least `retry_after_ms`, then repeat the action. With `gesture:
  cancelled`, your drag stopped after `moved` steps: check the page before drawing
  it again.
- `not_agent_tab`: the user opened that tab, so release it instead of closing it.
- `ambiguous_target`: choose one of the listed candidates by `ref`; pass `--first`
  only when any match will do.
- `stale_element`: the page changed or navigated. Run `locate` again.
- Elements only inside an iframe: `locate` and the refusal name the `--frame
  SELECTOR` to add. `cross_origin_frame` cannot be reached.
- `page_unavailable`: Chrome forbids scripting this page (`chrome://`, the Web
  Store, or `file://` without file access). Use another page.
- `claim_required`: the tab is not on screen and you hold no claim on it. Capture
  only tabs you opened or claimed.
- `capture_unavailable`: Chrome would not render the tab. With `stop_reason:
  debugging_cancelled`, the user pressed Cancel on the debugging bar; do not retry
  for that tab. Use `read` and `locate` instead.
- `no_instances`: see step 1.
- `extension_outdated`, or `extension_reload_needed` from `doctor`: run
  `ca browser install`, then `ca browser reload`. `manual_reload_needed`: ask the
  user once to press reload on the Computer Artist card in `chrome://extensions`.
- A site ignores the action: events are untrusted (`isTrusted: false`), so file
  pickers, clipboard, popups and native drag and drop refuse them. Tell the user
  instead of retrying.
- You lost a lease: `ca browser cleanup --owner OWNER` closes and releases your own
  recorded tabs.

## Details

- **Claim a tab the user has open** (`ca browser claim --tab H --owner OWNER`) only
  when they asked you to work in that tab. Release it when done.
- **Show the user something:** `ca browser focus --tab H --lease L` brings your tab
  to the front and takes their keyboard focus. Focus once, at handoff, only the one
  tab the user needs to look at, or during the work when they asked to watch.
  Background and parallel work stays unfocused. The user may switch to your tab or
  use it at any time; that keeps your lease.
- **The agents' window:** add `--agent-window` to `open` only when the user or
  coordinator asks for agent tabs in a window of their own. If the platform focused
  it anyway, the reply has `focus_returned`. `user_window`: focus went back to the
  user's browser window, and your tab renders as shown. `minimized`: the user was in
  another application, so the agent window was minimized to give it back; on Linux
  its tabs cannot be captured until shown. Tell the user instead of focusing it.
- **A screenshot of a tab on screen** (the user switched to it) includes your pen
  (`pen: included`, with `pen_rect`); ignore those pixels. Background screenshots
  leave it out (`pen: excluded`).
- **Coordinates** are CSS pixels. `--x/--y` and `drag --from X Y [--via X Y] --to X Y`
  are offsets from the element's top-left, or viewport points without an element.
  For a canvas, divide screenshot pixels by `scale`. Pick toolbar tools by their
  tooltip (`--text 'Single bond'`). For drawing, see [canvas
  drawing](operating-tips.md#drawing-on-a-canvas).
- **Native selects:** `locate` on a `<select>` lists each option's value;
  `ca browser select --tab H --lease L --selector '#colour' VALUE` chooses one.
- **Do not save shared records** just to test something.

See [Chrome extension](../../../docs/BROWSER.md) for installation, sessions and
platform limits.
