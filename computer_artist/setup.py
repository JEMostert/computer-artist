"""Install the bundled skill without connecting to the desktop."""

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

from .files import atomic_json

COPY_RECORD = '.ca-install.json'


def skill_hashes(root):
    if any(path.is_symlink() for path in root.rglob('*')):
        raise ValueError('Managed skill copies cannot contain links')
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob('*'))
        if path.is_file() and path.name != COPY_RECORD
    }


def copy_record(source):
    return {'source': str(source.resolve()), 'files': skill_hashes(source)}


def install_copy(args, source, destination):
    expected = copy_record(source)
    existing = destination.exists() or destination.is_symlink()
    previous = None
    if existing:
        record = destination / COPY_RECORD
        if destination.is_symlink() or not record.is_file() or record.is_symlink():
            raise ValueError(f'Refusing to replace an unowned skill: {destination}')
        previous = json.loads(record.read_text())
        if previous.get('source') != expected['source'] or previous.get('files') != skill_hashes(
            destination
        ):
            raise ValueError(
                f'Installed skill was modified; preserve it before updating: {destination}'
            )
    if args.remove:
        action = 'remove' if existing else 'absent'
        if existing and not args.dry_run:
            shutil.rmtree(destination)
    else:
        action = 'unchanged' if previous == expected else 'update' if existing else 'install'
        if action != 'unchanged' and not args.dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            staging = destination.with_name('.computer-artist-install-' + uuid.uuid4().hex)
            backup = destination.with_name('.computer-artist-backup-' + uuid.uuid4().hex)
            try:
                shutil.copytree(source, staging)
                atomic_json(staging / COPY_RECORD, expected)
                if existing:
                    os.replace(destination, backup)
                try:
                    os.replace(staging, destination)
                except BaseException:
                    if backup.exists():
                        os.replace(backup, destination)
                    raise
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
            if backup.exists():
                shutil.rmtree(backup)
    return {
        'ok': True,
        'action': action,
        'dry_run': args.dry_run,
        'destination': str(destination),
        'source': str(source),
        'mode': 'managed_copy',
        'note': 'Available on the next agent turn. Re-run setup after updates; modified copies are preserved.',
    }


def add_command(commands):
    parser = commands.add_parser('setup', help='Install the Computer Artist agent skill')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument(
        '--codex', action='store_true', help='Use ~/.agents/skills (global Codex skills)'
    )
    target.add_argument(
        '--skills-dir', type=Path, help='Custom skills parent, e.g. ~/.codex/skills for older hosts'
    )
    parser.add_argument(
        '--dry-run', action='store_true', help='Report changes without writing anything'
    )
    parser.add_argument(
        '--remove', action='store_true', help="Remove only this checkout's installed skill link"
    )


def install(args):
    from .config import skill_source

    source = skill_source()
    root = args.skills_dir if args.skills_dir is not None else Path.home() / '.agents/skills'
    destination = root.expanduser().absolute() / 'computer-artist'
    if os.name == 'nt' and not destination.is_symlink():
        if not (source / 'SKILL.md').is_file():
            raise ValueError(f'Bundled skill is missing: {source}')
        return install_copy(args, source, destination)
    owned = destination.is_symlink() and destination.resolve() == source.resolve()
    exists = destination.exists() or destination.is_symlink()
    if exists and not owned:
        raise ValueError(
            f'Refusing to replace an existing skill: {destination}. Choose another --skills-dir or move it yourself.'
        )
    if args.remove:
        action = 'remove' if owned else 'absent'
        if owned and not args.dry_run:
            destination.unlink()
    else:
        if not (source / 'SKILL.md').is_file():
            raise ValueError(f'Bundled skill is missing: {source}')
        action = 'unchanged' if owned else 'install'
        if not owned and not args.dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source, target_is_directory=True)
    return {
        'ok': True,
        'action': action,
        'dry_run': args.dry_run,
        'destination': str(destination),
        'source': str(source),
        'note': 'Skill links follow installation updates. Keep the checkout or Python environment in place; reload skills or start a new agent session.',
    }
