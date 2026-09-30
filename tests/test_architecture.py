"""Guard module boundaries so future features do not reconnect the CLI to the core."""

import ast
import unittest
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / 'computer_artist'


def dependencies():
    graph = {}
    for path in PACKAGE.glob('*.py'):
        targets = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module:
                targets.add(node.module.split('.')[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith('computer_artist.'):
                    targets.add(node.module.split('.')[1])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith('computer_artist.'):
                        targets.add(alias.name.split('.')[1])
        graph[path.stem] = targets
    return graph


class ArchitectureTest(unittest.TestCase):
    def test_internal_dependencies_are_acyclic(self):
        graph = dependencies()

        def visit(module, stack):
            self.assertNotIn(module, stack, f'Circular module imports: {stack + [module]}')
            if module not in visited:
                for dependency in graph[module]:
                    self.assertIn(dependency, graph, f'{module} imports missing {dependency}')
                    visit(dependency, stack + [module])
                visited.add(module)

        visited = set()
        for module in graph:
            visit(module, [])

    def test_core_modules_do_not_import_command_or_process_entrypoints(self):
        graph = dependencies()
        entrypoints = {'cli', 'commands', 'worker', 'supervisor', '__main__'}
        core = set(graph) - entrypoints
        for module in core:
            with self.subTest(module=module):
                self.assertFalse(graph[module] & entrypoints)

    def test_storage_and_contracts_are_independent_of_desktop_control(self):
        graph = dependencies()
        desktop = {'client', 'runtime', 'lanes', 'observations', 'watch', 'programs'}
        for module in (
            'files',
            'config',
            'errors',
            'input',
            'contracts',
            'fragments',
            'workspace',
            'storage',
            'artifacts',
            'records',
        ):
            with self.subTest(module=module):
                self.assertFalse(graph[module] & desktop)

    def test_existing_fragment_image_helper_import_is_preserved(self):
        from PIL import Image

        from computer_artist.runtime import image_box

        image = Image.new('RGB', (200, 160))
        self.assertEqual(
            image_box([10, 20, 30, 40], {'width': 100, 'height': 80}, image), (20, 40, 80, 120)
        )
