"""Portable input-attribution regressions; no macOS frameworks or live event taps."""

import threading
import unittest
from types import SimpleNamespace

from computer_artist.macos_activity import EVENT_TAG, Activity


class Events:
    kCGEventLeftMouseDown = 1
    kCGEventRightMouseDown = 3
    kCGEventLeftMouseDragged = 6
    kCGEventRightMouseDragged = 7
    kCGEventKeyDown = 10
    kCGEventScrollWheel = 22
    kCGEventOtherMouseDown = 25
    kCGEventOtherMouseDragged = 27
    kCGEventTapDisabledByTimeout = -2
    kCGEventTapDisabledByUserInput = -1
    kCGEventSourceUserData = 42

    @staticmethod
    def CGEventGetLocation(event):
        return event.location

    @staticmethod
    def CGEventGetIntegerValueField(event, field):
        if field != Events.kCGEventSourceUserData:
            raise AssertionError('Input monitoring must not inspect key codes or text')
        return event.tag


def window(wid, x, y, width, height, layer=0):
    return {
        'kCGWindowNumber': wid,
        'kCGWindowLayer': layer,
        'kCGWindowBounds': {'X': x, 'Y': y, 'Width': width, 'Height': height},
    }


class Desktop:
    q = Events

    def __init__(self):
        self.foreground = 100
        self.pointer = (300, 100)
        self.ordered_windows = [window(1, 0, 0, 200, 200), window(2, 200, 0, 200, 200)]

    def front_pid(self):
        return self.foreground

    def cursor(self):
        return self.pointer

    def windows(self):
        return self.ordered_windows


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.desktop = Desktop()
        self.activity = self.listener()

    def listener(self):
        # Bypass only native tap/thread startup; exercise actual event attribution and check().
        activity = Activity.__new__(Activity)
        activity.api, activity.pid, activity.wid = self.desktop, 100, 1
        activity._conflict, activity._stop = threading.Event(), threading.Event()
        activity._reason = None
        activity._thread = SimpleNamespace(is_alive=lambda: True)
        return activity

    def deliver(self, kind, point=(100, 100), tag=0, listener=None):
        event = SimpleNamespace(location=SimpleNamespace(x=point[0], y=point[1]), tag=tag)
        target = listener or self.activity
        self.assertIs(target._event(None, kind, event, None), event)

    def test_foreground_watching_without_input_is_allowed(self):
        self.desktop.foreground = 100
        self.desktop.pointer = (100, 100)
        self.activity.check()
        self.desktop.foreground = 200
        self.activity.check()
        self.desktop.foreground = 100
        self.activity.check()

    def test_initial_click_can_focus_background_target_for_watching(self):
        self.desktop.foreground = 200
        self.deliver(Events.kCGEventLeftMouseDown)
        self.desktop.foreground = 100
        self.activity.check()

    def test_key_in_target_conflicts_without_reading_key_contents(self):
        self.desktop.foreground = 200
        self.deliver(Events.kCGEventKeyDown)
        self.activity.check()
        self.desktop.foreground = 100
        self.deliver(Events.kCGEventKeyDown)
        with self.assertRaisesRegex(PermissionError, 'keyboard'):
            self.activity.check()

    def test_background_scroll_keeps_event_target_when_pointer_moves_away(self):
        self.desktop.foreground = 200
        self.deliver(Events.kCGEventScrollWheel, point=(100, 100))
        self.desktop.pointer = (300, 100)
        with self.assertRaisesRegex(PermissionError, 'scroll'):
            self.activity.check()

    def test_other_app_scroll_then_hover_target_is_allowed(self):
        self.desktop.foreground = 200
        self.deliver(Events.kCGEventScrollWheel, point=(300, 100))
        self.desktop.pointer = (100, 100)
        self.activity.check()

    def test_own_tagged_events_do_not_claim_human_takeover(self):
        for kind in (
            Events.kCGEventKeyDown,
            Events.kCGEventLeftMouseDown,
            Events.kCGEventRightMouseDown,
            Events.kCGEventOtherMouseDown,
            Events.kCGEventLeftMouseDragged,
            Events.kCGEventRightMouseDragged,
            Events.kCGEventOtherMouseDragged,
            Events.kCGEventScrollWheel,
        ):
            with self.subTest(kind=kind):
                self.deliver(kind, tag=EVENT_TAG)
                self.activity.check()

    def test_disabled_listener_fails_closed(self):
        for kind in (Events.kCGEventTapDisabledByTimeout, Events.kCGEventTapDisabledByUserInput):
            with self.subTest(kind=kind):
                listener = self.listener()
                self.deliver(kind, listener=listener)
                with self.assertRaisesRegex(PermissionError, 'disabled'):
                    listener.check()

    def test_foreground_click_conflicts_only_in_target_window(self):
        self.deliver(Events.kCGEventLeftMouseDown, point=(300, 100))
        self.activity.check()
        self.deliver(Events.kCGEventLeftMouseDown, point=(100, 100))
        with self.assertRaisesRegex(PermissionError, 'click'):
            self.activity.check()

    def test_drag_into_background_target_conflicts(self):
        self.desktop.foreground = 200
        self.deliver(Events.kCGEventLeftMouseDragged)
        with self.assertRaisesRegex(PermissionError, 'drag'):
            self.activity.check()

    def test_occluding_app_claims_scroll_but_floating_overlay_does_not(self):
        self.desktop.ordered_windows.insert(0, window(3, 0, 0, 200, 200))
        self.deliver(Events.kCGEventScrollWheel)
        self.activity.check()
        self.desktop.ordered_windows[0]['kCGWindowLayer'] = 3
        self.deliver(Events.kCGEventScrollWheel)
        with self.assertRaisesRegex(PermissionError, 'scroll'):
            self.activity.check()
