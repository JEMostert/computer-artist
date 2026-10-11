"""The process tree inside one environment's systemd user service.

serve() runs KWin on the private socket; KWin starts the Manager as its session
child, so the Manager sees the private display and bus and answers the CLI over
the runtime's manager socket.
"""

import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from .environments import APP_ENV, MAX_REQUEST, check_env, host_environment

# Distributions install the registry in either place.
REGISTRY_PATHS = ('/usr/lib/at-spi2-registryd', '/usr/libexec/at-spi2-registryd')


def serve(config_path):
    config = json.loads(Path(config_path).read_text())
    runtime, state = Path(config['runtime']), Path(config['state'])
    command = [
        'kwin_wayland', '--virtual', '--socket', str(runtime / 'shared/wayland'),
        '--width', str(config['width']), '--height', str(config['height']),
        '--no-lockscreen', '--no-global-shortcuts', '--no-kactivities',
    ]  # fmt: skip
    if config['xwayland']:
        command.append('--xwayland')
    # KWin supplies DISPLAY/XAUTHORITY to the session child when XWayland is enabled.
    command += [
        '--exit-with-session',
        shlex.join(
            [sys.executable, '-m', 'computer_artist.environment_service', '_session', config_path]
        ),
    ]
    with (state / 'logs/kwin.log').open('ab') as log:
        return subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)


class Manager:
    def __init__(self, config):
        self.config = config
        self.runtime, self.state = Path(config['runtime']), Path(config['state'])
        shared = self.runtime / 'shared'
        env = dict(os.environ)
        env.pop('QT_PLUGIN_PATH', None)
        env.update(
            APP_ENV,
            WAYLAND_DISPLAY=str(shared / 'wayland'),
            CA_SOCKET=str(self.runtime / 'control'),
            CA_WINDOW_DIR=str(self.state / 'window'),
            CA_OUTPUT_DIR=str(self.state / 'output'),
        )
        self.env = env
        if env.get('XAUTHORITY') and Path(env['XAUTHORITY']).is_file():
            shutil.copyfile(env['XAUTHORITY'], shared / 'xauthority')
        # Bus-activated services (portals, notifications) join this desktop.
        names = [k for k in ('WAYLAND_DISPLAY', 'DISPLAY', 'XAUTHORITY', 'XDG_CURRENT_DESKTOP',
                             'XDG_SESSION_TYPE', 'QT_QPA_PLATFORM') if env.get(k)]  # fmt: skip
        subprocess.run(['dbus-update-activation-environment', *names], env=env, capture_output=True)
        # Keep the newest application logs; every session adds a few.
        for old in sorted((self.state / 'logs/apps').glob('*.log'))[:-200]:
            old.unlink(missing_ok=True)
        self.apps = {}
        self.counter = 0
        self.stamp = time.strftime('%Y%m%d-%H%M%S')
        self.info = {
            'ok': True,
            'running': True,
            'name': config['name'],
            'kind': config.get('kind', 'host'),
            'state': str(self.state),
            'socket': env['CA_SOCKET'],
            'wayland': env['WAYLAND_DISPLAY'],
            'bus': str(shared / 'bus'),
            'display': env.get('DISPLAY'),
            'home': str(self.state / 'home'),
            'window_dir': env['CA_WINDOW_DIR'],
            'output_dir': env['CA_OUTPUT_DIR'],
            'width': config['width'],
            'height': config['height'],
            'xwayland': config['xwayland'],
            'container': config.get('container'),
            'image': config.get('image'),
            'shared_files': True,
            'started_at': config.get('started_at'),
        }
        self.registry = self.start_registry()
        self.info['accessibility'] = self.registry is not None

    def start_registry(self):
        """Start the AT-SPI registry; a missing or broken one only disables accessibility."""
        binary = next((p for p in REGISTRY_PATHS if Path(p).is_file()), None)
        if binary is None:
            return None
        with (self.state / 'logs/at-spi-registryd.log').open('ab') as log:
            try:
                return subprocess.Popen(
                    [binary], env=self.env, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT,
                )  # fmt: skip
            except OSError as error:
                log.write(f'Could not start {binary}: {error}\n'.encode())
                return None

    def record(self, app):
        child = app['process']
        code = child.poll()
        return {
            **{k: v for k, v in app.items() if k != 'process'},
            'alive': code is None,
            'returncode': code,
        }

    def handle(self, request):
        op = request.get('op')
        if op == 'status':
            return {**self.info, 'applications': [self.record(a) for a in self.apps.values()]}
        if op == 'app':
            app = self.apps.get(request.get('app'))
            if not app:
                raise ValueError('Unknown application id')
            return {'ok': True, 'app': self.record(app)}
        if op == 'exec':
            return {'ok': True, 'app': self.record(self.execute(request))}
        raise ValueError('Unknown environment operation')

    def execute(self, request):
        command = request.get('argv')
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(a, str) or '\0' in a for a in command)
        ):
            raise ValueError('Expected a nonempty command argument list')
        extra = check_env(request.get('env') or {})
        on_host = bool(request.get('on_host')) or not self.config.get('container')
        cwd = request.get('cwd')
        if on_host and cwd and not Path(cwd).is_dir():
            raise ValueError(f'Working directory does not exist: {cwd}')
        self.counter += 1
        identity = f'a{self.counter}'
        log_path = self.state / 'logs/apps' / f'{self.stamp}-{identity}.log'
        if on_host:
            argv, env = command, {**self.env, **extra}
            cwd = cwd or str(Path.home())
        else:
            argv = ['podman', 'exec', '--interactive=false']
            for k, v in extra.items():
                argv += ['--env', f'{k}={v}']
            if cwd:
                argv += ['--workdir', cwd]
            argv += [self.config['container'], *command]
            env, cwd = host_environment(), str(self.state)
        with log_path.open('wb') as log:
            log.write(f'$ {shlex.join(command)}\n'.encode())
            log.flush()
            child = subprocess.Popen(
                argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT, start_new_session=True,
            )  # fmt: skip
        app = {
            'id': identity,
            'argv': command,
            'where': 'host' if on_host else 'container',
            'cwd': cwd if on_host else request.get('cwd') or self.config.get('workdir'),
            'pid': child.pid,
            'log': str(log_path),
            'started_at': time.time(),
            'process': child,
        }
        self.apps[identity] = app
        return app

    def close(self):
        for app in self.apps.values():
            if app['process'].poll() is None:
                try:
                    os.killpg(app['process'].pid, signal.SIGTERM)
                except OSError:
                    pass
        if self.registry is not None:
            self.registry.terminate()
            try:
                self.registry.wait(2)
            except subprocess.TimeoutExpired:
                self.registry.kill()

    def serve(self):
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(self.runtime / 'manager'))
            os.chmod(self.runtime / 'manager', 0o600)
            server.listen(8)
            server.settimeout(1)
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            try:
                while True:
                    try:
                        connection, _ = server.accept()
                    except TimeoutError:
                        continue
                    with connection:
                        connection.settimeout(3)
                        try:
                            with connection.makefile('rb') as stream:
                                reply = self.handle(json.loads(stream.readline(MAX_REQUEST + 1)))
                        except Exception as error:
                            reply = {'ok': False, 'error': str(error)}
                        try:
                            connection.sendall(json.dumps(reply).encode() + b'\n')
                        except OSError:
                            pass
            finally:
                self.close()


# Command line


if __name__ == '__main__':
    if sys.argv[1] == '_serve':
        sys.exit(serve(sys.argv[2]))
    elif sys.argv[1] == '_session':
        Manager(json.loads(Path(sys.argv[2]).read_text())).serve()
