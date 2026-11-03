# Chrome extension bridge, 3 November 2026

Host: Fedora 43 (Linux 7.2.9), GNOME Wayland, Python 3.14.8. Browser: Google
Chrome for Testing 153.0.8010.12 (Playwright build 1243). Every browser used a
disposable profile, and the host was registered only with `--user-data-dir`. No
user browser profile or keyring was touched.

## Headless end-to-end check

`tests/integration/check-browser.py` started a localhost notes fixture and two
separate headless Chrome processes with the extension loaded. It then drove them
only through `ca browser`. All 24 checks passed:

- Scoped install; two instances discovered and labelled; an instance-less command
  refused as `ambiguous_instance`.
- Two agents at once, one per instance: each opened a background tab, located,
  typed and clicked. The form reached the server, and after `navigate --reload` the
  note read back from the page. The focused window and active tab stayed the same
  before and after.
- Refusals: `stale_element` (ref from before the reload), `lease_mismatch`,
  `tab_claimed`, `wrong_instance`, `target_unavailable`, and `tab_not_visible` for
  a background screenshot.
- An existing active tab was claimed and captured (780×437 PNG).
- Trusted DevTools input standing in for the human revoked the lease
  (`human_input`), and the next click was refused with `ownership_revoked`.
  Activating the agent's tab revoked its lease (`human_activated_tab`).
- After the native host was killed with SIGKILL, requests failed with
  `instance_unavailable`. The extension then started a new host (new PID), and the
  existing lease still held.
- After 40 seconds idle the host PID was unchanged: the native port kept the MV3
  service worker alive.
- Terminating one browser pruned its record, and its tabs were refused. The
  rendezvous folder was empty afterwards apart from the key.

## Headed check in a private KWin desktop

A private host environment (`ca env start ca-browser-proof`) ran headed Chrome for
Testing on Wayland with the fixture's "Other page" in the active tab, standing in
for a browser the user already had open. Steps:

1. `ca browser open` created a background tab in a blue **CA** group.
2. `type` (×2), `click`, `navigate --reload` and `read` returned
   `Headed note: written while you watch another tab`, and the fixture store held
   the same record.
3. A KWin capture of the browser window before and after showed the human's tab
   still active and unchanged
   ([after](../evidence/chrome-extension/headed-background-tab.png)).
4. A screenshot of the background tab was refused. Claiming the active tab gave a
   1018×695 `captureVisibleTab` PNG. A DOM click on its button changed the page to
   "Pressed" without revoking the lease, and the claim was then released.

The bridge never sends operating-system input, so these runs used no pointer or
keyboard device at all. The headless browsers had no desktop windows. The headed
browser ran on a private KWin desktop, not on the user's GNOME session.

## Also run

- `python -m unittest discover -s tests`: 231 tests, 9 skipped.
- `scripts/check.sh`: ruff, plus `node --check` of the service worker.
- `pip wheel --no-deps .`: the wheel contains `computer_artist/chrome_extension`.

## Not verified

- Branded Google Chrome, Chromium distribution packages, Brave, Edge and Vivaldi.
- macOS and Windows with a real browser. Their transport is covered by
  `tests/test_browser.py` on CI, where the Windows job runs the TCP path.
- The user's own desktop browser, and pages that require trusted gestures.

Test note: on this GNOME host, a Chrome for Testing profile without
`--password-store=basic` stalled every network request while waiting for the
desktop keyring, so the check always passes that flag.
