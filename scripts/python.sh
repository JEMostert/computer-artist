# Sourced by the scripts: pick the project's Python. $PYTHON wins, then the checkout's
# .venv, then python3 or python on PATH (CI installs one there).
python="${PYTHON:-}"
if [[ -z "$python" ]]; then
    for candidate in .venv/bin/python python3 python; do
        if [[ -x "$candidate" ]] || command -v "$candidate" >/dev/null; then
            python="$candidate"
            break
        fi
    done
fi
if [[ -z "$python" ]]; then
    echo 'No Python found; create .venv (see CONTRIBUTING.md) or set PYTHON' >&2
    exit 2
fi
