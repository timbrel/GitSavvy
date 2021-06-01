from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.commands.init import parse_url_from_clipboard


class TestGsCloneHelper(DeferrableTestCase):
    @p.expand([
        ("git://github.com/timbrel/GitSavvy.git", "git://github.com/timbrel/GitSavvy.git"),
        ("git@github.com:divmain/GitSavvy.git", "git@github.com:divmain/GitSavvy.git"),
        ("https://github.com/timbrel/GitSavvy.git", "https://github.com/timbrel/GitSavvy.git"),

        ("https://github.com/timbrel/GitSavvy", "https://github.com/timbrel/GitSavvy.git"),
        (
            "https://github.com/timbrel/GitSavvy/issues?q=is%3Aissue+is%3Aopen+sort%3Aupdated-desc",
            "https://github.com/timbrel/GitSavvy.git"
        ),
        (
            "https://github.com/timbrel/GitSavvy/blob/master/core/git_command.py",
            "https://github.com/timbrel/GitSavvy.git"
        ),

        ("", ""),
        ("https://github.com", ""),
        ("https://github.com/", ""),
        ("https://github.com/timbrel", ""),
        ("https://github.com/timbrel/", ""),
    ])
    def test_parse_url_from_clipboard(self, IN, OUT):
        self.assertEqual(OUT, parse_url_from_clipboard(IN))
