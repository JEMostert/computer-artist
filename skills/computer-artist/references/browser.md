# Browser tabs (`ca browser`)

`ca browser` operates tabs in the user's running Chrome/Chromium through the
Computer Artist extension. It works on every operating system the browser runs on
and needs no desktop backend. Actions are DOM events inside the page: no pointer,
no keyboard device, no tab switching. Use it when the user wants work done in
their own browser. For testing a web app in isolation, a private environment
browser (`ca env browser`) remains the default.

## Steps

1. `ca browser instances`. With none connected, run `ca browser doctor` and report
   its remedy. Installing the extension needs the user (Developer mode, Load
   unpacked); tell them, never work around it. With several instances, pick one
   by ID or label and pass `--instance` or full `INSTANCE:TAB` handles every time.
2. Work in a tab you open: `ca browser open URL --owner NAME` returns a background
   tab `handle` and a `lease`. Claim an existing tab (`ca browser claim --tab H`)
   only when the user asked you to work in that tab.
3. Observe, then act: `read`, `locate` (returns `ref`s) and `wait`; then `click`,
   `type`, `press` or `navigate` with `--tab H --lease L`. Re-locate after any
   navigation, because refs from an earlier page are refused as `stale_element`.
4. Verify the outcome by reading it back, after `navigate --reload` when it must
   persist. A dispatched click is a claim, not proof.
5. Finish: `ca browser close` for tabs you opened, `ca browser release` for tabs
   you claimed.

Done when the outcome reads back as expected and every claim you made is closed
or released.

## Refusals

- `ownership_revoked` (`stop_reason` `human_input` or `human_activated_tab`): the
  user took the tab, possibly while the pen was still moving, in which case the
  action did not happen. Stop acting in it; reclaim only if they ask.
- `ambiguous_target` lists candidates: choose a `ref`, or pass `--first` only when
  any match is acceptable.
- `tab_not_visible`: screenshots work only on the active tab, and ca never
  switches tabs. Use `read`/`locate`, or ask the user to show the tab.
- `page_unavailable`: browser pages, the Web Store and (without file access)
  `file://` URLs cannot be scripted.

Events are untrusted (`isTrusted: false`). Sites that demand real gestures (file
pickers, clipboard, popups) refuse them; tell the user instead of retrying. See
[docs/BROWSER.md](../../../docs/BROWSER.md) for installation, transport and
platform limits.
