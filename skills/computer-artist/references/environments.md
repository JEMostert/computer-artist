# Private environments (`ca env`)

A private environment is a desktop of your own: its own KWin, seat, clipboard and
session bus, optionally with the app in a container. Nobody else uses it, so the
default input lane there needs no care for a human pointer. On Windows and macOS
each environment is a Linux box in Docker Desktop: it runs Linux and web apps, not
Mac or Windows apps.

## Steps

1. **Start.** Pick a name that is yours alone (your task or owner name), then run
   `ca env start NAME --project .`. Add `--recipe FILE` or `--image IMAGE` when the
   app needs them (see [recipes](#recipes)). The first start of a box builds its
   image, which takes several minutes once.
2. **Launch the app.** `ca env exec NAME --name app --wait-window TITLE -- program args`,
   or a browser: `ca env browser NAME --family chromium --executable chromium --name web`
   (`--family firefox --executable firefox` in a box). For a one-off command use
   `ca env exec NAME --wait -- npm test`.
3. **Work.** Put `--environment NAME` on every `ca` command. Search
   `ca memory search "TASK" --environment NAME` first and reuse a fitting memory
   ([workflow memory](memory.md)). Then observe, act and observe again:
   - `ca --environment NAME observe --window app`, and read the image.
   - `ca --environment NAME a11y --window app` lists controls by role and name;
     prefer them over pixel positions.
   - Act with `click`, `type` (whole text at once; `write` types key by key when
     an app rejects that), `press`, `drag`, or a `ca execute` program for anything
     with several steps ([programs](programs.md)). Coordinates are logical window
     pixels; scale screenshot pixels as in [coordinates](operating-tips.md).
4. **Verify.** Read the text back, check saved files with `ctx.verify_file`, and
   look at a fresh `observe`. A dispatched action is not proof.
5. **Finish.** If the workflow is worth reusing, learn it:
   `ca memory learn NAME --run RUN_ID --task "TASK" --app "APP" --notes "PROCEDURE" --environment NAME`.
   Then `ca env stop NAME`. Evidence and the definition are kept.

## If something goes wrong

- **No container runtime, or its daemon is not running:** stop and tell the user:
  "Private test environments need Docker Desktop (Windows: with WSL 2)", or on
  Linux "need Podman or Docker". Never fall back to the user's desktop.
- **The app dies or never shows a window:** `exec` reports `ok: false` with the
  exit status and log tail. Read `ca env logs NAME a1` (also `kwin`, `build`,
  `container`) and `ca env doctor NAME`.
- **A refusal:** never drop `--environment` to get past it. Report it instead.
- **A window is XWayland:** you can observe and capture it, not drive it.
- **A name is taken or the environment belongs to someone else:** use another
  name. Never stop or remove another agent's environment. `ca env remove NAME --yes`
  deletes only your own stopped environment and its data.

## Recipes

A TOML (or JSON) recipe stores the definition; relative paths follow the file.
`ca env start NAME --recipe FILE` replaces the definition, builds (cached) and
launches the apps, failing with their log tail when one dies.

```toml
containerfile = "Containerfile"     # or: image = "docker.io/library/node:22"
project = "."                       # read-write at the same path; working directory
mounts = ["../assets:ro", "/data/fixtures:/fixtures:ro"]
network = "private"                 # private, host (reach host dev servers) or none
memory = "4g"

[[apps]]
command = ["npm", "run", "dev"]
wait_window = "My App"              # or true for any new window; omit for services
name = "app"
```

Images need a `sleep` executable. Flags such as `--memory 2g` or `--mount SRC`
update a stopped definition; `ca env show NAME` prints it.

In a box, omit `image` and `gpu`, and extend the box image instead: start the
`containerfile` with `ARG BOX_IMAGE` and `FROM ${BOX_IMAGE}`. The project appears at
`/project`; use that path inside app commands and `ca execute` programs. Paths you
pass to `ca` (capture outputs, `--path-file`, `files PATH`) must lie under the
project, a mount or the environment folder; `ca env status NAME` maps box paths to
host paths. The box contains KWrite, Konsole and Firefox.

On Linux without an image, apps run from the host inside the private desktop.
Container apps see mounts at their host paths; their HOME is `status.home`, so write
evidence there or into the project. See [environments](../../../docs/ENVIRONMENTS.md)
for the runtime choice, accessibility and resource limits.
