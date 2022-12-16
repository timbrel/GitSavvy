from GitSavvy.common import ui

from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p
from GitSavvy.tests.parameterized import param


class TestAutoInjectingState(DeferrableTestCase):
    @p.expand([
        (
            "fills in argument from state",
            lambda self, status: status,
            {"status": "ok"},
            param(),
            "ok"
        ),
        (
            "fills in two arguments from state",
            lambda self, status, code: (status, code),
            {"status": "ok", "code": 202},
            param(),
            ("ok", 202)
        ),
        (
            "given first arg (positional), fill in the second",
            lambda self, status, code: (status, code),
            {"status": "ok", "code": 202},
            param("erred"),
            ("erred", 202)
        ),
        (
            "`given first arg (per keyword), fill in the second",
            lambda self, status, code: (status, code),
            {"status": "ok", "code": 202},
            param(status="erred"),
            ("erred", 202)
        ),
        (
            "given second arg, fill in the first",
            lambda self, status, code: (status, code),
            {"status": "ok", "code": 202},
            param(code=303),
            ("ok", 303)
        ),
        (
            "do not fill in args which have defaults",
            lambda self, status, code=202: (status, code),
            {"status": "ok", "code": "xxx"},
            param(),
            ("ok", 202)
        ),

        (
            "okay if fn takes no arguments",
            lambda self: "ok",
            {"status": "ok", "code": "xxx"},
            param(),
            "ok"
        ),
    ])
    def test_success_cases(self, _, fn, state, call_sig, return_value):

        self.state = state
        self.assertEqual(
            ui.section("branch")(fn)
            (self, *call_sig.args, **call_sig.kwargs),
            return_value)

    def test_return_empty_string_if_val_not_present(self):
        self.state = {}

        @ui.section("branch")
        def fn(self, status):
            return status

        self.assertEqual(fn(self), "")
