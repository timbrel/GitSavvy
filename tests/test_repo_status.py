import sublime

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import expect, unstub, when
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.git_command import GitCommand

class TestPrivateGitStatusFunction(DeferrableTestCase):
    def tearDown(self):
        unstub()
    # def setUp(self):
    #     self.view = sublime.active_window().new_file()
    #     # make sure we have a window to work with
    #     s = sublime.load_settings("Preferences.sublime-settings")
    #     s.set("close_windows_when_empty", False)

    # def tearDown(self):
    #     if self.view:
    #         self.view.set_scratch(True)
    #         self.view.window().focus_view(self.view)
    #         self.view.window().run_command("close_file")

    def test_ensure_it_calls_git_status(self):
        git = GitCommand()
        with expect(git).git("status", "--porcelain", "-z", "-b").thenReturn(''):
            git._get_status()

    @p.expand([
        ("FOO",            ["FOO"]),
        ("FOO\x00",        ["FOO"]),
        ("FOO\x00BAR",     ["FOO", "BAR"]),
        ("FOO\x00BAR\x00", ["FOO", "BAR"]),
    ])
    def test_splits_output_into_lines(self, git_status, expected):
        git = GitCommand()
        when(git).git("status", ...).thenReturn(git_status)

        actual = git._get_status()
        self.assertEqual(actual, expected)


TestShortBranchStatusTestcases = [
(
    "## optimize-status-interface",
    "optimize-status-interface"
),
(
    "## optimize-status-interface\x00",
    "optimize-status-interface"
),
(
    "## optimize-status-interface\x00?? foo.txt",
    "optimize-status-interface*"
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1]",
    "optimize-status-interface+1"
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1]\x00?? foo.txt",
    "optimize-status-interface*+1"
),
(
    "## dev...origin/dev [behind 7]",
    "dev-7"
),
(
    "## dev...origin/dev [behind 7]\x00?? foo.txt",
    "dev*-7"
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1, behind 2]",
    "optimize-status-interface+1-2"
),
(
    "## optimize-status-interface...fork/branch-name [ahead 1, behind 2]\x00M foo",
    "optimize-status-interface*+1-2"
),
(
    "## improve-diff-view...fork/improve-diff-view [gone]",
    "improve-diff-view"
),
(
    "## improve-diff-view...fork/improve-diff-view [gone]\x00?? foo",
    "improve-diff-view*"
)

]
class TestShortBranchStatus(DeferrableTestCase):
    def tearDown(self):
        unstub()

    @p.expand(TestShortBranchStatusTestcases)
    def test_format_branch_status_for_statusbar(self, status_lines, expected):
        git = GitCommand()
        when(git).in_rebase().thenReturn(False)
        when(git).in_merge().thenReturn(False)

        when(git).git("status", ...).thenReturn(status_lines)

        actual = git.get_branch_status_short()
        self.assertEqual(actual, expected)



TestLongBranchStatusTestcases = [
("""\
## optimize-status-interface...fork/optimize-status-interface [ahead 1]
?? tests/test_repo_status.py""".strip().splitlines(), "optimize-status-interface*+1"),

("""\
## dev...origin/dev [behind 7]
?? tests/test_repo_status.py
""".strip().splitlines(), "dev*-7"),

("""\
## optimize-status-interface...fork/optimize-status-interface [ahead 1, behind 2]
?? tests/test_repo_status.py
""".strip().splitlines(), "optimize-status-interface*+1-2"),

("""\
## improve-diff-view...fork/improve-diff-view [gone]
?? tests/test_repo_status.py
""".strip().splitlines(), "improve-diff-view*")

]


"""\
## optimize-status-interface...fork/optimize-status-interface [ahead 1]
 M core/interfaces/status.py
?? tests/test_repo_status.py
"""