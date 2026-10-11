import time

REGISTRY = 'org.a11y.atspi.Registry'
REGISTRY_ROOT = '/org/a11y/atspi/accessible/root'
ACCESSIBLE = 'org.a11y.atspi.Accessible'
COMPONENT = 'org.a11y.atspi.Component'
TEXT = 'org.a11y.atspi.Text'
BUS_NAME = 'org.a11y.Bus'
BUS_PATH = '/org/a11y/bus'
COORD_WINDOW = 1  # SCREEN (0) is meaningless on Wayland
TEXT_LIMIT = 1000

# Index order of the AT-SPI state bit set (bit i of word i // 32).
STATE_NAMES = (
    'invalid active armed busy checked collapsed defunct editable enabled expandable expanded '
    'focusable focused has_tooltip horizontal iconified modal multi_line multiselectable opaque '
    'pressed resizable selectable selected sensitive showing single_line stale transient vertical '
    'visible manages_descendants indeterminate required truncated animated invalid_entry '
    'supports_autocompletion selectable_text is_default visited checkable has_popup read_only'
).split()


class AccessibilityUnavailable(RuntimeError):
    pass


class BusError(RuntimeError):
    """A single D-Bus call failed (error reply, timeout, vanished peer)."""


def _jeepney():
    try:
        import jeepney
        from jeepney.io import blocking
    except ImportError as error:
        raise AccessibilityUnavailable(
            'Install jeepney (pip install -e .) for accessibility'
        ) from error
    return jeepney, blocking


class Bus:
    """Thin jeepney wrapper; the only place that touches D-Bus. Tests replace it."""

    def __init__(self, session_address=None, *, timeout=2.0):
        self.session_address = session_address or 'SESSION'
        self.timeout = timeout
        self._session = None
        self._a11y = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for connection in (self._session, self._a11y):
            if connection is not None:
                connection.close()
        self._session = self._a11y = None

    def _session_connection(self):
        if self._session is None:
            _, blocking = _jeepney()
            try:
                self._session = blocking.open_dbus_connection(self.session_address)
            except (OSError, KeyError, RuntimeError) as error:
                raise AccessibilityUnavailable(
                    f'Cannot reach the session bus for {BUS_NAME}: {error}'
                ) from error
        return self._session

    def _a11y_connection(self):
        if self._a11y is None:
            jeepney, blocking = _jeepney()
            session = self._session_connection()
            address = jeepney.DBusAddress(BUS_PATH, bus_name=BUS_NAME, interface=BUS_NAME)
            try:
                reply = self._send(session, jeepney.new_method_call(address, 'GetAddress'))
                self._a11y = blocking.open_dbus_connection(reply[0])
            except (BusError, OSError, RuntimeError) as error:
                raise AccessibilityUnavailable(
                    f'Cannot reach the accessibility bus via {BUS_NAME}: {error}'
                ) from error
        return self._a11y

    def _send(self, connection, message):
        jeepney, _ = _jeepney()
        try:
            return jeepney.wrappers.unwrap_msg(
                connection.send_and_get_reply(message, timeout=self.timeout)
            )
        except jeepney.DBusErrorResponse as error:
            raise BusError(str(error)) from error
        except TimeoutError as error:
            raise BusError('D-Bus call timed out') from error
        except OSError as error:
            raise BusError(f'D-Bus connection failed: {error}') from error

    def _status_address(self, jeepney):
        return jeepney.DBusAddress(BUS_PATH, bus_name=BUS_NAME, interface='org.a11y.Status')

    def status(self):
        jeepney, _ = _jeepney()
        session = self._session_connection()
        result = {}
        for key, name in (('enabled', 'IsEnabled'), ('screen_reader', 'ScreenReaderEnabled')):
            try:
                body = self._send(
                    session, jeepney.Properties(self._status_address(jeepney)).get(name)
                )
                result[key] = bool(body[0][1])
            except BusError:
                result[key] = None
        return result

    def set_enabled(self, value):
        jeepney, _ = _jeepney()
        properties = jeepney.Properties(self._status_address(jeepney))
        self._send(self._session_connection(), properties.set('IsEnabled', 'b', bool(value)))

    def applications(self):
        body = self.call(REGISTRY, REGISTRY_ROOT, ACCESSIBLE, 'GetChildren')
        return [(name, path) for name, path in body[0]]

    def pid(self, bus_name):
        jeepney, _ = _jeepney()
        address = jeepney.DBusAddress(
            '/org/freedesktop/DBus',
            bus_name='org.freedesktop.DBus',
            interface='org.freedesktop.DBus',
        )
        message = jeepney.new_method_call(address, 'GetConnectionUnixProcessID', 's', (bus_name,))
        try:
            return int(self._send(self._a11y_connection(), message)[0])
        except BusError:
            return None  # the application vanished between listing and lookup

    def call(self, bus_name, path, interface, method, signature=None, args=()):
        jeepney, _ = _jeepney()
        address = jeepney.DBusAddress(path, bus_name=bus_name, interface=interface)
        message = jeepney.new_method_call(address, method, signature, args)
        return tuple(self._send(self._a11y_connection(), message))

    def get_property(self, bus_name, path, interface, name):
        jeepney, _ = _jeepney()
        address = jeepney.DBusAddress(path, bus_name=bus_name, interface=interface)
        body = self._send(self._a11y_connection(), jeepney.Properties(address).get(name))
        return body[0][1]


def status(*, session_address=None, bus=None):
    owned = bus is None
    bus = bus or Bus(session_address)
    result = {'available': False, 'enabled': None, 'screen_reader': None}
    try:
        result.update(bus.status())
        bus.applications()  # proves the accessibility bus itself is reachable
        result['available'] = True
    except (AccessibilityUnavailable, BusError) as error:
        result['error'] = str(error)
    finally:
        if owned:
            bus.close()
    return result


def enable(*, session_address=None, bus=None):
    owned = bus is None
    bus = bus or Bus(session_address)
    try:
        try:
            bus.set_enabled(True)
        except BusError as error:
            raise AccessibilityUnavailable(
                f'Cannot set IsEnabled on {BUS_NAME}: {error}'
            ) from error
        return status(bus=bus)
    finally:
        if owned:
            bus.close()


def decode_states(words):
    names = []
    for index in range(len(words) * 32):
        if words[index // 32] >> (index % 32) & 1:
            names.append(STATE_NAMES[index] if index < len(STATE_NAMES) else f'unknown_{index}')
    return names


def elements(
    pid,
    title,
    *,
    role=None,
    name=None,
    max_depth=32,
    max_nodes=2000,
    deadline=None,
    session_address=None,
    bus=None,
):
    owned = bus is None
    bus = bus or Bus(session_address)
    try:
        # The walk recurses; keep it far from Python's recursion limit.
        max_depth = min(max_depth, 128)
        return _elements(bus, pid, title, role, name, max_depth, max_nodes, deadline)
    finally:
        if owned:
            bus.close()


def _elements(bus, pid, title, role, name, max_depth, max_nodes, deadline):
    def guard():
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError('Accessibility tree walk exceeded the deadline')

    def accessible(bus_name, path, method):
        guard()
        return bus.call(bus_name, path, ACCESSIBLE, method)[0]

    def node_name(bus_name, path):
        guard()
        return bus.get_property(bus_name, path, ACCESSIBLE, 'Name')

    result = {
        'application': None,
        'frame': None,
        'frames': [],
        'elements': [],
        'nodes': 0,
        'truncated': False,
    }
    try:
        guard()
        applications = bus.applications()
        app = None
        for bus_name, path in applications:
            guard()
            if bus.pid(bus_name) == pid:
                app = (bus_name, path)
                break
    except AccessibilityUnavailable as error:
        raise AccessibilityUnavailable(
            f'{error}; run `ca a11y --enable`, then restart the application if needed'
        ) from error
    except BusError as error:
        raise AccessibilityUnavailable(
            f'Cannot read the accessibility registry from {BUS_NAME}: {error}; '
            'run `ca a11y --enable`, then restart the application if needed'
        ) from error
    if app is None:
        return result
    app_name, app_path = app
    result['application'] = {'bus_name': app_name, 'pid': pid}

    top = []  # (name, path, accessible name) of the application's top-level children
    try:
        children = accessible(app_name, app_path, 'GetChildren')
    except BusError:
        # The application vanished or stopped answering between lookup and walk.
        return result
    for child_name, child_path in children:
        try:
            top.append((child_name, child_path, node_name(child_name, child_path)))
        except BusError:
            continue
    result['frames'] = [label for _, _, label in top]
    chosen = [entry for entry in top if entry[2] == title][:1]
    if not chosen and len(top) == 1:
        chosen = top
    if not chosen:
        return result
    frame_name, frame_path, frame_label = chosen[0]
    try:
        frame_role = accessible(frame_name, frame_path, 'GetRoleName')
        children = accessible(frame_name, frame_path, 'GetChildren')
    except BusError:
        return result
    result['frame'] = {'name': frame_label, 'role': frame_role}

    found = result['elements']

    def read(bus_name, path, depth):
        guard()
        interfaces = [
            item.removeprefix('org.a11y.atspi.').lower()
            for item in accessible(bus_name, path, 'GetInterfaces')
        ]
        element = {
            'path': path,
            'role': accessible(bus_name, path, 'GetRoleName'),
            'name': node_name(bus_name, path),
            'states': decode_states(accessible(bus_name, path, 'GetState')),
            'interfaces': interfaces,
            'depth': depth,
            'text': None,
            'x': None,
            'y': None,
            'width': None,
            'height': None,
            'center': None,
        }
        if 'component' in interfaces:
            guard()
            x, y, width, height = bus.call(
                bus_name, path, COMPONENT, 'GetExtents', 'u', (COORD_WINDOW,)
            )[0]
            if width > 0 and height > 0:
                element.update(
                    x=x, y=y, width=width, height=height, center=[x + width / 2, y + height / 2]
                )
        if 'text' in interfaces:
            guard()
            element['text'] = bus.call(bus_name, path, TEXT, 'GetText', 'ii', (0, -1))[0][
                :TEXT_LIMIT
            ]
        return element, accessible(bus_name, path, 'GetChildren')

    def walk(bus_name, path, depth):
        if depth >= max_depth or result['nodes'] >= max_nodes:
            result['truncated'] = True
            return
        result['nodes'] += 1
        try:
            element, kids = read(bus_name, path, depth)
        except BusError:
            return  # defunct object: skip it and its subtree
        found.append(element)
        for kid_name, kid_path in kids:
            walk(kid_name, kid_path, depth + 1)

    for child_name, child_path in children:
        walk(child_name, child_path, 0)

    if role is not None:
        found[:] = [item for item in found if item['role'].lower() == role.lower()]
    if name is not None:
        found[:] = [item for item in found if name.lower() in item['name'].lower()]
    return result
