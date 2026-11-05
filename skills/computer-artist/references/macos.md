# The user's desktop on macOS

Use this only when the user explicitly asked you to work on their own desktop;
otherwise use a [private environment](environments.md), which runs Linux and web
apps (Mac apps cannot run there). The agent gets its own on-screen ink pen and
per-app keys in the target app; nothing falls back to the user's pointer, focus or
clipboard.

## Steps

1. **Start.** Run `ca doctor`; it needs Accessibility, Screen Recording and Input
   Monitoring for the terminal or app running `ca`. Then `ca windows` and bind the
   exact window with `ca set WINDOW_ID --name app`. The target must be on the
   current Space.
2. **Work.** Search `ca memory search "TASK"` first and reuse a fitting memory
   ([workflow memory](memory.md)). Then observe, act and observe again:
   - `ca observe --window app`, and read the image.
   - `ca a11y --window app --role 'push button'` lists controls by role and name;
     prefer them over pixel positions.
   - Act with `click`, `write` (physical keys from the live layout), `press`,
     `scroll`, `drag`, or a `ca execute` program for anything with several steps
     ([programs](programs.md)). Coordinates are logical window-frame points,
     including the title bar; Retina captures have more pixels, so scale them as in
     [coordinates](operating-tips.md).
3. **Verify.** Read text back, check saved files with `ctx.verify_file`, and look
   at a fresh `observe`. A dispatched action is not proof.
4. **Finish.** Learn a reusable workflow with `ca memory learn NAME --run RUN_ID
   --task "TASK" --app "APP" --notes "PROCEDURE"`. Then `ca session close`; until
   then the pen stays at its last position.

## If something goes wrong

- **Permissions missing in `ca doctor`:** ask the user to grant them in System
  Settings > Privacy & Security. ca does not prompt.
- **Input stopped:** a real key in the target app, a click in its foreground
  window, or a drag or scroll over it means the user took over. Hovering does not.
  Inspect before retrying.
- **Another window of the same app is in front:** keys are refused; one lane owns
  each app. Do not edit in the same app as the user, and do not switch Spaces or
  change the user's focus to get past a refusal.
- **A drag leaves only a dot** (Chrome canvases such as Kleki, GTK apps such as
  Pinta): the app reads macOS's single shared button state. Ask the user before
  adding `--host-drags` to that command, for example
  `ca draw --host-drags --window app --path-file shape.txt` (the file holds SVG path
  `d` data only). Each drag then borrows the real cursor and locks out their mouse
  for the stroke, and keys they type just after release can land in the target;
  tell them. The path must stay visible, and their click stops the drag. Clicks
  stay on the agent lane. Apps that track strokes themselves, such as Excalidraw,
  draw without it.
- **Text with dead keys, compose or IME:** refused before input. Host paste
  (`--host`, Cmd+V) needs the user's authorization in this task.
- **The window moved, closed or changed Space:** input stops; observe and bind again.

See [macOS](../../../docs/MACOS.md) for what was verified and the backend design.
