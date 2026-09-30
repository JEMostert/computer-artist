"""Save a known active text document through its shortcut and verify fresh bytes.

Select the correct existing document first. expected_text includes the app's
actual line endings, including KWrite's final newline. This does not identify
documents, resolve save dialogs or perform text editing.
"""

CONTRACT = {'requires': ['key']}


def run(ctx, path: str, expected_text: str):
    before = ctx.snapshot_file(path)
    ctx.press('Ctrl+S')
    ctx.verify_file(
        'Active text document saved to the requested path',
        path,
        kind='text',
        after=before,
        expected_text=expected_text,
        timeout=5,
    )
    return {'path': path}
