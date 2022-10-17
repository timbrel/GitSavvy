from unittesting import DeferrableTestCase
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.commands.init import (
    parse_url_from_clipboard,
    project_name_from_url
)


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

        (
            "https://bitbucket.org/mdaloia/sublime-compare-side-by-side/src/master/",
            "https://bitbucket.org/mdaloia/sublime-compare-side-by-side.git"
        ),
    ])
    def test_parse_url_from_clipboard(self, IN, OUT):
        self.assertEqual(OUT, parse_url_from_clipboard(IN))

    @p.expand([
        ("git://github.com/timbrel/GitSavvy.git", "GitSavvy"),
        ("git@github.com:divmain/GitSavvy.git", "GitSavvy"),
        ("https://github.com/timbrel/GitSavvy.git", "GitSavvy"),
        ("https://github.com/timbrel/GitSavvy", "GitSavvy"),
        ("https://github.com/wbond/packagecontrol.io.git", "packagecontrol.io"),
        ("https://github.com/wbond/packagecontrol.io", "packagecontrol.io"),
        ("https://bitbucket.org/mdaloia/sublime-compare-side-by-side.git", "sublime-compare-side-by-side"),

        # pathological cases
        ("", ""),
        ("abc", "abc"),
    ])
    def test_project_name_from_git_url(self, GIT_URL, PROJECT_NAME):
        self.assertEqual(PROJECT_NAME, project_name_from_url(GIT_URL))
