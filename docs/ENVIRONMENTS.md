# Private environments

The agent workflow is in the skill's [private environments](../skills/computer-artist/references/environments.md)
reference. This page explains how environments work and what they cannot do.

## How they run

On Linux, `ca env` runs one systemd user service per environment: a private KWin
with the matching plugin, a private D-Bus session bus and a small manager. With an
image it also runs one container. The container receives only the desktop's
Wayland/bus sockets, its HOME and browser profiles, and the declared mounts, all at
their host paths. The CLI never routes a stopped or missing environment to the
user's desktop.

On Windows and macOS each environment is one Docker Desktop container running the
same private KWin desktop, its D-Bus session and the manager (a Linux box). The box
image is built from this checkout on first start (several minutes once, then
cached; rebuilt when the sources change). On Apple silicon it runs under amd64
emulation. Commands run inside the box; replies show host paths. Plain `--image`,
`--gpu` and `exec --on-host` are not supported in a box.

Inside an environment the default lane is that desktop's own seat, and targets are
raised automatically. There `ready`/`host_acquirable` (doctor: `host_candidate`)
decide readiness; `acquirable`, `agent_restrictions` and `human_active` (grouped
under `agent_lane`) describe the agent lane and do not block.

Names and layout maps are per environment. Fragments and workflow memories are
shared with your main workspace. Run evidence goes to the environment's output
folder, so `ca memory learn` needs `--environment NAME` to find the run; removing
an environment leaves the shared library intact.

## Container runtime

Windows and macOS use Docker Desktop only; selecting Podman there is refused. On
Linux, Podman and Docker both work: the choice is `--runtime podman|docker` (stored
with the definition), else `CA_CONTAINER_RUNTIME`, else Podman when installed, else
Docker. Podman runs rootless with `--userns=keep-id`; rootless Docker maps container
root to you; rootful Docker runs the container as your uid. Membership of the
`docker` group is equivalent to root on the host; prefer rootless Podman or rootless
Docker. `ca env doctor NAME` reports the runtime and whether its daemon answers.

## Recipes in full

Besides the fields in the skill, recipes take `cpus`, `gpu` (`/dev/dri`, plus NVIDIA
only with a CDI spec), `xwayland`, `width`, `height`, an `[env]` table and per-app
`timeout`. `ca env create` stores a definition without starting it, and `--pull`
refreshes base images.

## Browsers

`ca env browser NAME --family chromium|firefox --executable EXE` keeps a persistent
private profile per executable (`--profile NAME` for more). Container browsers must
exist in the image; Chromium in a container may need `-- --no-sandbox`. Sign in
once; the profile persists.

## Accessibility

Environments start the AT-SPI registry and set `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`
for their apps (`status.accessibility`). `ca --environment NAME a11y --window APP`
then lists roles, names, states, text and window-content centres from the private
bus. Verified for a Qt6 app on 11 October 2026 and in a macOS Linux box on 15
October (see the [platform evidence](validation/2026-10-15-macos.json)); GTK and
browser accessibility trees remain unverified.

## Limits

Each desktop service measured about 230 MiB and a small container about 40 MB; four
ran concurrently with verified drawing. There is no audio server or microphone.
XWayland windows display and capture but accept no input. Portals, notifications
and secrets are bus-activated host services inside the private session. Remove
reports `running: null` when the manager does not answer and refuses to delete until
the environment is known to be stopped. Mounted folders stay shared; a private
desktop is not a security sandbox.
