# Chrome background work, 4 November 2026

Host: macOS 27 (Apple Silicon), Python 3.13. Extension 0.7.0. Browser: Chrome for
Testing 153.0.8010.12 with disposable profiles.

## Why

Routine visual checks in `ca browser` needed the tab in front: screenshots used
`chrome.tabs.captureVisibleTab`, so an agent had to `focus` its tab and take the
user's foreground, or ask first. Claims lived in session storage, so reloading the
extension after an update orphaned every agent tab, and an agent that lost a lease
could not close its own tab. Nothing told which build a browser ran when the
version number stayed the same, and every update needed the user's click.

## Measurements before building

Probes used a scratch extension with the `debugger` permission, driven from its
service worker, on a page with a timer-driven canvas and a same-origin iframe
canvas, opened as a background tab next to a "user" tab.

| Approach | Result |
| --- | --- |
| `chrome.debugger` + `Page.captureScreenshot` on a background tab | Fresh render including both canvases, 0.04 to 0.05 s per capture; the tab stayed `hidden`; focused window and active tab unchanged |
| Same, tab in a minimized window | Rendered, 0.03 s |
| `chrome.windows.create({focused: false})` + `captureVisibleTab` | Window created in 0.1 s, focused window unchanged, capture 0.02 s, page `visible` |
| User tab viewport while the debugger was attached (headful) | 557 px before, during and after |

The headful run was launched with `open -g`, yet Chrome for Testing made itself the
frontmost application at start-up; it was closed right after. That start-up
activation is the test browser's, not something the extension does. Whether
branded Chrome shows a "started debugging" notice during a capture was not visible
to these probes (captures show page content only).

A reload probe found why `chrome.runtime.reload()` and Chrome's own reload had
unloaded the extension in earlier test profiles: with Developer mode off, Chrome
disables an unpacked extension when it reloads (`unsupportedDeveloperExtension`).
With Developer mode on, as a user who loaded it unpacked has it, both reloads work.

Upgrade path, in a profile with Developer mode on and the extension loaded
unpacked: the merged 0.6.0 build ran, this build was installed over it, and the
card's reload (`developerPrivate.reload`) loaded it. The extension stayed enabled,
the new "Access the page debugger backend" permission was granted without a prompt,
`doctor` warned `extension_reload_needed` before and nothing after, and a background
screenshot used the debugger. A 0.6.0 claim did not survive, since 0.6.0 kept claims
in session storage. Then `ca browser reload` came back in 0.4 s with a 0.7.0 claim
kept, and `cleanup --owner` closed the open tab and reported the lost one as ended.

## Implementation

- `screenshot` captures the active tab of a shown window as before and any other
  tab through the debugger, attached for that capture only, one at a time per tab,
  with the pen hidden. Replies name the `method`.
- `open --agent-window` opens agent tabs in one unfocused window, reused.
- Claims are in local storage, restored after a reload only for tabs still showing
  the claimed URL; the human-input watcher replaces its orphaned predecessor.
- The CLI keeps an owner-only lease journal; `cleanup --owner` closes or releases
  only that owner's tabs through their leases.
- Install stamps a build digest into the worker; `instances`/`doctor` report it and
  `reload` loads the installed build, refusing while another request runs and
  explaining when an extension predates it.
- The CLI resends a request after a host restart when it was never delivered, and
  reads also when the connection dropped mid-request.
- Drags paced with `--interval` in hidden tabs no longer wait for Chrome's
  once-a-second timer throttling.

## Real Chrome regression

`tests/integration/check-browser.py` now loads the extension the way "Load unpacked"
does, with Developer mode on, and passed all 58 checks. New ones:

- A background tab captured by the debugger without changing the focused window or
  active tab; the iframe canvas mark (900 px) appears in it, in 0.11 s through the CLI.
- Three captures at once across two agents' tabs all succeeded.
- A trusted click in the user's own tab during a paced background drag left the
  agent's drag and claim intact.
- Two simultaneous first `--agent-window` opens in another browser shared one window.
- Two `--agent-window` tabs shared one new window; the user's focused window and
  active tab were unchanged; the active agent tab captured as `visible_tab`.
- `cleanup --owner viewer-2` closed that owner's tab and left `viewer`'s claim.
- After Chrome's own reload of the extension, the instance came back with the same
  build, the claim held and accepted a click, a tab opened through a redirect kept its
  claim, `ca browser reload` reported no reload
  needed, and a trusted click in the claimed tab still ended its claim.
