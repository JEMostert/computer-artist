"""Failure-path tests for the controller's ownership lifetime."""
import json
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from computer_artist import Client


class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'control'
        self.server = socket.socket(socket.AF_UNIX)
        self.server.bind(str(self.path))
        self.server.listen()
        self.events = []
        self.released = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        connection,_ = self.server.accept()
        lease = ''
        with connection, connection.makefile('rb') as stream:
            for line in stream:
                request = json.loads(line)
                self.events.append(request)
                if request['op']=='acquire': lease='lease-1'
                if request['op']=='release':
                    lease=''
                    self.released.set()
                connection.sendall((json.dumps({'ok':True,'lease':lease,'generation':7,'keyboard_ready':True})+'\n').encode())

    def tearDown(self):
        self.server.close()
        self.thread.join(3)
        self.tmp.cleanup()

    def test_deadline_returns_app_while_program_is_idle(self):
        with Client(self.path, deadline=.1) as client:
            client.acquire('existing-window')
            self.assertTrue(self.released.wait(2))
            with self.assertRaises(TimeoutError): client.move(1,2)
        self.assertFalse(any(e['op']=='move' for e in self.events))

    def test_program_exception_releases_lease(self):
        with Client(self.path) as client:
            with self.assertRaisesRegex(ValueError,'program failed'):
                with client.owned('existing-window'):
                    client.move(20,30)
                    raise ValueError('program failed')
        self.assertTrue(self.released.is_set())
        action = next(e for e in self.events if e['op']=='move')
        self.assertEqual(action['lease'],'lease-1')
        self.assertEqual(action['generation'],7)

    def test_action_budget_releases_without_dispatching_extra_action(self):
        with Client(self.path, action_budget=1) as client:
            client.acquire('existing-window')
            with self.assertRaises(TimeoutError): client.move(20,30)
            self.assertTrue(self.released.is_set())
        self.assertFalse(any(e['op']=='move' for e in self.events))

    def test_unsupported_text_does_not_partially_type(self):
        with Client(self.path) as client:
            with client.owned('existing-window'):
                with self.assertRaises(ValueError): client.type_text('valid prefix 😀')
        self.assertFalse(any(e['op']=='key' for e in self.events))

if __name__=='__main__': unittest.main()
