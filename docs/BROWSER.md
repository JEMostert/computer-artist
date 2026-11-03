# Browser extension

The Computer Artist extension lets `ca browser` read and operate tabs in Chrome or
Chromium windows you already have open, in your own profile, while you keep using
the browser. Each browser profile with the extension installed becomes a
**browser instance** that `ca` can discover, select and drive.

Actions are DOM operations inside the page. `ca browser` never moves your pointer,
never sends operating-system input, and never activates a tab or focuses a window.
Agents normally work in background tabs that they open themselves. This is
separation by design, not a security sandbox. The extension can read and change any
page it is allowed to script, and anything running as your user can reach the local
bridge.

![A headed browser keeps the human's tab in front while the agent's tab sits in a CA group](evidence/chrome-extension/headed-background-tab.png)

## Install

From a checkout or an installed `computer-artist` package:

```bash
ca browser install          # registers the native host with detected browsers
```

The command does three things:

- It copies the extension to `~/.local/share/computer-artist/chrome-extension`
  (`$XDG_DATA_HOME` is respected).
- It writes a small launcher that runs the current Python with
  `computer_artist.browser_host`.
- It registers the native messaging host `com.computer_artist.browser` for every
  browser whose profile folder exists.

Pass `--browser NAME` (repeatable) to choose browsers, `--user-data-dir DIR` for a
browser started with a custom profile folder, and `--dry-run` to preview. The
launcher points at this checkout or environment, so keep it in place.

Then load the extension once in each browser:

1. Open `chrome://extensions` (or your browser's extensions page) and turn on
   **Developer mode**.
2. Choose **Load unpacked** and select the `extension_dir` that install printed.
3. Run `ca browser instances`. Each connected profile lists its instance ID.

After updating Computer Artist, run `ca browser install` again and press the
reload button on the extension card. The extension's ID is fixed by the `key` in
its manifest (`fbbljgpbpgepkaimgbpcpemjdllnname`), so the host registration keeps
matching across reinstalls.

`ca browser doctor` checks the installed files, host registrations and connected
instances without changing anything. A red **!** badge on the toolbar icon means
the extension cannot reach the native host; its tooltip gives Chrome's reason.

### Uninstall

```bash
ca browser install --remove
```

This removes the host registrations, launcher and extension copy that install
wrote. Manifests it did not write are kept. Then remove the extension card in
`chrome://extensions`.

### Packaged copy

```bash
ca browser package --output dist
```

This writes `computer-artist-chrome-extension-VERSION.zip` byte for byte the same
way every time and reports its SHA-256. Unzip it and use **Load unpacked**. Chrome
installs packaged extensions from outside the Web Store only through enterprise
policy. The extension is not published to the Web Store. The browser still needs
`ca browser install` for the native host.

## Use

```bash
ca browser instances                          # IDs, labels, windows, tab and claim counts
ca browser label work --instance 1a2b3c4d     # name an instance for later --instance work
ca browser tabs --instance work               # handles INSTANCE:TAB, active state, claims
ca browser open http://localhost:5173 --instance work --owner agent-a
#   -> {"handle": "1a2b3c4d:812", "lease": "...", "active": false, ...}
ca browser locate --tab 1a2b3c4d:812 --role textbox
ca browser type  --tab 1a2b3c4d:812 --lease LEASE --ref k3x9q2-1 "hello"
ca browser click --tab 1a2b3c4d:812 --lease LEASE --text "Save"
ca browser wait  --tab 1a2b3c4d:812 --text "Saved"
ca browser read  --tab 1a2b3c4d:812 --selector main
ca browser navigate --tab 1a2b3c4d:812 --lease LEASE --reload
ca browser close --tab 1a2b3c4d:812 --lease LEASE
```

Targets are always explicit. A tab handle carries its instance. With several
instances connected, a bare tab ID or `instances`-wide command without
`--instance` is refused (`ambiguous_instance`) rather than guessed. Elements are
chosen by `--ref` (from `locate`), `--selector`, `--text` (label or visible text)
or `--role` with `--name`. Several matches are refused with candidates unless you
pass `--first`. Refs belong to one page load and are refused as `stale_element`
after navigation or removal.

Every reply is JSON. Refusals use `{"ok": false, "error": CODE, "detail": ...}`
and exit with status 1.

### Native selects

`ca browser locate --tab H --selector select` lists exact option values, labels,
selected state and disabled state. Use `ca browser select --tab H --lease L
--selector '#colour' blue` to choose one exact value in a native single select.
This explicit DOM control sets the selection without opening a native popup and
emits bubbling `input` then `change`, only if the selection changes. It uses the
same lease, pen and human-interruption guard as other actions. Missing/ambiguous
values, disabled options/optgroups/controls and multiple selects are refused.
Events remain untrusted; trusted-gesture requirements are unchanged.

### Focusing a tab

`ca browser focus --tab H --lease L` activates a claimed tab and focuses its
window, restoring it if minimized. It is the only command that switches tabs or
focuses a window, and it takes keyboard focus from whatever the user was doing.
Use it only for visible testing or when the user's attention is wanted, never for
background work. Activation keeps every lease, like the user switching tabs.

### Tab claims

A tab must be claimed before ca changes it: `open` claims the new tab, and `claim`
takes an existing one. The claim returns a **lease** that `navigate`, `click`,
`type`, `select`, `press`, `focus`, `release` and `close` require. Reads (`tabs`, `read`, `locate`,
`wait`, `screenshot`) need only the tab handle. A claimed tab shows a teal **CA**
badge on the extension icon. Tabs that ca opened also sit in a green **CA** tab group.

| Lease ends | `stop_reason` |
| --- | --- |
| You click, press a key, scroll or touch inside the tab | `human_input` |
| The tab closes or Chrome replaces it | `target_closed`, `target_replaced` |
| `ca browser release` | none, the claim is simply gone |

Tab activation and browser window focus are observation: they preserve the watched
claim and all other tabs. Trusted page input revokes only the tab receiving it.
Hover and page focus events do not count as takeover.

Input while the agent's pen is still moving to its target also counts: the action
is refused with `ownership_revoked` (`human_input`) instead of performed.

A revoked lease is refused with `ownership_revoked` and its stop reason. A
different lease is refused with `lease_mismatch`, and a second claim with
`tab_claimed`. `ca browser status --tab H --lease L` reports which of these
applies. ca only closes tabs it opened. Claims live in the extension's session
storage: they survive the native host restarting and end when the browser exits.

Human input is detected from trusted page events by `watch.js`. This small content
script runs at document start in every frame of every page and sends only the
event type to the extension. It is injected into already-open documents when a tab
is claimed. The extension cannot see clicks in the browser's own toolbar or
address bar, so typing a URL into a claimed tab's
address bar is not detected. Tab activation alone is allowed; browser-toolbar
input is outside this page-input guard.

### What you see

When you watch a tab an agent is working in, its pen moves through the page. This
is the native ink pen from computer use, drawn in teal Chrome ink with the owner's
name beside it, so you can tell browser control from computer use at a glance. The
blue pen is computer use. The teal pen and its name tag mean Chrome DOM control.
The pen glides to each target before ca clicks, types or presses a key, splashes
on clicks, writes along fields and drips at rest. When the claim ends, it shows
**Back to you** and lifts away. In a background tab ca acts without the glide. If
you switch to that tab, its agent keeps working and you can watch the pen. The pen is drawn above the page's dialogs, ignores the pointer and holds no
page text, so `read`, `locate` and the obscured-click check do not see it. With
reduced motion turned on, it moves without animation.

![The teal Chrome pen splashing on a click, with its owner tag](evidence/chrome-extension/pen-click.png)

Click the toolbar icon for the status card. It shows whether the extension
reaches ca, with Chrome's reason and the fix when it does not. It also shows
who controls the tab you are looking at, every tab an agent holds now with its
latest action, and why recent claims ended, for example **Your input in "Example Domain"**. The card only reads state the extension already keeps; it
never acts on tabs. Copy the instance ID from its footer for `--instance`.

![The status card in light and dark mode](evidence/chrome-extension/status-card.png)

## Limits

- **Input is untrusted DOM events.** Page scripts see `isTrusted: false`. Sites
  that require trusted gestures, such as popups, clipboard access, fullscreen or
  file pickers, refuse them. Keys other than Enter (submit or activate) and
  Backspace/Delete in text fields have no default action. ca does not use the
  `debugger` permission, so there is no "being debugged" banner and no trusted
  input.
- **Screenshots need the active tab of a visible window.** `chrome.tabs.captureVisibleTab`
  cannot capture background tabs and ca never switches tabs by itself, so
  `ca browser screenshot` on a background tab is refused as `tab_not_visible`.
  Use `read` and `locate` there, or `focus` the tab when the user should see it.
- **Scriptable pages only.** `chrome://` pages, the Web Store, other extensions'
  pages and, unless you allow file access on the extension card, `file://` URLs
  cannot be claimed or read. Only the top frame is read; iframes are not.
- **Branded Chrome 137 and later** ignore the `--load-extension` command-line flag.
  Load the extension through the extensions page as described above.
- Requests are capped at about 900 KB (Chrome's 1 MB native-message limit), typed
  text at 100,000 characters and replies at 64 MiB.

## How it works

```
ca browser ──(same-user socket, mutual HMAC)──> native host ──(stdio)──> extension service worker ──> tab
```

- **Extension** (Manifest V3, `computer_artist/chrome_extension`). Its permissions
  are `nativeMessaging`, `tabs`, `scripting`, `storage`, `alarms` and `tabGroups`,
  with host access `<all_urls>` and the `watch.js` content script. `pen.js` is
  injected only into the top frame of a claimed tab before an action. The popup
  reads bridge and claim state from session storage. The service worker connects to the native host at
  startup, and that open port keeps it running. It reconnects with backoff after a
  disconnect and checks every minute from an alarm.
- **Native host** (`computer_artist/browser_host.py`). Chrome starts one per
  connected profile. The host binds an endpoint and publishes it as
  `INSTANCE.json` in the rendezvous folder: `$XDG_RUNTIME_DIR/computer-artist/browsers`
  on Linux, otherwise `~/.local/share/computer-artist/browsers`. When Chrome
  closes the port, the host removes its record and exits.
- **Endpoint and authentication.** Linux and macOS use a Unix socket in the
  owner-only (0700) rendezvous folder. Windows uses loopback TCP, because CPython
  has no `AF_UNIX` there. Both ends prove the per-user 32-byte key in `key`
  (mode 0600) with a mutual HMAC challenge before any request. The key never
  crosses the connection. No port is reachable from other machines, and web pages
  cannot speak the protocol.
- **Identity.** The extension stores a random 8-character instance ID and your
  label in `chrome.storage.local`. They persist across browser restarts.
  Unreachable records are pruned during discovery.

The bridge is independent of any other browser agent. It does not use
ChatGPT/Codex browser-access credentials, Chrome remote debugging, or a TCP port
open to the network.

## Platforms

| Platform | Status |
| --- | --- |
| Linux (Fedora 43, GNOME Wayland) | Verified on 3 November 2026 with Chrome for Testing 153, headless and headed in a private KWin desktop. See the [validation record](validation/2026-11-03-chrome-extension.md). |
| macOS | Unix socket, `~/Library/Application Support/<browser>/NativeMessagingHosts`. The pen, status card and takeover were verified on 3 November 2026 with headless Chrome for Testing 153 on macOS 27; see the [pen and status card record](validation/2026-11-03-chrome-pen-status.md). |
| Windows | Implemented (loopback TCP plus key, `HKCU\Software\<vendor>\NativeMessagingHosts`) for Chrome, Chromium, Edge and Brave; covered by portable tests, not exercised with a real browser |

The folder names for Chrome Beta, Dev and Canary, Brave, Edge and Vivaldi follow
each vendor's convention but have not been tested. On Windows, Vivaldi and the
Chrome channels other than stable are not registered.
