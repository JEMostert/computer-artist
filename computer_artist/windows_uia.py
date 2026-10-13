"""Windows UI Automation: read a window's elements and act through control patterns.

Patterns run inside the application: no SendInput, no shared pointer, no foreground
change and no clipboard. Each application implements patterns itself, so a pattern
call is a dispatch, not an outcome; verify the result. Element output matches the
Linux AT-SPI reader (role, name, states, interfaces, window-content bounds).
"""

import time

TEXT_LIMIT = 4096
# UIA control type names (from UIA_<Name>ControlTypeId) in AT-SPI vocabulary, so
# fragments that find elements by role read the same on both platforms.
ROLES = {
    'Button': 'push button',
    'Calendar': 'calendar',
    'CheckBox': 'check box',
    'ComboBox': 'combo box',
    'Edit': 'text',
    'Hyperlink': 'link',
    'Image': 'image',
    'ListItem': 'list item',
    'List': 'list',
    'Menu': 'menu',
    'MenuBar': 'menu bar',
    'MenuItem': 'menu item',
    'ProgressBar': 'progress bar',
    'RadioButton': 'radio button',
    'ScrollBar': 'scroll bar',
    'Slider': 'slider',
    'Spinner': 'spin button',
    'StatusBar': 'status bar',
    'Tab': 'page tab list',
    'TabItem': 'page tab',
    'Text': 'label',
    'ToolBar': 'tool bar',
    'ToolTip': 'tool tip',
    'Tree': 'tree',
    'TreeItem': 'tree item',
    'Custom': 'unknown',
    'Group': 'panel',
    'Thumb': 'unknown',
    'DataGrid': 'table',
    'DataItem': 'table row',
    'Document': 'document text',
    'SplitButton': 'push button',
    'Window': 'frame',
    'Pane': 'panel',
    'Header': 'header',
    'HeaderItem': 'table column header',
    'Table': 'table',
    'TitleBar': 'title bar',
    'Separator': 'separator',
    'AppBar': 'tool bar',
}
# Pattern name -> (generated interface name, availability property name).
PATTERNS = {
    'invoke': ('IUIAutomationInvokePattern', 'IsInvokePatternAvailable'),
    'toggle': ('IUIAutomationTogglePattern', 'IsTogglePatternAvailable'),
    'selection item': ('IUIAutomationSelectionItemPattern', 'IsSelectionItemPatternAvailable'),
    'expand collapse': ('IUIAutomationExpandCollapsePattern', 'IsExpandCollapsePatternAvailable'),
    'value': ('IUIAutomationValuePattern', 'IsValuePatternAvailable'),
    'text': ('IUIAutomationTextPattern', 'IsTextPatternAvailable'),
    'scroll': ('IUIAutomationScrollPattern', 'IsScrollPatternAvailable'),
    'legacy': ('IUIAutomationLegacyIAccessiblePattern', 'IsLegacyIAccessiblePatternAvailable'),
}
PATTERN_IDS = {
    'invoke': 'InvokePattern',
    'toggle': 'TogglePattern',
    'selection item': 'SelectionItemPattern',
    'expand collapse': 'ExpandCollapsePattern',
    'value': 'ValuePattern',
    'text': 'TextPattern',
    'scroll': 'ScrollPattern',
    'legacy': 'LegacyIAccessiblePattern',
}
SCROLL_SMALL_DECREMENT, SCROLL_NO_AMOUNT, SCROLL_SMALL_INCREMENT = 1, 2, 4
# Wayland axis units per wheel notch, the unit ca scroll deltas use on every backend.
UNITS_PER_NOTCH = 15


class Unavailable(ValueError):
    """UI Automation cannot be used here (comtypes missing or COM refused)."""


class Automation:
    def __init__(self):
        try:
            import comtypes
            import comtypes.client
        except ImportError as error:
            raise Unavailable(
                'Windows UI Automation needs comtypes; reinstall computer-artist on Windows'
            ) from error
        self.COMError = comtypes.COMError
        try:
            self.uia = comtypes.client.GetModule('UIAutomationCore.dll')
            self.automation = comtypes.client.CreateObject(
                self.uia.CUIAutomation, interface=self.uia.IUIAutomation
            )
            self.walker = self.automation.ControlViewWalker
        except (OSError, comtypes.COMError) as error:
            raise Unavailable(f'UI Automation is unavailable: {error}') from error
        self.roles = {
            getattr(self.uia, name): name[4 : -len('ControlTypeId')]
            for name in dir(self.uia)
            if name.startswith('UIA_') and name.endswith('ControlTypeId')
        }

    # Elements

    def from_handle(self, hwnd):
        return self.automation.ElementFromHandle(hwnd)

    def from_point(self, x, y):
        return self.automation.ElementFromPoint(self.uia.tagPOINT(round(x), round(y)))

    def available(self, element, pattern):
        name = 'UIA_' + PATTERNS[pattern][1] + 'PropertyId'
        try:
            return bool(element.GetCurrentPropertyValue(getattr(self.uia, name)))
        except self.COMError:
            return False

    def pattern(self, element, pattern):
        if not self.available(element, pattern):
            return None
        unknown = element.GetCurrentPattern(getattr(self.uia, f'UIA_{PATTERN_IDS[pattern]}Id'))
        if not unknown:
            return None
        return unknown.QueryInterface(getattr(self.uia, PATTERNS[pattern][0]))

    def children(self, element):
        child = self.walker.GetFirstChildElement(element)
        while child:
            yield child
            child = self.walker.GetNextSiblingElement(child)

    def parent(self, element):
        return self.walker.GetParentElement(element)

    def text(self, element):
        value = self.pattern(element, 'value')
        if value is not None:
            return str(value.CurrentValue or '')[:TEXT_LIMIT]
        text = self.pattern(element, 'text')
        if text is not None:
            return str(text.DocumentRange.GetText(TEXT_LIMIT) or '')
        return None

    def describe(self, element, origin, depth):
        rect = element.CurrentBoundingRectangle
        width, height = rect.right - rect.left, rect.bottom - rect.top
        interfaces = [name for name in PATTERNS if self.available(element, name)]
        states = []
        if element.CurrentIsEnabled:
            states += ['enabled', 'sensitive']
        if element.CurrentIsKeyboardFocusable:
            states.append('focusable')
        if element.CurrentHasKeyboardFocus:
            states.append('focused')
        if not element.CurrentIsOffscreen and width > 0 and height > 0:
            states += ['showing', 'visible']
        if 'toggle' in interfaces and self.pattern(element, 'toggle').CurrentToggleState == 1:
            states.append('checked')
        if 'expand collapse' in interfaces:
            expanded = self.pattern(element, 'expand collapse').CurrentExpandCollapseState
            states.append({0: 'collapsed', 1: 'expanded', 2: 'expanded'}.get(expanded, 'leaf'))
        if (
            'selection item' in interfaces
            and self.pattern(element, 'selection item').CurrentIsSelected
        ):
            states.append('selected')
        if 'value' in interfaces:
            states.append(
                'read only' if self.pattern(element, 'value').CurrentIsReadOnly else 'editable'
            )
        kind = self.roles.get(element.CurrentControlType, 'Custom')
        result = {
            'path': '.'.join(str(part) for part in (element.GetRuntimeId() or ())),
            'role': ROLES.get(kind, kind.lower()),
            'uia_control_type': kind,
            'name': element.CurrentName or '',
            'automation_id': element.CurrentAutomationId or '',
            'class': element.CurrentClassName or '',
            'states': states,
            'interfaces': interfaces,
            'depth': depth,
            'text': self.text(element) if {'value', 'text'} & set(interfaces) else None,
            'x': None,
            'y': None,
            'width': None,
            'height': None,
            'center': None,
        }
        if width > 0 and height > 0:
            x, y = rect.left - origin[0], rect.top - origin[1]
            result.update(
                x=x, y=y, width=width, height=height, center=[x + width / 2, y + height / 2]
            )
        return result

    def elements(self, hwnd, origin, *, role=None, name=None, max_nodes=2000, max_depth=32,
                 deadline=None):  # fmt: skip
        """Breadth-limited walk of the window's control view in window-content coordinates."""
        found, nodes, truncated = [], 0, False
        stack = [(self.from_handle(hwnd), 0)]
        while stack:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError('UI Automation walk exceeded the deadline')
            element, depth = stack.pop()
            if nodes >= max_nodes:
                truncated = True
                break
            nodes += 1
            try:
                info = self.describe(element, origin, depth)
                if depth + 1 < max_depth:
                    kids = list(self.children(element))
                else:
                    kids = []
                    truncated = truncated or bool(self.walker.GetFirstChildElement(element))
            except self.COMError:
                continue  # The element vanished during the walk.
            if (role is None or info['role'] == role or info['uia_control_type'] == role) and (
                name is None or name.casefold() in info['name'].casefold()
            ):
                found.append(info)
            stack.extend((kid, depth + 1) for kid in reversed(kids))
        return {'elements': found, 'nodes': nodes, 'truncated': truncated}

    # Actions

    def activate(self, element):
        """The element's primary action; returns its name or raises without fallback."""
        invoke = self.pattern(element, 'invoke')
        if invoke is not None:
            invoke.Invoke()
            return 'invoke'
        toggle = self.pattern(element, 'toggle')
        if toggle is not None:
            toggle.Toggle()
            return 'toggle'
        item = self.pattern(element, 'selection item')
        if item is not None:
            item.Select()
            return 'select'
        expand = self.pattern(element, 'expand collapse')
        if expand is not None:
            if expand.CurrentExpandCollapseState == 0:
                expand.Expand()
                return 'expand'
            expand.Collapse()
            return 'collapse'
        if self.editable(element):
            return 'text_target'
        legacy = self.pattern(element, 'legacy')
        if legacy is not None and legacy.CurrentDefaultAction:
            legacy.DoDefaultAction()
            return 'default_action'
        raise ValueError(
            'The element under the agent pointer has no supported UI Automation action; '
            'no host fallback'
        )

    def editable(self, element):
        value = self.pattern(element, 'value')
        return value is not None and not value.CurrentIsReadOnly

    def value(self, element):
        value = self.pattern(element, 'value')
        if value is None:
            raise ValueError('Selected element does not expose an editable value; no host fallback')
        return value

    def append(self, element, text):
        """Append to the element's value; UIA has no caret insertion for arbitrary controls."""
        value = self.value(element)
        if value.CurrentIsReadOnly:
            raise ValueError('The selected element is read-only')
        value.SetValue(str(value.CurrentValue or '') + text)

    def backspace(self, element):
        value = self.value(element)
        current = str(value.CurrentValue or '')
        if current:
            value.SetValue(current[:-1])

    def select_text_at(self, element, x, y):
        """Place the caret at a screen point through the text pattern; False if unsupported."""
        text = self.pattern(element, 'text')
        if text is None:
            return False
        try:
            # RangeFromPoint returns the empty range (a caret) nearest to the point.
            text.RangeFromPoint(self.uia.tagPOINT(round(x), round(y))).Select()
        except self.COMError:
            return False
        return True

    def scroll(self, element, delta, axis):
        """Scroll the nearest scrollable ancestor by wheel notches; positive is down/right."""
        notches = max(1, round(abs(delta) / UNITS_PER_NOTCH))
        step = SCROLL_SMALL_INCREMENT if delta > 0 else SCROLL_SMALL_DECREMENT
        current = element
        while current:
            pattern = self.pattern(current, 'scroll')
            if pattern is not None and (
                pattern.CurrentVerticallyScrollable
                if axis == 'vertical'
                else pattern.CurrentHorizontallyScrollable
            ):
                for _ in range(notches):
                    if axis == 'vertical':
                        pattern.Scroll(SCROLL_NO_AMOUNT, step)
                    else:
                        pattern.Scroll(step, SCROLL_NO_AMOUNT)
                return notches
            current = self.parent(current)
        raise ValueError('Nothing scrollable under the agent pointer; no host fallback')
