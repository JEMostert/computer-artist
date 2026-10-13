"""Read-only diagnosis of native Windows support, without KWin build checks."""

from .client import Client


def diagnose_backend(args, store, checks, warnings, check):
    lane = getattr(args, 'lane', 'agent')
    backend, windows, selected = {}, [], None
    try:
        with Client(
            'win32', lane=lane, deadline=min(args.deadline, 5), window_dir=store.root
        ) as client:
            backend = client.request('capabilities')
            windows = client.windows()
            client.native_backend.secure_desktop()
        check('win32_backend', True, {'backend': 'win32', 'protocol': backend['protocol']})
        warnings.extend(
            {'code': 'platform_limit', 'detail': message} for message in backend['limitations']
        )
        if args.window:
            identity = store.resolve_window(args.window.strip('{}'))
            selected = next((w for w in windows if w['id'] == identity), None)
            check(
                'selected_window',
                selected is not None and selected.get('acquirable', False),
                selected,
                "Inspect the exact window's restrictions; independent input needs supported controls and human focus elsewhere.",
            )
        stale = [
            name
            for name, identity in store.names().items()
            if identity not in {w['id'] for w in windows}
        ]
        if stale:
            warnings.append(
                {
                    'code': 'stale_bindings',
                    'names': stale,
                    'remedy': 'Use ca set to bind an exact current window.',
                }
            )
        if args.build:
            check('windows_native_api', True, 'Win32 ctypes backend; no KWin plugin build required')
    except Exception as error:
        check('win32_backend', False, str(error), 'Use an unlocked interactive Windows session.')
    return {
        'ok': all(c['passed'] for c in checks),
        'command': 'doctor',
        'lane': lane,
        'checks': checks,
        'warnings': warnings,
        'backend': backend,
        'windows': windows,
        'selected_window': selected,
        'note': 'Read-only diagnostics; host input compatibility must be verified in the target app.',
    }
