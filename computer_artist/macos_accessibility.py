"""Bounded, target-window macOS accessibility reads without app activation."""

import ctypes
import time

ROLES = {
    'AXButton': 'push button',
    'AXCheckBox': 'check box',
    'AXRadioButton': 'radio button',
    'AXTextField': 'text',
    'AXTextArea': 'text',
    'AXStaticText': 'label',
    'AXLink': 'link',
    'AXWindow': 'frame',
    'AXGroup': 'panel',
    'AXScrollArea': 'scroll pane',
    'AXWebArea': 'document web',
    'AXImage': 'image',
    'AXList': 'list',
    'AXRow': 'table row',
    'AXTable': 'table',
    'AXOutline': 'tree',
    'AXSlider': 'slider',
    'AXMenu': 'menu',
    'AXMenuItem': 'menu item',
    'AXMenuBar': 'menu bar',
    'AXTabGroup': 'page tab list',
    'AXPopUpButton': 'combo box',
    'AXComboBox': 'combo box',
    'AXToolbar': 'tool bar',
    'AXProgressIndicator': 'progress bar',
    'AXScrollBar': 'scroll bar',
}


class Accessibility:
    def __init__(self):
        try:
            import ApplicationServices as ax

            lib = ctypes.CDLL(
                '/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices'
            )
            get_window = lib._AXUIElementGetWindow
            get_window.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            get_window.restype = ctypes.c_int32
            get_value = lib.AXValueGetValue
            get_value.argtypes = [ctypes.c_void_p, ctypes.c_int32, ctypes.c_void_p]
            get_value.restype = ctypes.c_bool
        except (ImportError, OSError, AttributeError) as error:
            raise ValueError(f'macOS accessibility APIs unavailable: {error}') from error
        if not ax.AXIsProcessTrusted():
            raise ValueError(
                'macOS Accessibility permission is required; grant it in System Settings'
            )
        self.ax = ax
        self._lib, self._get_window, self._get_value = lib, get_window, get_value

    def _bounded(self, element):
        if self.ax.AXUIElementSetMessagingTimeout(element, 0.2):
            raise ValueError('Could not bound the accessibility messaging timeout')
        return element

    def attr(self, element, name, default=None):
        self._bounded(element)
        error, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        return default if error or value is None else value

    def app(self, pid, enable=False):
        app = self._bounded(self.ax.AXUIElementCreateApplication(int(pid)))
        if enable:
            # Chrome's opt-in is scoped to this selected process and never activates it.
            self.ax.AXUIElementSetAttributeValue(app, 'AXManualAccessibility', True)
        return app

    def window_id(self, element):
        result = ctypes.c_uint32()
        error = self._get_window(element.__c_void_p__(), ctypes.byref(result))
        return int(result.value) if error == 0 and result.value else None

    def window(self, pid, wid):
        app = self.app(pid)
        error, windows = self.ax.AXUIElementCopyAttributeValues(app, 'AXWindows', 0, 2048, None)
        if not error:
            for window in windows or ():
                if self.window_id(window) == int(wid):
                    return self._bounded(window)
        raise ValueError(f'Window {wid} is not exposed by accessibility for process {pid}')

    def frame(self, element):
        position, size = self.attr(element, 'AXPosition'), self.attr(element, 'AXSize')
        if position is None or size is None:
            raise ValueError('Accessibility element has no frame')
        point, extent = (ctypes.c_double * 2)(), (ctypes.c_double * 2)()
        if not self._get_value(position.__c_void_p__(), 1, point) or not self._get_value(
            size.__c_void_p__(), 2, extent
        ):
            raise ValueError('Accessibility element has an unreadable frame')
        return point[0], point[1], extent[0], extent[1]

    def focused_window(self, pid):
        window = self.attr(self.app(pid), 'AXFocusedWindow')
        return None if window is None else self.window_id(window)

    def prepare_keyboard(self, pid, wid):
        target = self.window(pid, wid)
        if self.focused_window(pid) == int(wid):
            return
        error = self.ax.AXUIElementSetAttributeValue(target, 'AXMain', True)
        if error or self.focused_window(pid) != int(wid):
            raise ValueError('Target window is not the app keyboard window; no focus fallback')

    def _children(self, element, limit):
        self._bounded(element)
        error, children = self.ax.AXUIElementCopyAttributeValues(
            element, 'AXChildren', 0, limit, None
        )
        return [] if error else list(children or ())

    def _describe(self, element, origin, depth, path):
        raw_role = str(self.attr(element, 'AXRole', 'AXUnknown'))
        role = ROLES.get(raw_role, raw_role.removeprefix('AX').lower())
        name = self.attr(element, 'AXTitle') or self.attr(element, 'AXDescription', '')
        value = self.attr(element, 'AXValue')
        states = []
        for attribute, state in (
            ('AXEnabled', 'enabled'),
            ('AXFocused', 'focused'),
            ('AXSelected', 'selected'),
        ):
            if self.attr(element, attribute, False):
                states.append(state)
        if 'enabled' in states:
            states.append('sensitive')
        if role in ('check box', 'radio button') and value == 1:
            states.append('checked')
        expanded = self.attr(element, 'AXExpanded')
        if expanded is not None:
            states.append('expanded' if expanded else 'collapsed')
        result = {
            'path': path,
            'role': role,
            'ax_role': raw_role,
            'name': str(name)[:1000],
            'states': states,
            'interfaces': [],
            'depth': depth,
            'text': str(value)[:1000] if isinstance(value, (str, int, float)) else None,
            'x': None,
            'y': None,
            'width': None,
            'height': None,
            'center': None,
        }
        try:
            x, y, width, height = self.frame(element)
        except ValueError:
            return result
        if width > 0 and height > 0:
            x, y = x - origin[0], y - origin[1]
            result.update(
                x=x, y=y, width=width, height=height, center=[x + width / 2, y + height / 2]
            )
            if not self.attr(element, 'AXHidden', False):
                states.extend(['showing', 'visible'])
        return result

    def elements(self, pid, wid, origin, *, role=None, name=None, max_nodes=2000,
                 max_depth=32, deadline=None):  # fmt: skip
        if not 1 <= max_nodes <= 2000 or not 1 <= max_depth <= 32:
            raise ValueError('AX walk limits must be 1..2000 nodes and 1..32 depth')
        deadline = min(deadline, time.monotonic() + 10) if deadline else time.monotonic() + 10
        self.app(pid, enable=True)
        root = self.window(pid, wid)
        found, nodes, truncated = [], 0, False
        stack = [(root, 0, '0')]
        while stack:
            if time.monotonic() >= deadline:
                raise TimeoutError('Accessibility walk exceeded the deadline')
            if nodes >= max_nodes:
                truncated = True
                break
            element, depth, path = stack.pop()
            nodes += 1
            info = self._describe(element, origin, depth, path)
            if (role is None or role in (info['role'], info['ax_role'])) and (
                name is None or name.casefold() in info['name'].casefold()
            ):
                found.append(info)
            remaining = max_nodes - nodes - len(stack)
            kids = self._children(element, max(1, remaining + 1))
            if depth + 1 >= max_depth:
                truncated = truncated or bool(kids)
                continue
            if len(kids) > remaining:
                truncated = True
                kids = kids[: max(0, remaining)]
            stack.extend(
                (kid, depth + 1, f'{path}.{i}') for i, kid in reversed(list(enumerate(kids)))
            )
        return {'elements': found, 'nodes': nodes, 'truncated': truncated}
