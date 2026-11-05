#!/usr/bin/env bash
# Linux and macOS: run the tests for this OS with the project's Python. See
# scripts/run_tests.py for the sets; --changed [BASE] runs only what a change touches.
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
source scripts/python.sh
exec "$python" scripts/run_tests.py "$@"
