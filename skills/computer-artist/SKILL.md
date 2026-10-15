---
name: computer-artist
description: Use the ca CLI to test and interact with GUI applications on Linux (KDE Wayland), Windows or macOS, preferring private agent environments; control the user's actual desktop only when explicitly requested.
---

# Computer Artist

Use `ca` from PATH or this skill's checkout. The CLI is the interface; Python
`run(ctx)` programs are submitted through it, not a separate SDK.

## 1. Detect the operating system, then follow only its guide

Determine where you run before any `ca` command: `uname -s` printing `Linux` or
`Darwin`, or a Windows shell (`$env:OS` is `Windows_NT`, or `ver`). Then read
exactly one guide and follow its steps; never mix them:

- Linux: [Linux (KDE Wayland)](references/linux.md)
- Windows: [Windows](references/windows.md)
- macOS: [macOS](references/macos.md) (native desktop and Docker environments)
- Anything else: Computer Artist does not support it yet. Tell the user and stop.

## 2. Rules on every platform

- Prefer a private environment for development and testing (`ca env`). Use the
  user's actual desktop only when the user explicitly asks for it.
- Container environments need a container runtime, chosen by operating system:
  Linux uses Podman or Docker; Windows and macOS use Docker Desktop only (every
  environment there is a container). When `ca env start` reports that the runtime
  is missing or not running, stop and tell the user: "Private test environments
  need Docker Desktop (Windows: with WSL 2)" or, on Linux, "need Podman or Docker".
  Never fall back to the user's desktop instead.
- The agent pointer is an animated ink pen; its nib tip is the click point. The pen
  and its ink trail are drawn only on screen and never reach the application. Window
  captures exclude them, unless the reply says `may_include_overlapping_windows: true`.
- Never silently move the human's pointer or steal focus. The default agent lane
  never falls back to host input; do not use `--host` to work around a refusal.
  `--host` needs the user's authorization in the current task, and so does the
  macOS `--host-drags`, which borrows the real cursor for each drag.
- For text use `ca type` / `ctx.type`. With `--host` and inside environments it
  pastes the whole text at once and then restores the previous clipboard; on the
  Windows agent lane it inserts the whole text into the control; only the Linux
  agent lane types keys (it has no private clipboard). Use `ca write` (key by key)
  only when an application rejects paste.
- Dispatch, a screenshot or a child's passing checks do not verify the outer task.
  Read back text, snapshot files before saving and check them with
  `ctx.verify_file`, and inspect a fresh observation.
- Stop what you started (`ca env stop NAME`, `ca session close`); never stop
  another agent's environment. Input separation is not a security sandbox.

Programs, fragments, observation, handoff and evidence work the same everywhere:
see [programs](references/programs.md) and [environments](references/environments.md).
