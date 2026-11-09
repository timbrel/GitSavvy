from __future__ import annotations
import threading

from unittesting import DeferrableTestCase

from GitSavvy.core.runtime import enqueue_on_worker, must_run_on_ui


@must_run_on_ui
def add(a: int, b: int) -> int:
    return a + b


class TestMustRunOnUi(DeferrableTestCase):
    def test_runs_on_the_ui_thread(self):
        self.assertEqual(3, add(1, 2))

    def test_raises_on_the_worker(self):
        errors: list[BaseException] = []

        def call():
            try:
                add(1, 2)
            except RuntimeError as e:
                errors.append(e)

        enqueue_on_worker(call)

        yield lambda: len(errors) == 1

    def test_raises_on_other_threads(self):
        errors: list[BaseException] = []

        def call():
            try:
                add(1, 2)
            except RuntimeError as e:
                errors.append(e)

        thread = threading.Thread(target=call)
        thread.start()
        thread.join()

        self.assertEqual(1, len(errors))
