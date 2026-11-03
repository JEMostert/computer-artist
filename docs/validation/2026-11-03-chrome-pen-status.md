# Chrome pen and status card, 3 November 2026

Host: macOS 27.0.1 (Apple Silicon), Python 3.13. Browser: headless Chrome for Testing
153.0.8010.12 (Playwright build 1243) with a disposable profile, its own
rendezvous folder (`CA_BROWSER_DIR`) and data folder (`CA_BROWSER_DATA_DIR`). The
native host was registered only with `--user-data-dir`. The user's Chrome profile
and bridge were not touched. Extension 0.5.1.

## Pen

Driven through `ca browser` on the localhost notes fixture, with DevTools standing
in for the human and capturing frames:

- In a claimed visible tab, `type` and `click` showed the teal pen gliding to the
  target with a fading ink trail, writing along the field, splashing on the click
  and resting with the owner tag. The form submitted and the note read back.
- An earlier pen resting over the **Add note** button did not trigger
  `element_obscured`. A click on a button inside a modal `<dialog>` drew the pen
  above the backdrop and closed the dialog.
- A trusted DevTools click 0.30 s after starting `ca browser click` arrived while
  the pen was moving. The action was refused with `ownership_revoked`
  (`human_input`), and no note was added. Later clicks landed after the action,
  which completed, and then ended the claim as before.
- Trusted clicks inside the page's iframe 0.18 to 0.30 s into a glide ended the
  claim from that frame. All four actions were refused with `ownership_revoked`
  (`human_input`) and no note was added. A later fresh claim still typed and
  clicked normally.
- After human input, and after the human activated an agent's background tab, the
  tag changed to **Back to you** and the pen lifted away (`human_input`,
  `human_activated_tab`).
- In background tabs, `click` and `type` returned in 0.14 s and 0.11 s, about the
  CLI's own 0.11 s overhead, so there was no glide delay. The pen was parked where
  the agent acted.

## Status card

Opened with `chrome.action.openPopup()` and captured in light and dark mode. With
two claims (one on the viewed tab), it showed the viewed tab's owner and latest
action, both agent tabs with host and latest action, and **You switched to "Other
page"** from a revoked claim. With the host launcher temporarily removed, it
showed **Not connected** with Chrome's reason ("Specified native messaging host
not found.") and the fix. The opened-tab group was created green.

## Regression run

`tests/integration/check-browser.py` passed all 26 checks against two headless
Chrome for Testing instances (run with `TMPDIR=/tmp`, because the default macOS
temporary folder makes the socket path longer than the 104-byte limit).

## Not verified here

Branded Chrome, headed windows and real human input were not used in this record.
The installed extension in the user's Chrome is updated separately after merge.
