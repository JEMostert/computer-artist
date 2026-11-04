"""The ca browser command group: install, package, diagnose and drive tabs."""

import base64
import hashlib
import json
import math
import os
import shutil
import struct
import sys
import uuid
import zipfile
from pathlib import Path

from . import browser as bridge

EXTENSION_SOURCE = Path(__file__).resolve().parent / 'chrome_extension'
HOST_FILE = f'{bridge.HOST_NAME}.json'

# Per-user native messaging locations. Linux/macOS take a manifest file in the
# browser's NativeMessagingHosts folder; Windows takes a registry value naming it.
BROWSERS = {
    'chrome': ('google-chrome', 'Google/Chrome', r'Google\Chrome'),
    'chrome-beta': ('google-chrome-beta', 'Google/Chrome Beta', None),
    'chrome-dev': ('google-chrome-unstable', 'Google/Chrome Dev', None),
    'chrome-canary': (None, 'Google/Chrome Canary', None),
    'chrome-for-testing': ('google-chrome-for-testing', 'Google/Chrome for Testing', None),
    'chromium': ('chromium', 'Chromium', 'Chromium'),
    'brave': (
        'BraveSoftware/Brave-Browser',
        'BraveSoftware/Brave-Browser',
        r'BraveSoftware\Brave-Browser',
    ),  # fmt: skip
    'edge': ('microsoft-edge', 'Microsoft Edge', r'Microsoft\Edge'),
    'vivaldi': ('vivaldi', 'Vivaldi', None),
}

OPERATIONS = ('instances', 'label', 'tabs', 'open', 'claim', 'release', 'status', 'close',
              'focus', 'navigate', 'read', 'locate', 'wait', 'click', 'move', 'drag', 'scroll',
              'type', 'select', 'press', 'screenshot')  # fmt: skip
# Pointer coordinates, gestures and frames need this extension; older ones would
# ignore the new fields and act on an element's centre instead.
POINTER_VERSION = (0, 6, 0)
POINTER_FIELDS = ('x', 'y', 'button', 'count', 'modifiers', 'frame', 'path', 'delta', 'axis')
MODIFIERS = {'shift': 'Shift', 'control': 'Control', 'ctrl': 'Control', 'alt': 'Alt',
             'option': 'Alt', 'opt': 'Alt', 'meta': 'Meta', 'cmd': 'Meta', 'command': 'Meta',
             'super': 'Meta', 'win': 'Meta'}  # fmt: skip


def add_command(commands):
    parser = commands.add_parser(
        'browser',
        help='Drive tabs in your running Chrome/Chromium through the Computer Artist extension',
        description='Operate tabs in browsers where the Computer Artist extension is installed. '
        'Actions are DOM operations inside the page; they never move your pointer, switch tabs '
        'or focus windows, except the explicit focus command. Tabs ca acts on are claimed with a lease that your own clicks or '
        'keys in that tab revoke.',
    )
    actions = parser.add_subparsers(dest='browser_action', required=True)
    install = actions.add_parser(
        'install', help='Install the extension files and native messaging host for this user'
    )
    install.add_argument(
        '--browser',
        action='append',
        choices=sorted(BROWSERS),
        help='Register with this browser (repeatable; default: every detected browser)',
    )
    install.add_argument(
        '--user-data-dir',
        action='append',
        type=Path,
        help='Linux/macOS: also register for a browser started with --user-data-dir DIR',
    )
    install.add_argument('--dry-run', action='store_true', help='Report without writing')
    install.add_argument('--remove', action='store_true', help='Remove what install wrote')
    package = actions.add_parser('package', help='Build a reproducible extension zip')
    package.add_argument('--output', type=Path, default=Path('dist'), help='Folder (default: dist)')
    actions.add_parser('doctor', help='Check installation and connected browsers (read-only)')

    def target(name, help_text, tab=True, lease=False):
        sub = actions.add_parser(name, help=help_text)
        sub.add_argument(
            '--instance', help='Browser instance ID or label (see ca browser instances)'
        )
        if tab:
            sub.add_argument('--tab', required=True, help='Tab handle INSTANCE:TAB or a tab ID')
        if lease:
            sub.add_argument('--lease', required=True, help='Lease returned by open or claim')
        sub.add_argument('--timeout', type=float, default=30.0, help='Seconds (default: 30)')
        return sub

    def element(sub, required=True):
        group = sub.add_mutually_exclusive_group(required=required)
        group.add_argument('--ref', help='Element ref from ca browser locate')
        group.add_argument('--selector', help='CSS selector')
        group.add_argument('--text', help='Visible label or text')
        group.add_argument('--role', help='ARIA role, optionally with --name')
        sub.add_argument('--name', help='Accessible name used with --role')
        sub.add_argument('--first', action='store_true', help='Use the first of several matches')
        sub.add_argument(
            '--frame',
            action='append',
            metavar='SELECTOR',
            help='Find the element inside this same-origin iframe (repeat for nested frames)',
        )

    def pointer(sub, button=True):
        sub.add_argument(
            '--x',
            type=float,
            help='CSS pixels from the left of the element, or of the viewport without one',
        )
        sub.add_argument(
            '--y',
            type=float,
            help='CSS pixels from the top of the element, or of the viewport without one',
        )
        if button:
            sub.add_argument('--button', choices=('left', 'middle', 'right'), default=None)
        sub.add_argument(
            '--modifiers', help='Keys held during the action, e.g. Shift or Control+Shift'
        )

    target('instances', 'List connected browser instances', tab=False)
    label = target('label', 'Name a browser instance for later selection', tab=False)
    label.add_argument('label')
    target('tabs', 'List tabs with handles, activity and claims', tab=False)
    open_ = target('open', 'Open a URL in a new background tab and claim it', tab=False)
    open_.add_argument('url')
    open_.add_argument('--window', type=int, help='Window ID (default: the last focused)')
    open_.add_argument('--owner', help='Name shown on the claim (default: ca)')
    claim = target('claim', 'Claim an existing tab for agent actions')
    claim.add_argument('--owner', help='Name shown on the claim (default: ca)')
    target('release', 'Release a claimed tab', lease=True)
    status = target('status', 'Report a tab claim and why a lease ended')
    status.add_argument('--lease')
    target('close', 'Close a tab that ca opened', lease=True)
    target(
        'focus',
        'Bring a claimed tab and its window to the front. Only for visible testing or when '
        "the user should look; it takes the user's focus, so never use it for background work",
        lease=True,
    )
    navigate = target('navigate', 'Load a URL in a claimed tab, or reload it', lease=True)
    destination = navigate.add_mutually_exclusive_group(required=True)
    destination.add_argument('--url')
    destination.add_argument('--reload', action='store_true')
    read = target('read', 'Read page or element text')
    element(read, required=False)
    read.add_argument('--max-chars', type=int, default=20000)
    locate = target('locate', 'Find elements; returns refs for click and type')
    element(locate)
    locate.add_argument('--limit', type=int, default=20)
    wait = target('wait', 'Wait until an element or text appears')
    element(wait)
    wait.set_defaults(timeout=10.0)
    click = target(
        'click',
        'Click an element, a point in it (--x/--y) or a viewport point with DOM pointer events',
        lease=True,
    )
    element(click, required=False)
    pointer(click)
    click.add_argument('--count', type=int, choices=(1, 2, 3), help='2 = double, 3 = triple')
    move = target('move', 'Hover: move the pointer to an element or point', lease=True)
    element(move, required=False)
    pointer(move, button=False)
    drag = target(
        'drag',
        'Press, move through points and release; points are relative to the element or viewport',
        lease=True,
    )
    element(drag, required=False)
    drag.add_argument(
        '--from', dest='start', nargs=2, type=float, required=True, metavar=('X', 'Y')
    )
    drag.add_argument(
        '--via',
        nargs=2,
        type=float,
        action='append',
        metavar=('X', 'Y'),
        help='Intermediate point (repeatable, in order)',
    )
    drag.add_argument('--to', dest='end', nargs=2, type=float, required=True, metavar=('X', 'Y'))
    drag.add_argument('--button', choices=('left', 'middle', 'right'), default=None)
    drag.add_argument('--modifiers', help='Keys held during the drag, e.g. Shift')
    drag.add_argument(
        '--interval',
        type=float,
        help='Seconds between moves (default: frame rate in a visible tab, no delay otherwise)',
    )
    scroll = target('scroll', 'Scroll with a wheel event at an element or point', lease=True)
    element(scroll, required=False)
    pointer(scroll, button=False)
    scroll.add_argument(
        '--delta', type=float, required=True, help='CSS pixels; positive is down or right'
    )
    scroll.add_argument('--axis', choices=('vertical', 'horizontal'), default=None)
    type_ = target('type', 'Type text into a field with DOM events', lease=True)
    element(type_)
    type_.add_argument('--clear', action='store_true', help='Replace the current value')
    type_.add_argument('value', metavar='TEXT')
    select = target('select', 'Select a native HTML option by exact value', lease=True)
    element(select)
    select.add_argument('value', metavar='VALUE')
    press = target('press', 'Press a key such as Enter, Escape or Backspace', lease=True)
    element(press, required=False)
    press.add_argument('key')
    shot = target('screenshot', 'Capture the visible tab to a new PNG')
    shot.add_argument('output', type=Path, help='New PNG file; existing files are not overwritten')


def dispatch(args):
    action = args.browser_action
    if action == 'install':
        return install(args)
    if action == 'package':
        return package(args.output)
    if action == 'doctor':
        return doctor()
    try:
        return operate(args)
    except bridge.BrowserError as error:
        return error.as_dict()


# ------------------------------------------------------------------ operations


def operate(args):
    action = args.browser_action
    if action == 'instances':
        problems = []
        instances = bridge.live_instances(problems=problems)
        return {'ok': True, 'instances': instances, 'unreachable': problems}
    wanted = args.instance
    tab = None
    if getattr(args, 'tab', None) is not None:
        wanted, tab = bridge.parse_tab(args.tab, args.instance)
    problems = []
    instance = bridge.select_instance(bridge.live_instances(problems=problems), wanted, problems)
    request = {'op': action}
    if tab is not None:
        request['tab'] = tab
    for name in ('lease', 'url', 'reload', 'window', 'owner', 'ref', 'selector', 'text', 'role',
                 'name', 'first', 'max_chars', 'limit', 'clear', 'key', 'label', 'frame', 'x', 'y',
                 'button', 'count', 'delta', 'axis', 'interval'):  # fmt: skip
        value = getattr(args, name, None)
        if value not in (None, False):
            request[name] = value
    request.update(pointer_fields(args, action))
    if any(name in request for name in POINTER_FIELDS) or action in ('move', 'drag', 'scroll'):
        require_version(instance, POINTER_VERSION, action)
    if action == 'select':
        request['value'] = args.value
    if action == 'type':
        request['input'] = args.value
    if action in ('open', 'claim') and not request.get('owner'):
        request['owner'] = os.environ.get('CA_BROWSER_OWNER', 'ca')
    reply = bridge.call(instance, request, args.timeout)
    reply = with_handles(instance['instance'], reply)
    if action == 'screenshot' and reply.get('ok'):
        reply = save_png(reply, args.output)
    return {'instance': instance['instance'], **reply}


def pointer_fields(args, action):
    """Validate point, path and modifier options before anything reaches the browser."""
    fields = {}
    if getattr(args, 'modifiers', None):
        names = [part.strip().lower() for part in args.modifiers.replace(',', '+').split('+')]
        unknown = [name for name in names if name not in MODIFIERS]
        if unknown or not names:
            raise bridge.BrowserError(
                'invalid_modifier',
                f'Unknown modifier {", ".join(unknown) or args.modifiers!r}; use Shift, Control, '
                'Alt or Meta, joined with +',
            )
        fields['modifiers'] = sorted({MODIFIERS[name] for name in names})
    if action in ('click', 'move', 'scroll'):
        point = (getattr(args, 'x', None), getattr(args, 'y', None))
        if (point[0] is None) != (point[1] is None):
            raise bridge.BrowserError('invalid_point', 'Pass --x and --y together')
        targeted = any(getattr(args, name, None) for name in ('ref', 'selector', 'text', 'role'))
        if action != 'scroll' and point[0] is None and not targeted:
            raise bridge.BrowserError(
                'invalid_target',
                'Pass an element (--ref, --selector, --text or --role), --x and --y, or both',
            )
    if action == 'drag':
        fields['path'] = [list(args.start), *(list(p) for p in args.via or ()), list(args.end)]
    points = fields.get('path') or [[getattr(args, 'x', None) or 0, getattr(args, 'y', None) or 0]]
    for point in points:
        if not all(math.isfinite(value) for value in point):
            raise bridge.BrowserError('invalid_point', 'Coordinates must be finite numbers')
    return fields


def require_version(instance, minimum, action):
    running = instance.get('extension_version') or '0'
    try:
        version = tuple(int(part) for part in running.split('.')[:3])
    except ValueError:
        version = (0,)
    if version < minimum:
        wanted = '.'.join(map(str, minimum))
        raise bridge.BrowserError(
            'extension_outdated',
            f'{action} with these options needs Computer Artist extension {wanted} or later; '
            f'the browser runs {running}. Run ca browser install, then reload the extension card.',
            instance=instance.get('instance'),
            running=running,
            required=wanted,
        )


def with_handles(instance, reply):
    def mark(item):
        if isinstance(item, dict) and isinstance(item.get('tab'), int):
            return {'handle': f'{instance}:{item["tab"]}', **item}
        return item

    reply = mark(reply)
    if isinstance(reply.get('tabs'), list):
        reply['tabs'] = [mark(item) for item in reply['tabs']]
    return reply


def save_png(reply, output):
    prefix = 'data:image/png;base64,'
    data_url = reply.pop('data_url', '')
    if not data_url.startswith(prefix):
        return {'ok': False, 'error': 'invalid_capture', 'detail': 'The browser returned no PNG'}
    data = base64.b64decode(data_url[len(prefix) :])
    if data[:8] != b'\x89PNG\r\n\x1a\n' or len(data) < 24:
        return {'ok': False, 'error': 'invalid_capture', 'detail': 'The browser returned no PNG'}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, 'xb') as stream:
        stream.write(data)
    width, height = struct.unpack('>II', data[16:24])
    shot = {**reply, 'path': str(output.resolve()), 'width': width, 'height': height}
    viewport = reply.get('viewport') or {}
    if viewport.get('width'):
        # Screenshot pixels per CSS pixel (device pixel ratio times browser zoom):
        # divide an image position by it to get --x/--y for pointer commands.
        shot['scale'] = round(width / viewport['width'], 4)
    return shot


# -------------------------------------------------------------------- install


def extension_id(manifest):
    """Chrome derives an unpacked extension's ID from the manifest's public key."""
    digest = hashlib.sha256(base64.b64decode(manifest['key'])).hexdigest()[:32]
    return ''.join(chr(ord('a') + int(c, 16)) for c in digest)


def bundled_manifest():
    return json.loads((EXTENSION_SOURCE / 'manifest.json').read_text())


def extension_files(root=EXTENSION_SOURCE):
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob('*')
        if path.is_file() and not path.name.startswith('.')
    )


def host_targets(names=None, user_data_dirs=()):
    """(label, location) pairs: manifest folders on Linux/macOS, registry keys on Windows."""
    targets = []
    # Explicit --browser names are registered even before first use; without
    # them, --user-data-dir alone touches only those folders, and a bare install
    # registers the browsers whose profiles exist.
    detect = not names
    for name in names or ([] if user_data_dirs else sorted(BROWSERS)):
        linux, macos, windows = BROWSERS[name]
        if os.name == 'nt':
            if not windows:
                continue
            profile = Path(os.environ.get('LOCALAPPDATA', '')) / windows / 'User Data'
            if detect and not profile.is_dir():
                continue
            targets.append((name, rf'Software\{windows}\NativeMessagingHosts\{bridge.HOST_NAME}'))
            continue
        if sys.platform == 'darwin':
            base = macos and Path.home() / 'Library/Application Support' / macos
        else:
            config = Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config')
            base = linux and config / linux
        if not base or (detect and not base.is_dir()):
            continue
        targets.append((name, str(base / 'NativeMessagingHosts' / HOST_FILE)))
    for directory in user_data_dirs or ():
        if os.name == 'nt':
            raise ValueError('Windows registers hosts per browser; use --browser instead')
        path = Path(directory).expanduser().absolute()
        targets.append((f'user-data-dir:{path}', str(path / 'NativeMessagingHosts' / HOST_FILE)))
    return targets


def launcher_files(host_dir):
    package_parent = Path(__file__).resolve().parents[1]
    script = (
        '"""Started by Chrome through native messaging; see ca browser install."""\n'
        'import sys\n\n'
        f'sys.path.insert(0, {str(package_parent)!r})\n'
        'from computer_artist.browser_host import main\n\n'
        'raise SystemExit(main())\n'
    )
    launch = host_dir / 'launch.py'
    if os.name == 'nt':
        command = host_dir / 'ca-browser-host.bat'
        wrapper = f'@echo off\r\n"{sys.executable}" -I "{launch}" %*\r\n'
    else:
        command = host_dir / 'ca-browser-host'
        wrapper = f"#!/bin/sh\nexec '{sys.executable}' -I '{launch}' \"$@\"\n"
    return {launch: script, command: wrapper}, command


def owned_manifest(path, command):
    try:
        manifest = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return False
    return manifest.get('name') == bridge.HOST_NAME and manifest.get('path') == str(command)


def install(args):
    manifest = bundled_manifest()
    ident = extension_id(manifest)
    root = bridge.data_dir()
    extension_dir = root / 'chrome-extension'
    host_dir = root / 'browser-host'
    files, command = launcher_files(host_dir)
    targets = host_targets(args.browser, args.user_data_dir)
    host_manifest = {
        'name': bridge.HOST_NAME,
        'description': 'Computer Artist browser bridge for the ca CLI',
        'path': str(command),
        'type': 'stdio',
        'allowed_origins': [f'chrome-extension://{ident}/'],
    }
    results = []
    if not args.remove and not args.dry_run:
        bridge.private_dir(host_dir)
    for label, location in targets:
        if args.remove:
            action = remove_host(location, command, args.dry_run)
        else:
            action = write_host(location, host_manifest, host_dir, command, args.dry_run)
        results.append({'browser': label, 'location': location, 'action': action})
    if args.remove:
        if not args.dry_run:
            for path in files:
                path.unlink(missing_ok=True)
            if (extension_dir / 'manifest.json').is_file():
                shutil.rmtree(extension_dir)
        return {'ok': True, 'action': 'remove', 'dry_run': args.dry_run, 'hosts': results,
                'next': 'Remove the extension in chrome://extensions as well.'}  # fmt: skip
    if not args.dry_run:
        for path, content in files.items():
            path.write_text(content, encoding='utf-8', newline='')
            if path == command and os.name != 'nt':
                path.chmod(0o755)
        copy_extension(extension_dir)
    return {
        'ok': True,
        'action': 'install',
        'dry_run': args.dry_run,
        'extension_id': ident,
        'extension_version': manifest['version'],
        'extension_dir': str(extension_dir),
        'host': str(command),
        'hosts': results,
        'next': [
            "Open chrome://extensions (or your browser's extensions page) and turn on "
            'Developer mode.',
            f'Choose "Load unpacked" and select {extension_dir}. After later installs, press '
            'the reload button on the extension card.',
            'Run `ca browser instances` to confirm the browser is connected.',
        ],
        **({} if results else {'warning': 'No browser profile was detected; pass --browser'}),
    }


def write_host(location, manifest, host_dir, command, dry_run):
    if os.name == 'nt':
        import winreg

        path = host_dir / HOST_FILE
        if not dry_run:
            path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, location) as key:
                winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(path))
        return 'registered'
    path = Path(location)
    if path.exists() and not owned_manifest(path, command):
        current = json.loads(path.read_text() or '{}')
        if current.get('name') != bridge.HOST_NAME:
            raise ValueError(f'Refusing to replace an unrelated host manifest: {path}')
    action = 'unchanged' if path.is_file() and json.loads(path.read_text()) == manifest else 'write'
    if action == 'write' and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return action


def remove_host(location, command, dry_run):
    if os.name == 'nt':
        import winreg

        path = registered_manifest(location)
        if path is None:
            return 'absent'
        if not owned_manifest(path, command):
            return 'kept_unowned'
        if not dry_run:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, location)
        return 'remove'
    path = Path(location)
    if not path.is_file():
        return 'absent'
    if not owned_manifest(path, command):
        return 'kept_unowned'
    if not dry_run:
        path.unlink()
    return 'remove'


def registered_manifest(location):
    """Windows: the manifest path a browser's registry key names, or None."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, location) as key:
            return Path(winreg.QueryValue(key, None))
    except FileNotFoundError:
        return None


def copy_extension(destination):
    """Replace the installed extension copy as a whole, never merging old files."""
    if destination.exists() and not (destination / 'manifest.json').is_file():
        raise ValueError(f'Refusing to replace an unrelated folder: {destination}')
    staging = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}')
    shutil.copytree(EXTENSION_SOURCE, staging, ignore=shutil.ignore_patterns('.*'))
    previous = None
    if destination.exists():
        previous = destination.with_name(f'.{destination.name}.old.{uuid.uuid4().hex}')
        destination.rename(previous)
    staging.rename(destination)
    if previous:
        shutil.rmtree(previous, ignore_errors=True)


# -------------------------------------------------------------------- package


def package(output):
    """Write a byte-for-byte reproducible zip of the bundled extension."""
    manifest = bundled_manifest()
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f'computer-artist-chrome-extension-{manifest["version"]}.zip'
    temp = path.with_name(f'.{path.name}.{uuid.uuid4().hex}')
    try:
        with zipfile.ZipFile(temp, 'w') as archive:
            for name in extension_files():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, (EXTENSION_SOURCE / name).read_bytes(), compresslevel=9)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)
    return {
        'ok': True,
        'path': str(path.resolve()),
        'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
        'extension_id': extension_id(manifest),
        'extension_version': manifest['version'],
        'files': extension_files(),
        'note': 'Unzip and use "Load unpacked"; Chrome installs packaged extensions from outside '
        'the Web Store only through enterprise policy.',
    }


# --------------------------------------------------------------------- doctor


def doctor():
    manifest = bundled_manifest()
    ident = extension_id(manifest)
    root = bridge.data_dir()
    extension_dir = root / 'chrome-extension'
    files, command = launcher_files(root / 'browser-host')
    checks = []

    def check(name, passed, detail, remedy=None):
        checks.append({'name': name, 'passed': bool(passed), 'detail': detail,
                       **({'remedy': remedy} if remedy and not passed else {})})  # fmt: skip

    installed = extension_dir / 'manifest.json'
    version = json.loads(installed.read_text()).get('version') if installed.is_file() else None
    check('extension_files', version == manifest['version'], f'{extension_dir}: {version}',
          'Run ca browser install, then reload the extension card.')  # fmt: skip
    current = all(path.is_file() and path.read_text() == text for path, text in files.items())
    check('host_launcher', current, str(command), 'Run ca browser install.')
    hosts = []
    for label, location in host_targets():
        if os.name == 'nt':
            path = registered_manifest(location)
        else:
            path = Path(location)
        ok = bool(path and path.is_file() and owned_manifest(path, command))
        if ok:
            origins = json.loads(path.read_text()).get('allowed_origins')
            ok = origins == [f'chrome-extension://{ident}/']
        hosts.append({'browser': label, 'location': location, 'registered': ok})
    check('native_host', any(h['registered'] for h in hosts),
          f'{sum(h["registered"] for h in hosts)} of {len(hosts)} detected browsers registered',
          'Run ca browser install.')  # fmt: skip
    problems = []
    try:
        instances = bridge.live_instances(problems=problems)
    except bridge.BrowserError as error:
        instances = []
        problems.append(error.as_dict())
    warnings = [
        {'code': 'extension_version', 'instance': i['instance'], 'running': i.get('extension_version'),
         'bundled': manifest['version'], 'remedy': 'Reload the extension card in the browser.'}
        for i in instances if i.get('extension_version') != manifest['version']
    ]  # fmt: skip
    return {
        'ok': all(c['passed'] for c in checks),
        'extension_id': ident,
        'rendezvous': str(bridge.rendezvous_dir()),
        'checks': checks,
        'hosts': hosts,
        'instances': [bridge.summary(i) for i in instances],
        'unreachable': problems,
        'warnings': warnings,
    }
