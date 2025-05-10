from GitSavvy.core.runtime import run_or_timeout

from unittesting import DeferrableTestCase


class TestRunForTimeout(DeferrableTestCase):
    def testReturnsFunctionResult(self):
        def main():
            return "Ok"

        self.assertEqual("Ok", run_or_timeout(main, timeout=1.0))

    def testReraisesException(self):
        def main():
            1 / 0

        self.assertRaises(ZeroDivisionError, lambda: run_or_timeout(main, timeout=1.0))

    def testRaisesTimeoutIfFunctionTakesTooLong(self):
        def main():
            import time
            time.sleep(0.1)
            1 / 0

        self.assertRaises(TimeoutError, lambda: run_or_timeout(main, timeout=0.01))
