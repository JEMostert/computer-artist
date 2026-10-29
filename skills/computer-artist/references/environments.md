# Private environments

On Linux, `ca env` runs one systemd user service per environment: a private KWin
with the matching plugin, a private D-Bus session bus and a small manager. With an
image it also runs one container. The container receives only the desktop's
Wayland/bus sockets, its HOME and browser profiles, and the declared mounts, all at
their host paths. The CLI never routes a stopped or missing environment to the
user's desktop. On Windows and macOS every environment is a Linux box instead; see
[Linux boxes](#linux-boxes-windows-and-macos).

## Container runtime

The runtime depends on the operating system. Windows and macOS use Docker Desktop
only; selecting Podman there is refused. On Linux, Podman and Docker both work: the
choice is `--runtime podman|docker` (stored with the definition), else
`CA_CONTAINER_RUNTIME`, else Podman when installed, else Docker. Podman runs
rootless with `--userns=keep-id`; rootless Docker maps container root to you;
rootful Docker runs the container as your uid. Membership of the `docker` group is
equivalent to root on the host; prefer rootless Podman or rootless Docker.
`ca env doctor NAME` reports the runtime and whether its daemon answers. If no
usable runtime is installed, `ca env start` says so: tell the user which runtime
this operating system needs, and stop.

## Recipes (Linux host/container backend)

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
With `--host`, or the environment's own default lane, `ca type` pastes the whole text
at once and restores the previous plain-text clipboard.

## Applications and evidence

- `--wait-window [TITLE]` waits for a new window; `--name` binds it for `--window`.
- Without a window wait, exec reports whether the process is alive after a second.
- `--wait` runs a command to completion and returns its status and output tail.
- `ca env logs NAME` lists `kwin`, `build`, `container` and this session's `a1`, `a2`…
- Names and layout maps are per environment; fragments and workflow memories are
  shared with your main workspace. Run evidence goes to the environment's output
  folder. Keep `--environment NAME` on `memory learn` to select the correct run
  output; removing an environment leaves the shared library intact.

## Browsers

`ca env browser NAME --family chromium|firefox --executable EXE` keeps a persistent
private profile per executable (`--profile NAME` for more). Container browsers
must exist in the image; Chromium in a container may need `-- --no-sandbox`.
Sign in once; the profile persists.

## Accessibility

Environments start the AT-SPI registry and set `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`
for their apps (`status.accessibility`). `ca --environment TASK a11y --window APP`
then lists roles, names, states, text and window-content centers from the private
bus; programs use `ctx.find`/`ctx.click(element=...)`. Verified for a Qt6 app on
11 October 2026 and in a macOS Linux box on 15 October (see the
[platform evidence](../../../docs/validation/2026-10-15-macos.json)); GTK and
browser accessibility trees remain unverified. Remove reports
`running: null` when the manager does not answer and refuses to delete until the
environment is known to be stopped.

## Limits

Each desktop service measured about 230 MiB and a small container about 40 MB;
four ran concurrently with verified drawing. There is no audio server or
microphone. XWayland windows display and capture but accept no input. Portals,
notifications and secrets are bus-activated host services inside the private
session. `ca env doctor NAME` checks tools, image, mounts, GPU, services and apps.
`ca env remove NAME --yes` deletes a stopped environment's data and built image.

## Linux boxes (Windows and macOS)

There each environment is one Docker Desktop container running the private KWin
desktop, its D-Bus session and the manager described above. The box image is built
from this checkout on first start (several minutes once, then cached; rebuilt when
the sources change). On Apple silicon it runs under amd64 emulation.

```bash
ca env start TASK --project .                 # project appears in the box at /project
ca env exec TASK --name app --wait-window TITLE -- program args
ca env exec TASK --wait -- npm test
ca env browser TASK --family firefox --executable firefox --name web
ca --environment TASK windows                  # every ca command takes --environment
ca --environment TASK observe --window app
ca --environment TASK type --window app 'Hello'
ca env status TASK                             # "paths" maps box paths to host paths
ca env stop TASK
```

Commands run inside the box. Paths in arguments (`capture OUTPUT`, `--trace`,
`--path-file`, `files PATH`) must lie under the project, a mount or the
environment folder; replies show host paths again. Inside `ca execute` programs
use box paths (`/project/...`). The box contains KWrite, Konsole and Firefox;
install extra apps with a recipe `containerfile` that starts with `ARG BOX_IMAGE`
and `FROM ${BOX_IMAGE}`. Plain `--image`, `--gpu` and `exec --on-host` are not
supported in a box. Default lane, names, layouts, fragments and evidence behave as
above, including the shared workflow-memory library. The Linux recipe example
above needs adapting for boxes: omit `image` and `gpu`, use `/project` inside app
commands, and extend the base image through `ARG BOX_IMAGE` / `FROM ${BOX_IMAGE}`.
