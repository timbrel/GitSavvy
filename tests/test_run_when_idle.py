from unittesting import DeferrableTestCase

from GitSavvy.core.runtime import enqueue_on_worker, run_when_worker_is_idle


class TestRunWhenIdle(DeferrableTestCase):
    def test_a(self):
        messages = []

        def work(a):
            messages.append(a)

        run_when_worker_is_idle(work, 42)

        yield lambda: messages == [42]

    def test_b1(self):
        messages = []

        def work(a):
            messages.append(a)

        enqueue_on_worker(work, 24)
        run_when_worker_is_idle(work, 42)

        yield lambda: messages == [24, 42]

    def test_b2(self):
        messages = []

        def work(a):
            messages.append(a)

        run_when_worker_is_idle(work, 42)
        enqueue_on_worker(work, 24)

        yield lambda: messages == [24, 42]

    def test_c(self):
        messages = []

        def work(a):
            messages.append(a)

        def work2(a):
            enqueue_on_worker(work, a)

        run_when_worker_is_idle(work, 42)
        enqueue_on_worker(work2, 24)

        yield lambda: messages == [24, 42]

    def test_d(self):
        messages = []

        def work(a):
            messages.append(a)

        def work2(a):
            run_when_worker_is_idle(work, a)

        run_when_worker_is_idle(work2, 24)
        run_when_worker_is_idle(work2, 42)
        enqueue_on_worker(work, 4)

        yield lambda: messages == [4, 24, 42]
