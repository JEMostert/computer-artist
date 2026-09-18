"""Install the bundled skill without connecting to the desktop."""
from pathlib import Path


def add_command(commands):
    parser = commands.add_parser('setup', help='Install the Computer Artist agent skill')
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument('--codex', action='store_true', help='Use ~/.agents/skills (global Codex skills)')
    target.add_argument('--skills-dir', type=Path, help='Custom skills parent, e.g. ~/.codex/skills for older hosts')
    parser.add_argument('--dry-run', action='store_true', help='Report changes without writing anything')
    parser.add_argument('--remove', action='store_true', help='Remove only this checkout\'s installed skill link')


def install(args):
    source = Path(__file__).resolve().parents[1]/'skills/computer-artist'
    root = args.skills_dir if args.skills_dir is not None else Path.home()/'.agents/skills'
    destination = root.expanduser().absolute()/'computer-artist'
    owned = destination.is_symlink() and destination.resolve() == source
    exists = destination.exists() or destination.is_symlink()
    if exists and not owned:
        raise ValueError(f'Refusing to replace an existing skill: {destination}. Choose another --skills-dir or move it yourself.')
    if args.remove:
        action = 'remove' if owned else 'absent'
        if owned and not args.dry_run:
            destination.unlink()
    else:
        if not (source/'SKILL.md').is_file():
            raise ValueError(f'Bundled skill is missing: {source}')
        action = 'unchanged' if owned else 'install'
        if not owned and not args.dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.symlink_to(source, target_is_directory=True)
    return {'ok': True, 'action': action, 'dry_run': args.dry_run,
            'destination': str(destination), 'source': str(source),
            'note': 'Skill links follow checkout updates. Keep this checkout in place; reload skills or start a new agent session.'}
