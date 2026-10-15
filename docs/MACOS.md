# macOS

Requires macOS with Python 3.12 or later and Docker Desktop. There is
no native macOS desktop backend: `ca` does not see or drive Mac applications, and
desktop commands without `--environment` refuse with a pointer to `ca env`.
Agents work in private environments, each a Linux box container with its own KWin
desktop, exactly as on [Windows](WINDOWS.md#private-environments-linux-boxes).

```bash
git clone https://github.com/JEMostert/computer-artist.git
cd computer-artist
python3.13 -m venv .venv                       # e.g. brew install python@3.13
.venv/bin/python -m pip install -e '.[dev]'
source .venv/bin/activate
ca doctor                                      # container_runtime must pass
ca setup --codex                               # or --skills-dir PATH
ca env start web --project .
ca env exec web --name editor --wait-window --timeout 120 -- kwrite /project/notes.txt
ca --environment web observe --window editor
ca env stop web
```

## Status

Status: **implemented** means code and unit tests exist; **verified** names what
was exercised live.

| Feature | macOS scope | Status |
| --- | --- | --- |
| Private environments | Linux box per environment in Docker Desktop; state in `~/.local/state/computer-artist/environments`; host/box path translation | Verified 15 Oct (Docker Desktop 4.95, Apple silicon) |
| Apple silicon | The Arch-based box runs as `linux/amd64` under Docker Desktop's emulation; first build about 3 minutes, later starts about 20 seconds | Verified 15 Oct |
| Apps in the box | KWrite type/save with a fresh-file check, Firefox with a private profile, recipe-derived images, startup apps, AT-SPI tree | Verified 15 Oct |
| Host dev servers | `http://host.docker.internal:PORT` reaches a Mac server listening on all interfaces | Verified 15 Oct; loopback-only servers unverified |
| Container runtime | Docker Desktop only, as on Windows; selecting Podman is refused | Implemented |
| User's Mac desktop | None; no independent or host input, capture or accessibility on macOS itself | Not implemented |

The box image is about 5 GB. Docker Desktop's default memory limit is enough for
one or two environments; raise it in Docker Desktop's settings for more. `network
= "host"` depends on Docker Desktop's host networking setting and is untested. Run
the unit tests with `scripts/check.sh` and `scripts/test.sh`. Results of 15
October 2026 are summarized in
[docs/validation/2026-10-15-macos.json](validation/2026-10-15-macos.json).
