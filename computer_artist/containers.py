"""Podman and Docker command lines for environment containers.

Both runtimes get the same container: the private desktop's display and bus
sockets, the definition's mounts and a long sleep as the process that apps join
with exec. Only the user mapping and a few flags differ.
"""

import os
import shutil
import subprocess

RUNTIMES = ('podman', 'docker')
MISSING = (
    'Container environments need Docker or Podman and neither was found. Tell the user to '
    'install one (Linux: rootless Podman recommended, or Docker; Windows: Docker Desktop '
    'with the WSL 2 backend), or use an environment without --image/--containerfile.'
)


def available():
    return [name for name in RUNTIMES if shutil.which(name)]


def choose(preferred=None):
    """Explicit choice, then CA_CONTAINER_RUNTIME, then Podman, then Docker."""
    preferred = preferred or os.environ.get('CA_CONTAINER_RUNTIME') or None
    if preferred is not None:
        if preferred not in RUNTIMES:
            raise ValueError(f'Container runtime must be one of {RUNTIMES}, not {preferred!r}')
        if not shutil.which(preferred):
            raise RuntimeError(f'{preferred} was selected but is not installed. ' + MISSING)
        return Runtime(preferred)
    found = available()
    if not found:
        raise RuntimeError(MISSING)
    return Runtime(found[0])


class Runtime:
    def __init__(self, name, env=None):
        if name not in RUNTIMES:
            raise ValueError(f'Unknown container runtime {name!r}')
        self.name = name
        self.env = env
        self._rootless = None

    def __repr__(self):
        return f'Runtime({self.name!r})'

    def run(self, *args, timeout=60):
        return subprocess.run(
            [self.name, *args], capture_output=True, text=True, env=self.env, timeout=timeout
        )

    def check(self):
        """Raise a user-facing error when the runtime cannot answer (e.g. Docker daemon down)."""
        try:
            result = self.run('info', '--format', '{{json .}}', timeout=30)
        except FileNotFoundError:
            raise RuntimeError(MISSING) from None
        except subprocess.TimeoutExpired:
            raise RuntimeError(f'{self.name} did not answer; is its service running?') from None
        if result.returncode:
            detail = (result.stderr or result.stdout).strip()[-400:]
            hint = 'start Docker Desktop or the docker service' if self.name == 'docker' else ''
            raise RuntimeError(
                f'{self.name} is installed but not usable ({detail}). Tell the user to {hint or "fix it"}.'
            )

    @property
    def rootless(self):
        """Podman here is always rootless; Docker reports it in its security options."""
        if self._rootless is None:
            if self.name == 'podman':
                self._rootless = True
            else:
                result = self.run('info', '--format', '{{json .SecurityOptions}}', timeout=30)
                self._rootless = 'rootless' in result.stdout
        return self._rootless

    # Images

    def image_exists(self, tag):
        verb = ('image', 'exists', tag) if self.name == 'podman' else ('image', 'inspect', tag)
        return self.run(*verb).returncode == 0

    def build_command(self, tag, containerfile, context, pull=False):
        command = [self.name, 'build', '--tag', tag, '--file', containerfile]
        if pull:
            command.append('--pull=always' if self.name == 'podman' else '--pull')
        return command + [context]

    def pull_command(self, tag):
        return [self.name, 'pull', tag]

    def remove_image(self, tag):
        self.run('image', 'rm', tag)

    # Containers

    def exists(self, name):
        verb = (
            ('container', 'exists', name)
            if self.name == 'podman'
            else ('container', 'inspect', name)
        )
        return self.run(*verb).returncode == 0

    def state(self, name, field='Status'):
        result = self.run('container', 'inspect', '--format', '{{.State.%s}}' % field, name)
        return result.stdout.strip() if result.returncode == 0 else ''

    def remove(self, name, timeout=30):
        args = (
            ['rm', '--force', '--time', '3', name]
            if self.name == 'podman'
            else ['rm', '--force', name]
        )
        self.run(*args, timeout=timeout)

    def remove_words(self, name):
        """Arguments for a cleanup command that must not fail when the container is gone."""
        if self.name == 'podman':
            return ['rm', '--force', '--ignore', '--time', '3', name]
        return ['rm', '--force', name]

    def user_args(self, devices=()):
        """Run as the invoking user so the private sockets (mode 0700) stay reachable."""
        if self.name == 'podman':
            return ['--userns=keep-id']
        if self.rootless or os.name == 'nt' or not hasattr(os, 'getuid'):
            # Rootless Docker maps container root to the invoking user.
            return []
        args = ['--user', f'{os.getuid()}:{os.getgid()}']
        groups = set()
        for device in devices:
            if device.startswith('/dev/'):
                for path in [device] + [os.path.join(device, n) for n in _listdir(device)]:
                    try:
                        groups.add(os.stat(path).st_gid)
                    except OSError:
                        pass
        for gid in sorted(groups - {os.getgid()}):
            args += ['--group-add', str(gid)]
        return args

    def device_args(self, devices):
        args = []
        for device in devices:
            if device == 'nvidia.com/gpu=all' and self.name == 'docker':
                args += ['--gpus', 'all']
            else:
                args += ['--device', device]
        return args

    def run_prefix(self, name):
        """`run` options shared by both runtimes; Podman replaces a stale container itself."""
        command = [self.name, 'run', '--detach', '--rm', '--init', '--name', name]
        if self.name == 'podman':
            command.insert(5, '--replace')
        return command

    def exec_command(self, name, command, env=None, workdir=None, interactive=False):
        argv = [self.name, 'exec']
        if interactive:
            argv.append('--interactive')
        elif self.name == 'podman':
            argv.append('--interactive=false')
        for k, v in (env or {}).items():
            argv += ['--env', f'{k}={v}']
        if workdir:
            argv += ['--workdir', workdir]
        return argv + [name, *command]


def _listdir(path):
    try:
        return os.listdir(path) if os.path.isdir(path) else []
    except OSError:
        return []
