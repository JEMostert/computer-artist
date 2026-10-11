import sys
import time
import unittest
from unittest.mock import patch

from computer_artist import accessibility
from computer_artist.accessibility import (
    ACCESSIBLE,
    COMPONENT,
    TEXT,
    AccessibilityUnavailable,
    Bus,
    BusError,
)

APP = (':1.7', '/org/a11y/atspi/accessible/root')


def states(*indexes):
    words = [0, 0]
    for index in indexes:
        words[index // 32] |= 1 << (index % 32)
    return words


class FakeBus:
    """In-memory AT-SPI tree: nodes[path] = dict(role, name, children, ...)."""

    def __init__(self, nodes, apps=((APP, 4242),), unreachable=None):
        self.nodes = nodes
        self.apps = apps
        self.unreachable = unreachable
        self.closed = False
        self.calls = 0
        self.defunct = set()

    def status(self):
        if self.unreachable:
            raise self.unreachable
        return {'enabled': True, 'screen_reader': False}

    def set_enabled(self, value):
        if self.unreachable:
            raise self.unreachable
        self.enabled = value

    def applications(self):
        if self.unreachable:
            raise self.unreachable
        return [app for app, _ in self.apps]

    def pid(self, bus_name):
        return dict((app[0], pid) for app, pid in self.apps).get(bus_name)

    def get_property(self, bus_name, path, interface, name):
        self.calls += 1
        assert (interface, name) == (ACCESSIBLE, 'Name')
        return self.node(path)['name']

    def node(self, path):
        if path in self.defunct or path not in self.nodes:
            raise BusError(f'no such object {path}')
        return self.nodes[path]

    def call(self, bus_name, path, interface, method, signature=None, args=()):
        self.calls += 1
        if path == APP[1]:
            return ([(APP[0], child) for child in self.nodes['root']['children']],)
        node = self.node(path)
        if method == 'GetChildren':
            return ([(bus_name, child) for child in node['children']],)
        if method == 'GetRoleName':
            return (node['role'],)
        if method == 'GetInterfaces':
            return (node.get('interfaces', []),)
        if method == 'GetState':
            return (node.get('states', states()),)
        if (interface, method) == (COMPONENT, 'GetExtents'):
            assert args == (1,)  # WINDOW coordinates only
            return (node.get('extents', (0, 0, 0, 0)),)
        if (interface, method) == (TEXT, 'GetText'):
            assert args == (0, -1)
            return (node.get('text', ''),)
        raise AssertionError(method)

    def close(self):
        self.closed = True


def node(role, name, children=(), **extra):
    return {'role': role, 'name': name, 'children': list(children), **extra}


def tree(frames=None):
    nodes = {
        'root': {'children': ['/frame']},
        '/frame': node('frame', 'Notes', ['/button', '/group', '/entry']),
        '/button': node(
            'push button',
            'Save Button',
            interfaces=['org.a11y.atspi.Accessible', 'org.a11y.atspi.Component'],
            states=states(8, 11, 32, 33),
            extents=(10, 20, 100, 40),
        ),
        '/group': node('filler', 'Panel', ['/label']),
        '/label': node('label', 'save hint'),
        '/entry': node(
            'entry',
            'Title',
            interfaces=['org.a11y.atspi.Text', 'org.a11y.atspi.Component'],
            extents=(0, 0, 0, 0),
            text='x' * 1500,
        ),
    }
    nodes.update(frames or {})
    return nodes


class ElementsTest(unittest.TestCase):
    def run_elements(self, nodes=None, title='Notes', **kwargs):
        bus = FakeBus(nodes or tree())
        return bus, accessibility.elements(4242, title, bus=bus, **kwargs)

    def test_walks_frame_in_preorder_without_closing_given_bus(self):
        bus, result = self.run_elements()
        self.assertFalse(bus.closed)
        self.assertEqual(result['application'], {'bus_name': ':1.7', 'pid': 4242})
        self.assertEqual(result['frame'], {'name': 'Notes', 'role': 'frame'})
        self.assertEqual(result['frames'], ['Notes'])
        self.assertEqual(
            [(e['path'], e['depth']) for e in result['elements']],
            [('/button', 0), ('/group', 0), ('/label', 1), ('/entry', 0)],
        )
        self.assertEqual(result['nodes'], 4)
        self.assertFalse(result['truncated'])

    def test_unknown_pid_gives_no_application(self):
        bus = FakeBus(tree())
        result = accessibility.elements(1, 'Notes', bus=bus)
        self.assertIsNone(result['application'])
        self.assertEqual(result['elements'], [])

    def test_frame_selection_exact_title_beats_other_frames(self):
        nodes = tree({'root': {'children': ['/other', '/frame']}, '/other': node('frame', 'Other')})
        _, result = self.run_elements(nodes)
        self.assertEqual(result['frame']['name'], 'Notes')
        self.assertEqual(result['frames'], ['Other', 'Notes'])
        self.assertEqual(len(result['elements']), 4)

    def test_single_frame_fallback_ignores_title(self):
        _, result = self.run_elements(title='Renamed Window')
        self.assertEqual(result['frame']['name'], 'Notes')
        self.assertEqual(len(result['elements']), 4)

    def test_ambiguous_frames_give_no_frame(self):
        nodes = tree({'root': {'children': ['/other', '/frame']}, '/other': node('frame', 'Other')})
        _, result = self.run_elements(nodes, title='Missing')
        self.assertIsNone(result['frame'])
        self.assertEqual(result['elements'], [])
        self.assertEqual(result['frames'], ['Other', 'Notes'])

    def test_role_filter_is_case_insensitive_exact(self):
        _, result = self.run_elements(role='PUSH BUTTON')
        self.assertEqual([e['path'] for e in result['elements']], ['/button'])
        _, result = self.run_elements(role='push')
        self.assertEqual(result['elements'], [])

    def test_name_filter_is_substring_and_walks_filtered_parents(self):
        _, result = self.run_elements(name='SAVE')
        self.assertEqual([e['path'] for e in result['elements']], ['/button', '/label'])

    def test_state_decoding_across_words(self):
        _, result = self.run_elements(role='push button')
        found = result['elements'][0]['states']
        self.assertEqual(found, ['enabled', 'focusable', 'indeterminate', 'required'])
        self.assertEqual(accessibility.STATE_NAMES[32], 'indeterminate')

    def test_interfaces_are_stripped_and_lowercased(self):
        _, result = self.run_elements(role='push button')
        self.assertEqual(result['elements'][0]['interfaces'], ['accessible', 'component'])

    def test_geometry_and_center(self):
        _, result = self.run_elements()
        button, group, _, entry = result['elements']
        self.assertEqual(
            (button['x'], button['y'], button['width'], button['height']), (10, 20, 100, 40)
        )
        self.assertEqual(button['center'], [60.0, 40.0])
        for empty in (group, entry):  # no Component, or non-positive size
            self.assertEqual(
                (empty['x'], empty['y'], empty['width'], empty['height'], empty['center']),
                (None,) * 5,
            )

    def test_text_only_for_text_interface_and_truncated(self):
        _, result = self.run_elements()
        self.assertIsNone(result['elements'][0]['text'])
        self.assertEqual(result['elements'][3]['text'], 'x' * 1000)

    def test_defunct_node_skips_subtree_but_not_walk(self):
        bus = FakeBus(tree())
        bus.defunct.add('/group')
        result = accessibility.elements(4242, 'Notes', bus=bus)
        self.assertEqual([e['path'] for e in result['elements']], ['/button', '/entry'])
        self.assertFalse(result['truncated'])

    def test_max_nodes_truncates(self):
        _, result = self.run_elements(max_nodes=2)
        self.assertEqual([e['path'] for e in result['elements']], ['/button', '/group'])
        self.assertEqual(result['nodes'], 2)
        self.assertTrue(result['truncated'])

    def test_max_depth_truncates(self):
        _, result = self.run_elements(max_depth=1)
        self.assertEqual([e['path'] for e in result['elements']], ['/button', '/group', '/entry'])
        self.assertTrue(result['truncated'])

    def test_expired_deadline_raises_before_any_call(self):
        bus = FakeBus(tree())
        with self.assertRaisesRegex(TimeoutError, 'exceeded the deadline'):
            accessibility.elements(4242, 'Notes', bus=bus, deadline=time.monotonic() - 1)
        self.assertEqual(bus.calls, 0)

    def test_deadline_passing_mid_walk_raises(self):
        bus = FakeBus(tree())
        original = bus.call

        def slow(*args, **kwargs):
            if args[3] == 'GetRoleName' and args[1] == '/button':
                time.sleep(0.05)
            return original(*args, **kwargs)

        bus.call = slow
        with self.assertRaises(TimeoutError):
            accessibility.elements(4242, 'Notes', bus=bus, deadline=time.monotonic() + 0.03)

    def test_unreachable_bus_names_org_a11y_bus_and_hint(self):
        bus = FakeBus(tree(), unreachable=AccessibilityUnavailable('Cannot reach org.a11y.Bus'))
        with self.assertRaisesRegex(AccessibilityUnavailable, r'org\.a11y\.Bus.*ca a11y --enable'):
            accessibility.elements(4242, 'Notes', bus=bus)

    def test_owned_bus_is_created_and_closed(self):
        bus = FakeBus(tree())
        with patch.object(accessibility, 'Bus', return_value=bus) as factory:
            result = accessibility.elements(4242, 'Notes', session_address='unix:path=/x')
        factory.assert_called_once_with('unix:path=/x')
        self.assertTrue(bus.closed)
        self.assertEqual(len(result['elements']), 4)


class StatusTest(unittest.TestCase):
    def test_status_available(self):
        bus = FakeBus(tree())
        self.assertEqual(
            accessibility.status(bus=bus),
            {'available': True, 'enabled': True, 'screen_reader': False},
        )
        self.assertFalse(bus.closed)

    def test_status_when_bus_raises_does_not_raise(self):
        for error in (AccessibilityUnavailable('no org.a11y.Bus'), BusError('timed out')):
            result = accessibility.status(bus=FakeBus(tree(), unreachable=error))
            self.assertFalse(result['available'])
            self.assertIn(str(error), result['error'])

    def test_enable_sets_flag_and_returns_status(self):
        bus = FakeBus(tree())
        result = accessibility.enable(bus=bus)
        self.assertTrue(bus.enabled)
        self.assertTrue(result['available'])

    def test_enable_unreachable_raises_unavailable(self):
        bus = FakeBus(tree(), unreachable=BusError('boom'))
        with self.assertRaisesRegex(AccessibilityUnavailable, 'org.a11y.Bus'):
            accessibility.enable(bus=bus)

    def test_enable_closes_owned_bus(self):
        bus = FakeBus(tree())
        with patch.object(accessibility, 'Bus', return_value=bus):
            accessibility.enable()
        self.assertTrue(bus.closed)


class MissingJeepneyTest(unittest.TestCase):
    def test_missing_module_is_reported_without_connecting(self):
        with patch.dict(sys.modules, {'jeepney': None}):
            with self.assertRaisesRegex(AccessibilityUnavailable, 'Install jeepney'):
                Bus().status()


if __name__ == '__main__':
    unittest.main()
