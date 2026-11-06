# Chrome: the agents' window gives focus back (6 November 2026)

Host: Fedora 43, KWin 6.7.5. Chrome for Testing 153.0.8010.12 and Vivaldi 8.2.4133.94
with extension 0.11.0, in disposable profiles. The headed runs used a private KWin desktop
(`ca env start ca-focus-proof`, its own Wayland socket and session bus). No user
profile or desktop was touched. A KWin script, loaded through that desktop's own
session bus, reported KWin's active window and stacking order.

## Problem

`ca browser open --agent-window` took keyboard focus from the user's window
(`agent_window_unfocused_and_reused` failed on `main` and in 0.10.0). There were two causes:

1. **Reuse, every platform.** A later agent tab was created with
   `chrome.tabs.create({windowId, active: true})`. Headless Chrome then reported the
   agent window as focused next to the user's. Chrome focuses the window a tab is
   created active in. Creating it inactive and then activating it does not.
2. **New window, KWin on Wayland.** `chrome.windows.create({focused: false})` is not
   honoured. KWin activated and raised the new window about 70 ms after `create`
   returned. `chrome.windows.update(agent, {focused: false})` changed nothing. Once
   the window existed, `chrome.windows.update(user, {focused: true})` gave the
   user's window focus back and raised it above the agent window again (checked in
   KWin's stacking order). When another application (KWrite) had focus, KWin also
   focused the new window. Chrome cannot focus another application, but minimizing
   the agent window made KWin return focus to KWrite.

Vivaldi adds a third case: it focuses a window when any tab is created in it, active
or not, about 10 ms after the call.

## Decision

The extension creates later agent tabs inactive and then activates them. It also
listens for focus moving to the agent window: for up to 1.5 seconds after creating it
and 300 ms after adding a tab. If focus moves there, the extension gives it back:

- If one of the browser's windows had focus, that window gets focus back
  (`focus_returned: user_window`). The agent window stays shown, so its tabs render
  at full speed.
- If another application had focus, the extension minimizes the agent window
  (`focus_returned: minimized`), and KWin returns focus to that application.

Focus still moves briefly before it is handed back. Minimizing also costs the
agent's view: on Linux with Wayland, tabs in a minimized window cannot be captured
(`capture_unavailable` in Vivaldi). We kept the user's focus over the agent's view,
because the user's keyboard matters more than one screenshot. The reply says which
path ran.

## Results

| Run | Result |
| --- | --- |
| `check-browser.py` headless, Chrome for Testing | 69 of 69 checks passed; `agent_window_unfocused_and_reused` passed with no `focus_returned` (focus never moved) |
| `check-browser.py --headed`, private KWin | 70 of 70 passed. `agent_window_unfocused_and_reused`: `focus_returned: user_window`, alpha's focused windows unchanged, second tab `visible_tab`. New headed-only `agent_window_leaves_other_app_focused`: with beta's browser focused, alpha's new agent window was `minimized`, and the focused windows were unchanged in both browsers |
| Vivaldi headed, private KWin, real extension (scenario script, not the full check, whose fixture assumes Chrome's `about:blank` start page) | User window focused: both opens returned `focus_returned: user_window`. KWin then reported the user's window active and on top, and the agent tab captured as `visible_tab`. KWrite focused: both opens returned `minimized`, KWrite stayed active, and the screenshot was `capture_unavailable` |
| Short timeout, headless | `open --agent-window --timeout 0.1` returned `ok` with `loaded: false` and a lease, and the tab was claimed. The load timeout no longer ends the request before the focus wait has created the tab |
| Reproduction before the fix | Headless `check-browser.py` on 0.10.0 code: `agent_window_unfocused_and_reused` failed with both windows focused |

## Limits

- Focus moves to the agent window for a moment before it is handed back (between
  KWin's activation and the hand-back), so a key typed in that instant can reach it.
- When another application had focus, the agent window is minimized. Its tabs then
  render like background tabs and, on Linux with Wayland, cannot be captured until the
  user shows the window or the agent runs `ca browser focus` (which takes focus).
- A compositor that focuses the window later than the wait (1.5 s for a new window,
  300 ms for a tab) keeps the focus. Platforms that honour an unfocused window, and
  headless Chrome, never take the fallback, but the reply then comes up to that long
  later.
- GNOME, X11, Windows and macOS were not tested with 0.11.0, and neither were the
  user's own browsers.
