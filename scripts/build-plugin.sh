#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cmake -S "$project_root/plugin" -B "$project_root/build/plugin" -G Ninja -DCMAKE_BUILD_TYPE=Debug
cmake --build "$project_root/build/plugin" --parallel 4
ldd -r "$project_root/build/plugin/kwin/plugins/computerartist.so" > "$project_root/build/plugin-link-check.log" 2>&1
if rg -q 'undefined symbol:' "$project_root/build/plugin-link-check.log"; then
    cat "$project_root/build/plugin-link-check.log"
    exit 1
fi
