# Installation

Requires Python 3.12+, Pillow, jeepney, KDE Wayland and matching installed KWin
development headers. Accessibility reading needs `at-spi2-core`. Installing Python
does not deploy the plugin.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e .
source .venv/bin/activate
ca doctor --build
./scripts/build-plugin.sh
```

Arch/CachyOS build packages: `kwin`, `qt6-base`, `qt6-tools`, `extra-cmake-modules`,
`cmake`, `ninja`, `gcc`, `pkgconf`, `wayland`, `wayland-protocols`,
`plasma-wayland-protocols`. Other distributions may package KWin headers separately.
Integration also uses PySide6 (system package), KWrite, qdbus6, dbus-run-session,
setpriv and at-spi2-core; `scripts/test.sh --integration` creates
`build/integration-venv` with system site packages for it.

First host installation, when explicitly requested:

```bash
sudo install -m 755 build/plugin/kwin/plugins/computerartist.so /usr/lib/qt6/plugins/kwin/plugins/computerartist.so
qdbus6 org.kde.KWin /Plugins org.kde.KWin.Plugins.LoadPlugin computerartist
ca capabilities
```

`ca doctor` compares the loaded plugin's source hash and KWin versions with this
checkout and reports a stale or mismatched build. Never overwrite a loaded library.
Stop input and unload its actual plugin ID before replacement. KWin can retain mappings after unload; applying new code in
the same desktop process may require a versioned filename/plugin ID. Rebuild after
KWin upgrades. Metadata defaults to disabled; `computerartistEnabled=true` in
kwinrc's `[Plugins]` group enables loading on later logins.

Socket: `$XDG_RUNTIME_DIR/computer-artist/control`, overridden by `--socket` or
`CA_SOCKET`. `ca doctor` checks both lanes without input or sessions.
`ca setup --codex` links the skill into `~/.agents/skills`; `--skills-dir PATH`
selects another location. Keep the linked source in place.

A checkout uses `window/` and `output/`. Wheels use
`$XDG_DATA_HOME/computer-artist/window` and `$XDG_STATE_HOME/computer-artist/output`
(defaults under `~/.local/share` and `~/.local/state`). Override with
`--window-dir`/`CA_WINDOW_DIR` and `--output-dir`/`CA_OUTPUT_DIR`. Back up the window root.
Build with `pip wheel . --wheel-dir dist`; offline install uses
`pip install --no-index --find-links dist computer-artist` with dependencies present.
