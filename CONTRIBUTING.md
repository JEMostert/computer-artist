# Contributing

Start with [FEATURES.md](FEATURES.md) and [AGENTS.md](AGENTS.md). The goal is
independent agent input in existing applications on the user's actual desktop.

## Local checks

```bash
python -m pip install Pillow
./scripts/test.sh
```

Python tests cover the client, CLI, fragments, and supervised runtime. GitHub Actions
runs these checks without a compositor. For changes to input delivery, ownership,
or the plugin, also run the isolated stock-KWin integration harness:

```bash
./scripts/test.sh --integration
```

See [plugin setup](plugin/README.md) for build and test dependencies. Keep compositor
experiments separate from the running host. Never overwrite a loaded plugin file.
Do not silently substitute host input when the agent lane rejects an action.

## Changes and reports

Keep changes focused. Describe the triggering behavior, what changes, and the
checks performed. Distinguish implemented behavior from behavior verified in a
specific application. Native Wayland and XWayland are separate compatibility cases.

For input bugs, include KWin/Qt versions, application/backend, display scaling,
the command or minimal program, expected behavior, and observed behavior. Share
only the relevant, redacted trace or capture; local layouts, fragments, and run artifacts can
contain private desktop content.

Build products, captures, local layouts and API fragments, and execution records
stay ignored. Documentation artwork intended for publication belongs in
`docs/assets/`.
