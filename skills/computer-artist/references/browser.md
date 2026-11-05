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
4. **Screenshot only your own tabs.** Run `ca browser screenshot --tab H FILE`
   with a new file each time, numbered in order (`01-saved.png`, `02-...`), in a
   folder named `ca-OWNER` inside your scratch or output folder, or else inside
   `${TMPDIR:-/tmp}` (`$env:TEMP` on Windows). ca creates the folder. Never capture
   the user's tabs. Background screenshots leave your pen out (`pen: excluded`).
   The first one makes Chrome show "Computer Artist started debugging this
   browser" above its windows until your session ends; that is expected, and the
   bar is not part of the screenshot.
5. **Close or release.** `ca browser close --tab H --lease L` for a tab you opened,
   or `ca browser release --tab H --lease L` for a tab you claimed. This ends your
   session; the pen disappears. Do it as soon as you are done. You are done when
   the result reads back as expected and every tab you opened or claimed is closed
   or released.

## If something goes wrong

- `ownership_revoked` with `stop_reason: human_input`: the user clicked or typed in
  your tab and took it back. Stop working there; claim it again only if they ask.
  If the pen was still moving, the action did not happen.
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
  to the front and takes their keyboard focus. Use it only when they asked to watch
  or you need their attention, never for background work. The user may switch to
  your tab at any time; that keeps your lease.
- **The agents' window:** add `--agent-window` to `open` only when the user or
  coordinator asks for agent tabs in a window of their own.
- **A screenshot of a tab on screen** (the user switched to it) includes your pen
  (`pen: included`, with `pen_rect`); ignore those pixels. Each reply gives
  `viewport` and `scale`; use the latest, because the debugging bar can change the
  viewport height once.
- **Coordinates** are CSS pixels. `--x/--y` and `drag --from X Y [--via X Y] --to X Y`
  are offsets from the element's top-left, or viewport points without an element.
  For a canvas, divide screenshot pixels by `scale`. Pick toolbar tools by their
  tooltip (`--text 'Single bond'`).
- **Native selects:** `locate` on a `<select>` lists each option's value;
  `ca browser select --tab H --lease L --selector '#colour' VALUE` chooses one.
- **Do not save shared records** just to test something.

See [Chrome extension](../../../docs/BROWSER.md) for installation, sessions and
platform limits.
