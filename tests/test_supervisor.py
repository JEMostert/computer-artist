"""A real worker's CPU deadline preserves observed evidence without claiming success."""

import json
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path

from PIL import Image

from computer_artist.control import request_stop
from computer_artist.supervisor import run_supervised


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.operations = []
        test = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                for line in self.rfile:
                    request = json.loads(line)
                    operation = request['op']
                    test.operations.append(operation)
                    reply = {'ok': True, 'generation': 1, 'lease': ''}
                    if operation == 'capabilities':
                        reply['operations'] = ['capture']
                    elif operation == 'windows':
                        reply['windows'] = [
                            {
                                'id': 'w',
                                'title': 'Read-only fixture',
                                'native': True,
                                'visible': True,
                                'x': 0,
                                'y': 0,
                                'width': 20,
                                'height': 10,
                                'pid': 123,
                            }
                        ]
                    elif operation == 'capture':
                        Image.new('RGB', (20, 10), 'blue').save(request['path'])
                        reply['source'] = 'fixture_capture'
                    else:
                        raise AssertionError(f'Unexpected desktop input: {operation}')
                    try:
                        self.wfile.write((json.dumps(reply) + '\n').encode())
                    except (BrokenPipeError, ConnectionResetError):
                        break

        self.server = socketserver.ThreadingUnixStreamServer(str(self.root / 'socket'), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def execute(self, source, deadline=0.5):
        return run_supervised(
            {
                'socket': str(self.root / 'socket'),
                'deadline': deadline,
                'budget': 100,
                'window': 'w',
                'window_root': str(self.root / 'window'),
                'output_root': str(self.root / 'output'),
                'source': source,
            }
        )

    def test_cpu_loop_hard_timeout_preserves_checks_and_capture(self):
        result = self.execute(
            'def run(ctx):\n'
            '    ctx.observe()\n'
            '    ctx.verify("frame observed", True, evidence={"stage": "before loop"})\n'
            '    while True: pass\n'
        )
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'interrupted')
        self.assertFalse(result['final_outcome_known'])
        self.assertTrue(result['partial_evidence'])
        self.assertEqual(result['checks'][0]['name'], 'frame observed')
        self.assertTrue(Path(result['observation']['full_image']).is_file())
        full = json.loads(Path(result['record']).read_text())
        self.assertTrue(full['trace'])
        self.assertLess(result['duration'], 2)
        self.assertEqual(set(self.operations), {'capabilities', 'windows', 'capture'})

    def test_returned_unverified_final_record_is_distinct_from_checkpoint(self):
        result = self.execute('def run(ctx):\n    return {"returned": True}', deadline=2)
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'returned_unverified')
        self.assertFalse(result['final_outcome_known'])
        self.assertEqual(result['result'], {'returned': True})

    def test_verified_worker_record_supersedes_in_progress_checkpoint(self):
        result = self.execute(
            'def run(ctx):\n    ctx.verify("specific outcome", True)\n    return "checked"',
            deadline=2,
        )
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'verified')
        self.assertTrue(result['final_outcome_known'])
        self.assertEqual(result['checks'][0]['name'], 'specific outcome')

    def test_selected_running_cpu_loop_stops_and_preserves_checkpoint(self):
        stopped = []

        def cancel():
            until = time.monotonic() + 3
            while time.monotonic() < until:
                checkpoints = list((self.root / 'output').glob('*/checkpoint.json'))
                if checkpoints:
                    checkpoint = json.loads(checkpoints[0].read_text())
                    if checkpoint.get('checks'):
                        stopped.append(
                            request_stop(self.root / 'output', checkpoints[0].parent.name)
                        )
                        return
                time.sleep(0.01)

        thread = threading.Thread(target=cancel)
        thread.start()
        result = self.execute(
            'def run(ctx):\n'
            '    ctx.observe()\n'
            '    ctx.verify("frame observed", True)\n'
            '    while True: pass\n',
            deadline=4,
        )
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(stopped), 1)
        self.assertEqual(result['status'], 'interrupted')
        self.assertEqual(result['interruption']['code'], 'user_cancelled')
        self.assertFalse(result['final_outcome_known'])
        self.assertEqual(result['checks'][0]['name'], 'frame observed')
        self.assertTrue(Path(result['observation']['full_image']).is_file())
        self.assertLess(result['duration'], 2)


if __name__ == '__main__':
    unittest.main()
