# Contributing

Read [AGENTS.md](AGENTS.md) and [capabilities](docs/CAPABILITIES.md). Keep the CLI
central, reuse `Context` for actions and keep compositor experiments separate
from the running desktop.

```bash
python3.12 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
./scripts/check.sh                 # lint, format, extension syntax
./scripts/test.sh --changed        # the tests your change touches (the default)
./scripts/test.sh                  # every unit test for this OS (before a merge)
./scripts/test.sh --integration    # plus this OS's integration checks
```

The scripts use `.venv/bin/python` (or `$PYTHON`), so no activated venv is needed.
On Windows, run `python scripts/run_tests.py` with the same options.

### Tests per operating system

`scripts/run_tests.py` holds the test sets and a short mapping from changed files to
sets; `--list` prints them per OS, and `--plan` shows a selection without running it.

- Every OS runs the portable unit sets. Tests of another OS's backend run there only
  as mocks. Tests that need the real OS skip themselves elsewhere, such as the native
  Windows class in `test_windows`. Windows runs the modules in `WINDOWS_PORTABLE`; the
  rest need POSIX.
- Integration checks run only on their own OS: `kwin-integration` (Linux, a separate
  packaged KWin), `browser-integration` (Linux or macOS, two headless Chrome for
  Testing instances; set `CA_TEST_CHROME` if it is not found) and
  `windows-integration` (by hand on Windows, see [Windows](docs/WINDOWS.md)).

`--changed [BASE]` compares with `main` by default, including uncommitted and
untracked files, and prints each file's sets and why before running. Docs and skill
changes get only a relative-link check. A browser change runs the browser tests, and
an extension change also runs the browser integration. Shared code, test
infrastructure and any unmapped file select the full run. Integration checks of
another OS are never run. A needed check that cannot start here (exit 3) is reported
as `NOT RUN`; run it where it can, or pass `--no-integration` to skip it knowingly.

Agents run `--changed` by default. They run the full suite before a merge and when a
change touches shared code or test infrastructure. Add a rule to `CHANGES` when a new
area gets its own tests.

KWin integration uses system Python with PySide6/Pillow, builds the plugin and
launches a separate packaged KWin virtual instance. Evidence uses a fresh temporary
folder; `CA_INTEGRATION_OUTPUT_DIR` overrides it. Tests do not deploy the host plugin
or rotate retained command output. See [installation](docs/INSTALL.md) for
dependencies. Never overwrite a loaded plugin or substitute host input.
Keep dependencies acyclic and use the shared action/evaluation paths.
Describe the problem, behavior and checks. Input reports need KWin/Qt/app versions,
scaling, a minimal program and expected/observed behavior. Report native Wayland
and XWayland separately; claim only the workflows demonstrated by actual evidence.

Raise the version in `computer_artist/chrome_extension/manifest.json` with every
extension change, so `ca browser instances` shows which one a browser runs.
