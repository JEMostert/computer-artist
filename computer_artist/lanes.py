"""Two explicit lanes sharing a deadline and budget; KWin arbitrates ownership."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_EXCEPTION
import threading

from .client import Client


class Execution:
    def __init__(self, root):
        self.root = root
        self.allow_host = getattr(root.client, 'lane', 'agent') == 'host'
        self.contexts = {(getattr(root.client, 'lane', 'agent'), root.window_id): root}
        self.locks = {'agent': threading.RLock(), 'host': threading.RLock()}
        self.lock = threading.RLock()

    def window(self, lane, identity):
        if lane == 'host' and not self.allow_host:
            raise PermissionError('Host input requires invoking this program with --host')
        identity = identity.strip('{}')
        with self.lock:
            if (lane, identity) not in self.contexts:
                from .runtime import Context
                parent = self.root.client
                client = Client(parent.socket_path, lane=lane, shared=parent.shared)
                try:
                    context = Context(client, identity, self.root.store, execution=self)
                except BaseException:
                    client.close()
                    raise
                self.contexts[(lane, identity)] = context
            return self.contexts[(lane, identity)]

    def close(self):
        for context in list(self.contexts.values()):
            context.client.close()


class Lane:
    def __init__(self, execution, lane):
        self.execution, self.lane = execution, lane

    def window(self, identity):
        return self.execution.window(self.lane, identity)


class Parallel:
    def __init__(self, execution):
        self.execution = execution
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='ca-lane')
        self.futures = []

    def __enter__(self):
        return self

    def start(self, function, context, *args, **kwargs):
        if context.execution is not self.execution:
            raise ValueError('Parallel tasks must share an execution context')

        def run():
            with self.execution.locks[context.lane]:
                try:
                    context.check_budget()
                    return function(context, *args, **kwargs)
                except BaseException:
                    self.execution.root.client.shared.cancelled.set()
                    raise
                finally:
                    try: context.client.release()
                    finally: context.owned = False

        future = self.pool.submit(run)
        self.futures.append(future)
        return future

    def __exit__(self, kind, error, tb):
        try:
            if error:
                self.execution.root.client.shared.cancelled.set()
            elif self.futures:
                completed, pending = wait(self.futures, return_when=FIRST_EXCEPTION)
                failure = next((f.exception() for f in completed if f.exception()), None)
                if failure:
                    self.execution.root.client.shared.cancelled.set()
                    raise failure
                for future in self.futures:
                    future.result()
        finally:
            self.pool.shutdown(wait=True, cancel_futures=True)
