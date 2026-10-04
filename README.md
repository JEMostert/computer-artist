# Computer Artist

`ca` gives an agent its own animated pointer and physical keyboard input in supported
native Wayland applications on your KDE desktop. You can keep using your pointer
and keyboard in another application. It is a local CLI backed by a KWin plugin.

Keep your pointer and keyboard focus outside the target application's connection.
Returning to it revokes agent control. `--host` explicitly selects the real desktop
pointer, focus and clipboard. The agent lane never falls back to host input.
XWayland, independent clipboard/IME and popup grabs are unsupported.

For development and testing, give each agent a private environment instead: its own
KWin desktop, seat, clipboard and session bus, with apps from the host or a rootless
Podman container. Nothing in it touches your pointer, focus or browser:

```bash
ca env start web --image docker.io/library/node:22 --project .   # or --recipe ca-env.toml
ca env exec web --name app --wait-window "My App" -- npm run start
ca --environment web observe --window app
ca --environment web click --window app --x 120 --y 80
ca env stop web
```

See [private environments](skills/computer-artist/references/environments.md) for
recipes, browsers, profile copies and limits.

Install the CLI and build/load the matching plugin using [installation](docs/INSTALL.md):

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate
ca doctor --build
ca capabilities
ca windows
ca set WINDOW_ID --name paint
ca observe --window paint
ca click --window paint --x 200 --y 150
ca watch --window paint --duration 10 --changes-only
ca session close
```

Coordinates are logical pixels relative to window content. Inspect the captured
image before input. A cursor session starts on acquisition and stays visible
between commands until closed. The agent cursor is separate from your own cursor.

Programs define `run(ctx)` and arrive on stdin. They run in a supervised worker
with a hard deadline, shared request budget and input cleanup:

```bash
ca execute --window paint --deadline 15 --budget 1000 <<'PY'
def run(ctx):
    ctx.path([(100, 100), (180, 140), (240, 100)], interval=.03)
    return ctx.observe()
PY
```

Use `ca fragments create NAME < code.py` and `ca fragments run NAME --window paint`
for reusable, typed, versioned procedures. See the [CLI programs](docs/PROGRAMS.md)
for guarded regions, conditions, gestures and file verification.

An input reply means dispatched. A returned program is unverified until its own
named checks pass. A child fragment's checks do not verify its parent task.
Inspect or stop work from the terminal:

```bash
ca runs list
ca runs inspect RUN_ID
ca runs stop RUN_ID
ca storage keep RUN_ID
```

`window/layout/` holds maps, `window/api-fragmants/` holds reusable code, and
`output/` holds bounded evidence. Five eligible runs are retained by default;
active and preserved runs are protected. Save final documents outside rotating
output. Programs have your user permissions; input separation is not a sandbox.

`ca setup --codex` links the bundled agent skill. It does not update the plugin.
Read [capabilities](docs/CAPABILITIES.md), [validation](docs/VALIDATION.md),
[code structure](docs/CODE-STRUCTURE.md), [protocol](docs/PROTOCOL.md) or
[contributing](CONTRIBUTING.md). [GPL-2.0-or-later](LICENSE).
