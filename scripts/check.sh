#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
python -m ruff check computer_artist tests examples ca
python -m ruff format --check computer_artist tests examples ca
# The extension has no build step; catch syntax errors where Node is available.
if command -v node >/dev/null; then
    node --check computer_artist/chrome_extension/background.js
fi
