from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.runtime import THROTTLED_CACHE, throttled, throttled_by_key


class TestThrottled(DeferrableTestCase):
    def test_latest_call_wins_across_instances(self):
        calls = []
        first = Receiver(calls)
        second = Receiver(calls)
        pending = [throttled(first.record, 1), throttled(second.record, 2)]
        for task in pending:
            task()
        self.assertEqual([2], calls)

    def test_same_definition_coalesces_across_closures(self):
        calls = []
        first = throttled(make_callback(calls), 1)
        second = throttled(make_callback(calls), 2)
        first()
        second()
        self.assertEqual([2], calls)

    def test_keyed_calls_isolate_targets_and_operations(self):
        calls = []
        receiver = Receiver(calls)
        pending = [
            throttled_by_key(('view', 1), receiver.record, 1),
            throttled_by_key(('view', 2), receiver.record, 2),
            throttled_by_key(('view', 1), receiver.record, 3),
            throttled_by_key(('view', 1), receiver.other, 4),
        ]
        for task in pending:
            task()
        self.assertEqual([2, 3, 4], calls)

    def test_completed_callback_is_removed_and_runs_only_once(self):
        calls = []
        receiver = Receiver(calls)
        task = throttled(receiver.record, value=1)
        key = (receiver.record.__module__, receiver.record.__qualname__)
        self.assertIn(key, THROTTLED_CACHE)
        task()
        self.assertNotIn(key, THROTTLED_CACHE)
        task()
        self.assertEqual([1], calls)

    def test_stale_callback_cannot_remove_newer_request(self):
        calls = []
        receiver = Receiver(calls)
        stale = throttled(receiver.record, 1)
        current = throttled(receiver.record, 2)
        stale()
        current()
        newer = throttled(receiver.record, 3)
        current()
        newer()
        self.assertEqual([2, 3], calls)

    def test_request_during_execution_survives(self):
        pending = []
        calls = []

        def callback(value):
            calls.append(value)
            if value == 1:
                pending.append(throttled(callback, 2))

        throttled(callback, 1)()
        pending.pop()()
        self.assertEqual([1, 2], calls)

    def test_exception_does_not_retain_entry(self):
        def callback():
            raise ValueError('expected')

        task = throttled(callback)
        with self.assertRaises(ValueError):
            task()
        self.assertNotIn((callback.__module__, callback.__qualname__), THROTTLED_CACHE)


class Receiver:
    def __init__(self, calls):
        self.calls = calls

    def record(self, value):
        self.calls.append(value)

    def other(self, value):
        self.calls.append(value)


def make_callback(calls):
    def callback(value):
        calls.append(value)
    return callback
