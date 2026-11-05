#!/bin/bash
# render.sh OUTDIR [QUERY] : render the reel's frames with a headless Chrome into OUTDIR.
# QUERY defaults to every frame with motion blur: from=0&to=900&sub=5
# CHROME: a chrome-headless-shell binary (for example Playwright's chromium_headless_shell).
set -u
R=$(cd "$(dirname "$0")" && pwd)
OUT=$(realpath -m "$1"); QUERY=${2:-from=0&to=900&sub=5}; PORT=${PORT:-8767}
CHROME=${CHROME:-chrome-headless-shell}
mkdir -p "$OUT"; rm -f "$OUT/DONE"
python3 "$R/server.py" "$PORT" "$OUT" & SRV=$!
sleep 0.5
"$CHROME" --no-sandbox --window-size=1920,1080 --virtual-time-budget=100000000 --dump-dom \
  "http://127.0.0.1:$PORT/reel/index.html?$QUERY" >/dev/null 2>&1 & BR=$!
while [ ! -f "$OUT/DONE" ] && kill -0 $SRV 2>/dev/null && kill -0 $BR 2>/dev/null; do sleep 0.5; done
kill $BR $SRV 2>/dev/null; wait 2>/dev/null
if [ ! -f "$OUT/DONE" ]; then echo "render failed: the browser or the server stopped early" >&2; exit 1; fi
read -r written requested < "$OUT/DONE"
if [ "$written" != "$requested" ]; then echo "render failed: wrote $written of $requested frames" >&2; exit 1; fi
echo "rendered $written frames into $OUT"
