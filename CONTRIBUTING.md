# Contributing

Read [AGENTS.md](AGENTS.md) and [capabilities](docs/CAPABILITIES.md). Keep the CLI
central, reuse `Context` for actions and keep compositor experiments separate
from the running desktop.

```bash
.venv/bin/python -m pip install -e '.[dev]'
PATH="$PWD/.venv/bin:$PATH" ./scripts/check.sh
.venv/bin/python -m unittest discover -s tests -q
./scripts/test.sh --integration
```

Integration uses system Python with PySide6/Pillow, builds the plugin and launches
a separate packaged KWin virtual instance. Evidence uses a fresh temporary folder;
`CA_INTEGRATION_OUTPUT_DIR` overrides it. Tests do not deploy the host plugin or
rotate retained command output. See [installation](docs/INSTALL.md) for dependencies.

Run isolated integration for input/ownership/runtime changes; otherwise use the
relevant Python checks. Never overwrite a loaded plugin or substitute host input.
Keep dependencies acyclic and use the shared action/evaluation paths.
Describe the problem, behavior and checks. Input reports need KWin/Qt/app versions,
scaling, a minimal program and expected/observed behavior. Report native Wayland
and XWayland separately; claim only the workflows demonstrated by actual evidence.
