# macOS

Read this only on macOS. The rules in SKILL.md apply as well.

macOS has no native desktop backend: `ca` cannot see or drive Mac applications or
the user's own desktop, and desktop commands without `--environment` refuse. All
work happens in private environments, each a Linux box with its own KWin desktop
running in Docker Desktop (or Podman). They test Linux and web applications; Mac
applications cannot run inside them.

```bash
ca doctor                                      # container_runtime must pass
ca env start TASK --project .                  # first start builds the box image
ca env exec TASK --name app --wait-window TITLE -- program args
ca --environment TASK observe --window app
ca env stop TASK
```

If `ca doctor` or `ca env start` reports that no runtime is installed or that the
Docker daemon is not running, stop and tell the user (see SKILL.md). On Apple
silicon the box runs under amd64 emulation: the first build takes several minutes
and apps start more slowly than on Linux. Give `--wait-window` apps a generous
`--timeout`. Commands, paths and recipes are in
[Linux boxes](environments.md#linux-boxes-windows-and-macos); observation,
input, programs and verification work as in the [Linux guide](linux.md) with
`--environment TASK` on every command.
