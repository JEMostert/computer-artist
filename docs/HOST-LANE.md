# Host and agent input lanes

`ca` defaults to the independent agent pointer. `--host` explicitly selects the
real desktop pointer and ordinary desktop focus. It is shared with the human,
not another isolated seat. There is no automatic fallback between lanes.

```bash
ca click --window PAINT_ID --x 200 --y 150
ca --host click --window BROWSER_ID --x 300 --y 200
ca --host move --window BROWSER_ID --x 400 --y 250
ca --host scroll --window BROWSER_ID --x 400 --y 250 --delta 40
ca --host focus --window CHAT_ID
ca session status
ca session close
```

Sessions are created automatically on the first successful input acquisition.
Read-only commands do not create them. `session open` has been removed. Sessions
remain open across commands; `session close` releases both lanes and hides the
agent overlay. A host-only task does not display an agent cursor.

`focus` requests activation of the chosen visible window without inventing a
click location. It does not move the pointer. Use a host `move` or `click` as well
when the physical pointer must leave an app before agent acquisition. The focus
request fails if KWin redirects it to another window, such as a modal dialog;
observe and target the actual dialog explicitly.

Both lanes currently support native Wayland toplevels, pointer movement, buttons
and scrolling. The host lane additionally supports `focus`, keyboard input and
text clipboard access. XWayland, popup grabs, constrained pointers and application
DND are unsupported by the host implementation. Popup-triggered automatic
activation remains a known limitation of the agent lane; host focus is an
explicit recovery tool, not prevention of the original focus change.

## Text and keys

```bash
ca --host paste --window EDITOR_ID 'Hello café 😀'
ca --host clipboard set 'Shared clipboard text'
ca --host clipboard get
ca --host key --window EDITOR_ID Ctrl+S
ca --host key --window GAME_ID W --duration 1
```

Prefer clipboard paste for text. `type` is a compatibility alias for `paste`.
Paste focuses the selected window, writes the shared clipboard, then sends
Shift+Insert. This avoids keyboard-layout assumptions for text. Use `--shortcut`
for an application's alternative paste binding. Neither focus nor paste moves
the pointer; select the intended field first. Clipboard read/write alone does
not acquire a window, change focus or open a session.

Clipboard support is UTF-8 text only, bounded to 8192 bytes. It uses the selected
compositor's `ext-data-control-v1` connection, not the shell's display. Writes
persist after CLI disconnect until replaced or the plugin is unloaded. Paste
leaves the text available for asynchronous app reads; previous clipboard data
is not automatically restored. The primary selection is untouched. A successful
paste reply reports dispatch; verify the text in the application.

Key names refer to Linux physical key positions (letter names use US positions).
Their interpretation follows the desktop's active layout. Keyboard operations
require the leased target's real keyboard focus. A focus change, external input,
disconnect, stale action, timeout or session close stops input and releases held
keys. Host shortcuts pass through KWin's ordinary shortcut handling.

## Coordination

The existing plugin's socket server is the session controller. Its event loop
serializes ownership and dispatch; each lane has its own connection, lease,
generation, held buttons and five-second watchdog. No extra daemon is necessary.

One controller owns each lane at a time. The lanes can work concurrently in
**different Wayland connections**. Multiple windows belonging to the same
connection conflict. Conflicts are rejected before input. Agent acquisition still
refuses an app with real pointer or keyboard focus. Host input cannot enter the
agent-owned connection.

Host events pass through KWin's ordinary input pipeline with a distinct input
device identity. An event from another device, including other automation, stops
the host controller. Physical pointer movement, buttons, scrolling, keyboard
activity, touch or tablet proximity therefore take priority. The spy does not
consume the physical event. If the human presses the same button or key already
held by automation, ownership transfers to the human until their release.
Other synthetic held input is released.

Window geometry/lifetime changes, screen lock, pointer constraints, disconnect,
expiry or invalid host actions stop host control. Agent work in another app can
continue after an independent host takeover. A composed parallel program instead
cancels sibling tasks on failure. A fresh explicit host command can reacquire;
there is no automatic retry that fights the user's pointer.

## Parallel Python programs

Launch with `--host` to explicitly enable host access for the program:

```bash
ca --host execute --window BROWSER_ID <<'PY'
def run(ctx):
    browser = ctx.host.window('BROWSER_ID')
    paint = ctx.agent.window('PAINT_ID')

    def draw(c):
        return c.path([(100,200), (150,250), (200,200)])

    def point(c):
        c.move(x=300, y=200)
        return {'positioned': True}

    with ctx.parallel() as tasks:
        drawing = tasks.start(draw, paint)
        positioning = tasks.start(point, browser)
    return [drawing.result(), positioning.result()]
PY
```

Replace both IDs inside Python and the CLI argument. Contexts use fresh geometry,
normal observation/target checks and one shared request budget and deadline.
Parallel tasks on the same lane are serialized for the whole callable; different
lanes can overlap. Sibling tasks cancel on failure. Task completion returns its
app lease. The supervisor's hard deadline covers blocked Python code. Use the
parallel group for concurrency; manually sharing a context across arbitrary
threads is unsupported.

`ctx.host.window(...)` is rejected unless the program was invoked with `--host`.
A module uses its supplied context, never switches implicitly, and can declare
`CONTRACT = {'lane': 'host'}` or `{'lane': 'agent'}`. Omitted lane means either
lane is acceptable; it does not authorize escalation. Nested calls can explicitly
choose a context from the same execution:

```python
ctx.fragments.call('draw-shape', lane=paint, size=20)
```

Traces record the lane. Session status includes lane targets, held-button counts,
host stop reason and real pointer position. `ca stop` stops only the agent lane;
`ca --host stop` stops only the host lane. `session close` stops both. Python is
trusted local code, not a security sandbox.

The host client checks protocol version and explicit lane support before input.
An older plugin that would ignore the lane field is rejected.
