# The user's desktop on Linux (KDE Wayland)

Use this only when the user explicitly asked you to work on their own desktop;
otherwise use a [private environment](environments.md). The agent gets its own
pointer and keyboard lane in the target app while the user keeps theirs. It needs
the loaded KWin plugin, which skill setup does not deploy or update.

## Steps

1. **Start.** Run `ca capabilities`, `ca windows` and `ca doctor --window ID`. Bind
   the exact window with `ca set ID --name app`.
2. **Work.** Search `ca memory search "TASK"` first and reuse a fitting memory
   ([workflow memory](memory.md)). Then observe, act and observe again:
   - `ca observe --window app`, and read the image.
   - `ca a11y --window app` lists controls by role and name; prefer them over pixel
     positions (`ctx.find`, `ctx.click(element=...)`).
   - Act with `click`, `type` (agent text path; `write` types layout-resolved keys),
     `press`, `drag`, or a `ca execute` program for anything with several steps
     ([programs](programs.md)). Coordinates are logical content pixels; scale
     screenshot pixels as in [coordinates](operating-tips.md).
3. **Verify.** Read text back, check saved files with `ctx.verify_file`, and look
   at a fresh `observe`. A dispatched action is not proof.
4. **Finish.** Learn a reusable workflow with `ca memory learn NAME --run RUN_ID
   --task "TASK" --app "APP" --notes "PROCEDURE"`. Keep evidence you need with
   `ca storage keep RUN_ID`. Then `ca session close`.

## If something goes wrong

- **A refusal:** report it. Never retry it with `--host` or host focus/pointer
  recovery unless the user authorized that in this task.
- **The user takes over** (their pointer or focus enters the target): input stops.
  Inspect the current state before you retry anything.
- **`plugin_build` in doctor:** the loaded plugin is stale. `ca update --check`
  shows the fix; `ca update` reloads KWin's plugin with sudo, so ask the user first.
- **The window was reopened or `--title` matches several windows:** bind the exact
  new ID again with `ca set`.
- **A guarded target is refused:** take a fresh observation and revalidate it
  (`ca target app NAME --observation OBS --revalidate`); never copy its old
  coordinates around the guard.
- **An XWayland window:** observe only; it cannot be driven. Report native Wayland
  and XWayland results separately.
- **A dialog took the user's focus:** apps can do that; say so rather than promise
  it cannot happen.
- **A run must stop:** `ca runs stop ID`, then inspect `ca runs inspect ID` before
  retrying. Checkpoints can miss recent actions and never prove completion.

## Details

- Stable visual controls can be mapped once with `ca target app NAME --observation
  OBS --rect X Y W H` and clicked with `ctx.click(target='@NAME')`. Changing canvases
  need fresh geometry instead.
- Independent keys need advertised keyboard support and a ready target: use
  `ctx.press`, `key_down`/`key_up` and `ctx.write`. Unicode paste uses host focus
  and clipboard, so it needs the user's authorization.
- `ca watch` gives bounded JSONL observation; `ctx.handoff(reason)` releases or
  yields; `ctx.related_windows()` lists dialogs of the same process.
- Menus opened by the agent lane were selectable in the tested Qt fixture only; check
  each app's menus and dialogs live.
- Five eligible runs rotate; keep what you need with `ca storage keep`.
