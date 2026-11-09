from __future__ import annotations

import importlib.util
from collections import deque

from unittesting import DeferrableTestCase

from GitSavvy.core import runtime
from GitSavvy.tests.mockito import mock, unstub, when


class TestWorkerQueueReload(DeferrableTestCase):
    def setUp(self) -> None:
        # Execute the real module in an isolated namespace, then re-execute it
        # in place like the plugin reloader. Leave the live runtime untouched.
        self.spec = importlib.util.spec_from_file_location(
            'GitSavvy.core._runtime_reload_test', runtime.__file__
        )
        self.runtime = importlib.util.module_from_spec(self.spec)
        self.pending = deque()
        self.executors = []
        self.reload_runtime()

    def tearDown(self) -> None:
        unstub()
        for executor in self.executors:
            executor.shutdown(wait=True)

    def test_idle_callback_does_not_spin_after_reload(self) -> None:
        calls = []
        self.runtime.enqueue_on_worker(calls.append, 'work')
        self.runtime.run_when_worker_is_idle(calls.append, 'idle')
        self.reload_runtime()

        for _ in range(10):
            if not self.pending:
                break
            self.pending.popleft()()

        self.assertFalse(self.pending, 'idle callback keeps rescheduling')
        self.assertEqual(['work', 'idle'], calls)
        self.assertEqual(0, self.runtime._enqueued_tasks.count())

    def test_pending_work_and_idle_callback_survive_reload(self) -> None:
        calls = []
        self.runtime.enqueue_on_worker(calls.append, 'before reload')
        self.runtime.run_when_worker_is_idle(calls.append, 'idle')

        self.reload_runtime()
        self.runtime.enqueue_on_worker(calls.append, 'after reload')

        # Bound the drain: the regression otherwise reschedules forever.
        for _ in range(10):
            if not self.pending:
                break
            self.pending.popleft()()

        self.assertEqual(['before reload', 'after reload', 'idle'], calls)
        self.assertFalse(self.pending, 'idle callback keeps rescheduling')
        self.assertEqual(0, self.runtime._enqueued_tasks.count())

    def test_repeated_reloads_preserve_pending_count(self) -> None:
        self.runtime.enqueue_on_worker(lambda: None)
        counter = self.runtime._enqueued_tasks
        for _ in range(3):
            self.reload_runtime()
            self.assertIs(counter, self.runtime._enqueued_tasks)
            self.assertEqual(1, counter.count())
        self.pending.popleft()()
        self.assertEqual(0, counter.count())

    def reload_runtime(self) -> None:
        self.spec.loader.exec_module(self.runtime)
        self.executors.append(self.runtime.savvy_executor)
        scheduler = mock()
        when(scheduler).set_timeout_async(...).thenAnswer(
            lambda callback, after=0: self.pending.append(callback)
        )
        self.runtime.sublime = scheduler
