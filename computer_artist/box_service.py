"""Entry point of a Linux box: one private environment in the container's foreground.

The host (Windows, or Linux for testing) runs this image with `ca env start`. There
is no systemd inside, so the desktop service runs as this process's child and the
container stops with it. Applications run inside the box like host applications in
a Linux environment.
"""

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

from . import environments


def main(argv=None):
    parser = argparse.ArgumentParser(prog='box_service')
    parser.add_argument('name')
    parser.add_argument('--width', type=int, default=environments.DEFAULTS['width'])
    parser.add_argument('--height', type=int, default=environments.DEFAULTS['height'])
    parser.add_argument('--xwayland', action='store_true')
    args = parser.parse_args(argv)
    name = environments.check_name(args.name)
    # Created here: the box may run as any user (see box.user_args).
    runtime = Path(os.environ.setdefault('XDG_RUNTIME_DIR', '/tmp/ca-runtime'))
    runtime.mkdir(mode=0o700, exist_ok=True)
    state, _ = environments.locations(name)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    # The box itself is the environment's host: apps started with ca env exec run here.
    definition = environments.normalize(
        {'width': args.width, 'height': args.height, 'xwayland': args.xwayland}, state
    )
    from .files import atomic_json

    atomic_json(state / 'definition.json', definition)
    environments.make_folders(state)
    env, service = environments.prepare_service(name, definition, environments.plugin_source(None))
    child = subprocess.Popen(
        ['/usr/bin/env', '-i', *[f'{k}={v}' for k, v in env.items()], *service]
    )

    def stop(*_):
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        return child.wait()
    finally:
        stop()


if __name__ == '__main__':
    sys.exit(main())
