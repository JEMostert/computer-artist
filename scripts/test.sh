#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"
python -m unittest discover -s tests -v
if [[ "${1:-}" == "--integration" ]]; then
    ./scripts/build-plugin.sh
    wayland-scanner client-header /usr/share/plasma-wayland-protocols/fake-input.xml build/fake-input-client.h
    wayland-scanner private-code /usr/share/plasma-wayland-protocols/fake-input.xml build/fake-input-protocol.c
    cc tests/integration/stock-test-input.c build/fake-input-protocol.c -Ibuild -lwayland-client -lm -o build/stock-test-input
    # Keep regression evidence separate from the user's retained command runs.
    integration_output="${CA_INTEGRATION_OUTPUT_DIR:-$(mktemp -d /tmp/ca-integration-output-XXXXXX)}"
    CA_OUTPUT_DIR="$integration_output" python tests/integration/run-stock-plugin.py --headless --check
    CA_OUTPUT_DIR="$integration_output" python tests/integration/run-stock-plugin.py --headless --check-independent
    CA_OUTPUT_DIR="$integration_output" python tests/integration/run-stock-plugin.py --headless --check-recovery
elif [[ $# -gt 0 ]]; then
    echo 'Usage: scripts/test.sh [--integration]' >&2
    exit 2
fi
