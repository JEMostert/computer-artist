"""Replace and verify text in an already-open saved KWrite document."""

CONTRACT = {
    'lane': 'host',
    'requires': ['focus', 'key', 'clipboard_set'],
    'window': {'title_contains': 'KWrite'},
}


def run(ctx, path: str, text: str):
    """The editor field must be selected and path must be this document's saved file."""
    before = ctx.snapshot_file(path)
    if not before['exists']:
        raise ValueError(
            'Open an already-saved document; this fragment does not handle save dialogs'
        )
    ctx.press('Ctrl+A')
    ctx.paste(text)
    ctx.press('Ctrl+S')
    checked = ctx.verify_file(
        'Saved KWrite document contains the exact requested text',
        path,
        kind='text',
        after=before,
        expected_text=text,
        min_bytes=0,
    )
    return {'file': checked['evidence']['path'], 'sha256': checked['evidence']['sha256']}
