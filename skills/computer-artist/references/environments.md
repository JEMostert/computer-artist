# Private environments

`ca env` runs one systemd user service per environment: a private KWin with the
matching plugin, a private D-Bus session bus and a small manager. With an image it
also runs one rootless Podman container (`--userns=keep-id`, `--init`). The
container receives only the desktop's Wayland/bus sockets, its HOME and browser
profiles, and the declared mounts, all at their host paths. The CLI never routes
a stopped or missing environment to the user's desktop.

## Recipes

A TOML (or JSON) recipe is the reusable definition. Relative paths follow the file.

```toml
containerfile = "Containerfile"     # or: image = "docker.io/library/node:22"
project = "."                       # read-write at the same path; working directory
mounts = ["../assets:ro", "/data/fixtures:/fixtures:ro"]
cpus = 4
memory = "4g"
network = "private"                 # private, host (reach host dev servers) or none
gpu = false                         # /dev/dri, plus NVIDIA only with a CDI spec
xwayland = false
width = 1600
height = 1000

[env]
ELECTRON_DISABLE_SANDBOX = "1"

[[apps]]
command = ["npm", "run", "dev"]
wait_window = "My App"              # or true for any new window; omit for services
name = "app"
timeout = 90
```

`ca env start NAME --recipe FILE` replaces the definition with the recipe, builds
(cached) and launches the apps, failing with their log tail when one dies. Flags
(`--memory 2g`, `--mount SRC`) update a stopped definition; `ca env create` stores
one without starting. Images need a `sleep` executable. `--pull` refreshes bases.

## Applications and evidence

- `--wait-window [TITLE]` waits for a new window; `--name` binds it for `--window`.
- Without a window wait, exec reports whether the process is alive after a second.
- `--wait` runs a command to completion and returns its status and output tail.
- `ca env logs NAME` lists `kwin`, `build`, `podman` and this session's `a1`, `a2`…
- Names and layout maps are per environment; fragments are shared with your main
  workspace. Run evidence goes to the environment's output folder.

## Browsers

`ca env browser NAME --family chromium|firefox --executable EXE` keeps a persistent
private profile per executable (`--profile NAME` for more). Container browsers
must exist in the image; Chromium in a container may need `-- --no-sandbox`.
`--copy-profile default|PATH` seeds a new profile from a closed browser profile
(caches skipped) and refuses one in use. Wallet-encrypted logins and cookies do not
decrypt in a private session: Chromium-family browsers may ask to continue with
data loss, which only affects the copy; sign in once and the profile persists.

## Accessibility

Environments start the AT-SPI registry and set `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`
for their apps (`status.accessibility`). `ca --environment TASK a11y --window APP`
then lists roles, names, states, text and window-content centers from the private
bus; programs use `ctx.find`/`ctx.click(element=...)`. Verified for a Qt6 app on
11 October 2026; GTK, browsers and container apps are untested. Remove reports
`running: null` when the manager does not answer and refuses to delete until the
environment is known to be stopped.

## Limits

Each desktop service measured about 230 MiB and a small container about 40 MB;
four ran concurrently with verified drawing. There is no audio server or
microphone. XWayland windows display and capture but accept no input. Portals,
notifications and secrets are bus-activated host services inside the private
session. `ca env doctor NAME` checks tools, image, mounts, GPU, services and apps.
`ca env remove NAME --yes` deletes a stopped environment's data and built image.
