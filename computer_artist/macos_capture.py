"""Capture a selected macOS window without activating it or prompting permissions."""

import os


def capture(wid, path):
    try:
        import Quartz as q
        from Foundation import NSMutableData
    except ImportError as error:
        raise ValueError('macOS window capture needs PyObjC Quartz and Foundation') from error
    preflight = getattr(q, 'CGPreflightScreenCaptureAccess', None)
    if preflight is None or not preflight():
        raise ValueError(
            'macOS Screen Recording permission is required; grant it in System Settings'
        )
    create_image = getattr(q, 'CGWindowListCreateImage', None)
    if create_image is None:
        raise ValueError('CGWindowListCreateImage is unavailable on this macOS version')
    image = create_image(
        q.CGRectNull,
        q.kCGWindowListOptionIncludingWindow,
        int(wid),
        q.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        raise ValueError(f'Window {wid} cannot be captured (closed, hidden, or protected)')
    width, height = q.CGImageGetWidth(image), q.CGImageGetHeight(image)
    if width <= 0 or height <= 0:
        raise ValueError(f'Window {wid} produced an empty capture')
    data = NSMutableData.data()
    destination = q.CGImageDestinationCreateWithData(data, 'public.png', 1, None)
    if destination is None:
        raise ValueError(f'Cannot create PNG at {path}')
    q.CGImageDestinationAddImage(destination, image, None)
    if not q.CGImageDestinationFinalize(destination):
        raise ValueError(f'Could not save PNG at {path}')
    with open(path, 'xb') as stream:
        try:
            stream.write(bytes(data))
        except BaseException:
            stream.close()
            os.unlink(path)
            raise
    return {'width': int(width), 'height': int(height)}
