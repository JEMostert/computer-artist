# Browser tabs (`ca browser`)

`ca browser` operates tabs in the user's running Chrome/Chromium through the
Computer Artist extension, on any operating system and without a desktop backend.
Actions are DOM events inside the page: no pointer, no keyboard device, and no
tab switching unless you ask for `focus`. Use it when the user wants work done in
their own browser. For testing a web app in isolation, a private environment
browser (`ca env browser`) remains the default.

## Steps

1. `ca browser instances`. With none connected, run `ca browser doctor` and report
   its remedy. Installing or reloading the extension needs the user (Developer
   mode, Load unpacked, the card's reload button); tell them, never work around it.
   With several instances, pass `--instance` or full `INSTANCE:TAB` handles every time.
2. Work in a tab you open: `ca browser open URL --owner NAME` returns a background
   tab `handle` and a `lease`. Claim an existing tab (`ca browser claim --tab H`)
   only when the user asked you to work in that tab.
3. Observe, then act: `read`, `locate` (returns `ref`s) and `wait`; then `click`,
   `type`, `select`, `press` or `navigate` with `--tab H --lease L`. Re-locate after
   any navigation; refs from an earlier page are refused as `stale_element`.
4. Verify the outcome by reading it back, after `navigate --reload` when it must
   persist. A dispatched click is a claim, not proof.
5. Finish: `close` tabs you opened and `release` tabs you claimed.

Done when the outcome reads back as expected and every claim you made is closed
or released.

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
- `tab_not_visible`: screenshots need the active tab of a visible window. Use
  `read`/`locate`, or `focus` when the user should see it.
- `page_unavailable`: browser pages, the Web Store and (without file access)
  `file://` URLs cannot be scripted.

Events are untrusted (`isTrusted: false`). Sites that demand real gestures (file
pickers, clipboard, popups) refuse them; tell the user instead of retrying. Every
reply is JSON; refusals use `{"ok": false, "error": CODE, "detail": ...}` and exit
with status 1. See [docs/BROWSER.md](../../../docs/BROWSER.md) for installation,
transport and platform limits.
