#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
source scripts/python.sh
"$python" -m ruff check computer_artist tests examples scripts ca
"$python" -m ruff format --check computer_artist tests examples scripts ca
# The extension has no build step; catch syntax errors where Node is available.
if command -v node >/dev/null; then
    for script in computer_artist/chrome_extension/*.js; do node --check "$script"; done
fi
