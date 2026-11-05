#!/usr/bin/env python3
"""Run this operating system's tests: all of them, or only those a change touches.

  python scripts/run_tests.py                   every unit set for this OS (before a merge)
  python scripts/run_tests.py --changed [BASE]  sets for files changed since BASE (default:
                                                main), with uncommitted and untracked files
  python scripts/run_tests.py --integration     the full run plus this OS's integration checks
  python scripts/run_tests.py --list            the sets and where they run
  Add --plan to show what a run would select without running it. On Linux and macOS,
  scripts/test.sh runs this with the project's Python.

Edit SETS and CHANGES below when tests or modules move. Files no rule names select the
full run, so an unknown change runs too many tests rather than too few.
"""

import argparse
import fnmatch
import os
import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / 'tests'
OS = {'linux': 'linux', 'darwin': 'macos', 'win32': 'windows'}.get(sys.platform, sys.platform)
NAMES = {'linux': 'Linux', 'macos': 'macOS', 'windows': 'Windows'}

# Unit sets by area. Tests of another OS's backend are portable mocks and run anywhere
# they can; tests that need the real OS skip themselves elsewhere (test_windows has one
# native class). WINDOWS_PORTABLE lists the modules that also run on Windows; the rest
# need POSIX (Unix sockets, file modes, fcntl).
SETS = {
    'programs': ['test_runtime', 'test_programs', 'test_agent_keyboard', 'test_gestures',
                 'test_text_and_gestures', 'test_recovery', 'test_journal', 'test_observations',
                 'test_ink_cursor'],
    'storage': ['test_storage', 'test_records', 'test_control', 'test_supervisor', 'test_watch',
                'test_artifacts'],
    'reuse': ['test_fragments', 'test_fragment_integrity'],
    'linux': ['test_cli', 'test_client', 'test_input', 'test_accessibility', 'test_diagnostics'],
    'macos': ['test_macos', 'test_macos_activity'],
    'windows': ['test_windows'],
    'environments': ['test_box', 'test_containers', 'test_environments'],
    'browser': ['test_browser'],
    'maintenance': ['test_update', 'test_setup'],
}  # fmt: skip
WINDOWS_PORTABLE = {'test_windows', 'test_runtime', 'test_programs', 'test_journal',
                    'test_gestures', 'test_agent_keyboard', 'test_box', 'test_macos',
                    'test_macos_activity', 'test_containers', 'test_ink_cursor', 'test_browser'}  # fmt: skip

# Integration checks drive a real browser or desktop. They are slow, so they run only with
# --integration, or in a changed run whose files need them, and only on their own OS.
INTEGRATION = {
    'browser-integration': {
        'os': ('linux', 'macos'),
        'command': [sys.executable, 'tests/integration/check-browser.py'],
        'note': 'two headless Chrome for Testing instances; set CA_TEST_CHROME if not found',
    },
    'kwin-integration': {
        'os': ('linux',),
        'command': ['scripts/kwin-integration.sh'],
        'note': 'builds the plugin and runs a separate packaged KWin',
    },
    'windows-integration': {
        'os': ('windows',),
        'manual': 'tests/integration/check_windows.py --monitor MONITOR_ID (see docs/WINDOWS.md)',
        'note': 'drives disposable apps on the real desktop; run it by hand',
    },
}

# Changed files to sets, first match wins. Test modules select their own set, and docs
# get a relative-link check. Anything unmatched, such as the shared runtime, client,
# CLI, workspace, tests/support.py, scripts and pyproject.toml, selects the full run.
CHANGES = [
    ('docs and skills', ['docs/*', 'skills/*', '*.md', 'LICENSE', '.github/ISSUE_TEMPLATE/*'],
     ['docs']),
    ('Chrome extension', ['computer_artist/chrome_extension/*'],
     ['browser', 'browser-integration']),
    ('browser bridge', ['computer_artist/browser*.py'], ['browser']),
    ('browser harness', ['tests/integration/check-browser.py', 'tests/integration/*browser_fixture.py'],
     ['browser-integration']),
    ('macOS backend', ['computer_artist/macos*.py'], ['macos']),
    ('Windows backend', ['computer_artist/windows*.py'], ['windows', 'windows-integration']),
    ('Windows harness', ['tests/integration/check_windows*.py', 'tests/integration/windows_*.py'],
     ['windows-integration']),
    ('environments', ['computer_artist/box*.py', 'computer_artist/containers.py',
                      'computer_artist/environment*.py', 'containers/*'], ['environments']),
    ('fragments', ['computer_artist/fragments.py', 'computer_artist/contracts.py', 'examples/*'],
     ['reuse']),
    ('KWin lane', ['plugin/*', 'computer_artist/input.py', 'computer_artist/lanes.py',
                   'computer_artist/accessibility.py', 'computer_artist/diagnostics.py',
                   'tests/integration/*'], ['linux', 'kwin-integration']),
    ('maintenance', ['computer_artist/update.py', 'computer_artist/setup.py'], ['maintenance']),
]  # fmt: skip


def runnable(module):
    return OS != 'windows' or module in WINDOWS_PORTABLE


def changed_files(base):
    def git(*args):
        done = subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True)
        if done.returncode:
            raise SystemExit(f'git {" ".join(args)} failed: {done.stderr.strip()}')
        return done.stdout.split('\n')

    point = git('merge-base', base, 'HEAD')[0]
    files = git('diff', '--name-only', point) + git('ls-files', '--others', '--exclude-standard')
    return point, sorted({f for f in files if f})


def classify(path):
    """The rule label and sets for one changed file; None means the full run."""
    name = Path(path).name
    if path.startswith('tests/test_') and path.endswith('.py'):
        found = [s for s, modules in SETS.items() if Path(path).stem in modules]
        return (f'tests in {", ".join(found)}', found) if found else None
    for label, patterns, sets in CHANGES:
        if any(fnmatch.fnmatch(path, p) or (p.startswith('*') and fnmatch.fnmatch(name, p))
               for p in patterns):  # fmt: skip
            return label, sets
    return None


def check_links(paths):
    """Relative Markdown links in these files must point at existing files."""
    broken = []
    for path in paths:
        source = ROOT / path
        if source.suffix != '.md' or not source.is_file():
            continue
        for target in re.findall(r'\]\(([^)\s]+)\)', source.read_text(encoding='utf-8')):
            if re.match(r'[a-z][a-z0-9+.-]*:', target) or target.startswith('#'):
                continue
            if not (source.parent / target.split('#')[0]).exists():
                broken.append(f'{path}: {target}')
    for item in broken:
        print(f'  broken link {item}')
    return not broken


def run_unit(modules):
    # Imported as tests.MODULE from the checkout root, like `python -m unittest` there.
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.loadTestsFromNames([f'tests.{m}' for m in modules])
    return unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful()


def run_integration(name):
    entry = INTEGRATION[name]
    if 'manual' in entry:
        print(f'  {name} is manual: {entry["manual"]}')
        return None
    env = dict(os.environ)
    if OS == 'macos':
        env['TMPDIR'] = '/tmp'  # keeps Unix socket paths under the 104-byte limit
    return subprocess.run(entry['command'], cwd=ROOT, env=env).returncode


def list_sets():
    for os_name in ('linux', 'macos', 'windows'):
        print(f'{NAMES[os_name]}:')
        for name, modules in SETS.items():
            mods = [m for m in modules if os_name != 'windows' or m in WINDOWS_PORTABLE]
            if mods:
                print(f'  {name}: {" ".join(mods)}')
        for name, entry in INTEGRATION.items():
            if os_name in entry['os']:
                print(f'  {name} (integration): {entry["note"]}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument(
        '--changed',
        nargs='?',
        const='main',
        metavar='BASE',
        help='Only the sets for files changed since BASE (default: main)',
    )
    parser.add_argument(
        '--integration',
        action='store_true',
        help='Also run integration checks: all of this OS in a full run',
    )
    parser.add_argument(
        '--no-integration',
        action='store_true',
        help='Skip integration checks a changed run would select',
    )
    parser.add_argument('--list', action='store_true', help='Show the sets per OS and exit')
    parser.add_argument('--plan', action='store_true', help='Show the selection and exit')
    args = parser.parse_args()  # fmt: skip
    if args.list:
        list_sets()
        return 0
    if sys.version_info < (3, 12):
        raise SystemExit(f'The tests need Python 3.12 or later; {sys.executable} is '
                         f'{sys.version.split()[0]}. Use the project venv.')  # fmt: skip

    units, integrations, docs, full = set(), set(), [], args.changed is None
    if args.changed:
        point, files = changed_files(args.changed)
        print(f'Changed since {args.changed} ({point[:8]}), including uncommitted work: '
              f'{len(files)} files')  # fmt: skip
        for path in files:
            found = classify(path)
            if found is None:
                print(f'  {path} -> full run (shared code, test infrastructure or unmapped)')
                full = True
                continue
            label, sets = found
            print(f'  {path} -> {", ".join(sets) or "nothing"} ({label})')
            for name in sets:
                if name == 'docs':
                    docs.append(path)
                elif name in INTEGRATION:
                    integrations.add(name)
                else:
                    units.add(name)
    unregistered = sorted({f.stem for f in TESTS.glob('test_*.py')}
                          - {m for modules in SETS.values() for m in modules})  # fmt: skip
    if full:
        units = set(SETS)
    if args.integration and not args.changed:
        integrations = {n for n, e in INTEGRATION.items() if OS in e['os']}
    if args.no_integration:
        integrations = set()
    other = sorted(n for n in integrations if OS not in INTEGRATION[n]['os'])
    integrations = sorted(integrations - set(other))
    modules = [m for name in SETS if name in units for m in SETS[name] if runnable(m)]
    if full:
        # A new test module runs in the full run even before it is added to SETS.
        modules += [m for m in unregistered if runnable(m)]

    print(f'\nOn {NAMES.get(OS, OS)}:')
    print(f'  unit sets: {", ".join(n for n in SETS if n in units) or "none"}'
          f'{" (full run)" if full else ""}, {len(modules)} modules')  # fmt: skip
    if docs:
        print(f'  link check: {len(docs)} changed docs')
    if unregistered:
        print(f'  not in SETS yet: {", ".join(unregistered)}; add them to scripts/run_tests.py')
    print(f'  integration: {", ".join(integrations) or "none"}')
    for name in other:
        print(
            f'  not here: {name} runs on {" or ".join(NAMES[o] for o in INTEGRATION[name]["os"])}'
        )
    print(flush=True)
    if args.plan:
        return 0

    began = time.monotonic()
    ok, unavailable = True, []
    if docs:
        ok = check_links(docs) and ok
    if modules:
        ok = run_unit(modules) and ok
    for name in integrations:
        print(f'\n== {name}', flush=True)
        code = run_integration(name)
        if code == 2:
            unavailable.append(name)  # the harness could not start, e.g. no browser found
        elif code:
            ok = False
    print(f'\n{"PASSED" if ok else "FAILED"} in {time.monotonic() - began:.1f}s')
    if ok and unavailable:
        print(
            f'NOT RUN: {", ".join(unavailable)} could not start here (see above). Run it '
            'where it can, or pass --no-integration to skip it knowingly.'
        )
        return 3
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
