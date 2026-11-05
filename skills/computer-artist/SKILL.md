---
name: computer-artist
description: Use the ca CLI to work in tabs of the user's running Chrome, in private desktop environments for testing apps, or, only when explicitly asked, on the user's own Linux (KDE Wayland), macOS or Windows desktop.
---

# Computer Artist

Use `ca` from PATH or this skill's checkout. Choose your working environment, read
that one file, and follow its steps in order:

1. **A web page:** a tab in the user's running Chrome, on any operating system.
   Read [Chrome tabs](references/browser.md).
2. **A desktop app, or a browser kept apart from the user's:** a private
   environment. Read [private environments](references/environments.md).
3. **The user's own desktop, only when they explicitly ask for it.** Check the
   operating system (`uname -s` prints `Linux` or `Darwin`; on Windows `$env:OS` is
   `Windows_NT`) and read [Linux](references/linux.md), [macOS](references/macos.md)
   or [Windows](references/windows.md). On any other system, tell the user it is
   unsupported and stop.

## Rules everywhere

- Never move the user's pointer, take their focus or use their clipboard unless
  they authorized it in this task (`--host`, `--host-drags`). Never use host input
  to get around a refusal, and never fall back to the user's desktop when an
  environment fails; report the blocker.
- A dispatched action or a passing child check is not proof. Read the result back
  or look at a fresh observation before you call the work done.
- Stop what you started, and never touch another agent's tabs or environments.
  Input separation is not a security sandbox.

## Several agents at once

Each agent has its own owner or environment name, its own tabs or environment, its
own output files, and its own worktree when it edits a repository. Only the
coordinator signs in or out, impersonates users or changes account-wide settings;
agents ask the coordinator for those.

## Shared references

Read these when a step points to them: [programs](references/programs.md) for
`ca execute` and larger work such as artwork, [workflow memory](references/memory.md),
[coordinates and recovery](references/operating-tips.md), and [KolourPaint
techniques](references/kolourpaint.md) before painting in KolourPaint.
