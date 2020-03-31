from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import when, verify
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.git_mixins.history import HistoryMixin


examples = [
    ("from commit to working dir", "abc", None, ["abc", None]),
    ("from commit to commit", "abc", "def", ["abc", "def"]),
    ("from commit to commit", "abc", "HEAD", ["abc", "HEAD"]),
    ("from working dir to commit", None, "def", ["-R", "def"]),
    ("from working dir to HEAD", None, "HEAD", ["-R", "HEAD"]),
]


class TestDescribeGraphLine(DeferrableTestCase):
    @p.expand(examples)
    def test_no_context_diff_logic(self, _, base, target, cmd):
        test = HistoryMixin()
        when(test, strict=False).git("diff", ...).thenReturn("irrelevant")
        test.no_context_diff(base, target)
        common = ["diff", "--no-color", "-U0"]
        verify(test).git(*(common + cmd))

    def test_no_context_diff_add_file_if_given(self):
        test = HistoryMixin()
        when(test, strict=False).git("diff", ...).thenReturn("irrelevant")
        test.no_context_diff("a", "b", "foofile.py")
        common = ["diff", "--no-color", "-U0"]
        cmd = ["a", "b", "--", "foofile.py"]
        verify(test).git(*(common + cmd))
