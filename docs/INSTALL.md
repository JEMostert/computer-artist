# Installation

For native Windows support, follow [Windows installation](WINDOWS.md). The KWin
build and session instructions below apply to Linux only.

Requires Python 3.12+, Pillow, jeepney, KDE Wayland and matching installed KWin
development headers. Accessibility reading needs `at-spi2-core`. Installing Python
does not deploy the plugin. Container environments (`ca env --image`) need Podman
(rootless, preferred) or Docker; `ca env doctor NAME` checks the runtime.

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

## Updating

From a checkout, `ca update` does the steps above safely:

1. `git pull --ff-only` (refuses uncommitted changes; `--no-pull` skips it), then
   reinstalls dependencies when `pyproject.toml` changed (only inside a virtual
   environment; a system interpreter gets a pip hint instead) and continues with
   the pulled code.
2. Rebuilds the plugin when the loaded build or the login copy differs from the
   checkout's source hash.
3. Installs `computerartist-HASH.so` with `sudo install` (a new inode), unloads the
   running plugin and loads the versioned copy; if KWin refuses, the old plugin is
   loaded again. It refuses while a session holds an input lane unless `--force`.
   `--no-reload` leaves the running plugin alone.
4. Replaces `computerartist.so` for later logins, deletes unloaded old versioned
   copies and runs `ca doctor`.

`ca update --check` fetches and reports the planned actions without changing
anything. Set `CA_KWIN_PLUGIN_DIR` if KWin plugins live outside `/usr/lib*/qt6`.
A crash in a newly loaded plugin takes KWin down with it; run `scripts/test.sh
--integration` first when plugin sources changed.

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
