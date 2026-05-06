import os

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when, verify
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.git_mixins.history import FileStatus, HistoryMixin, parse_name_status_z


examples = [
    ("from commit to working dir", "abc", None, ["abc", None]),
    ("from commit to commit", "abc", "def", ["abc", "def"]),
    ("from commit to commit", "abc", "HEAD", ["abc", "HEAD"]),
    ("from working dir to commit", None, "def", ["-R", "def"]),
    ("from working dir to HEAD", None, "HEAD", ["-R", "HEAD"]),
]


class TestDescribeGraphLine(DeferrableTestCase):
    def tearDown(self):
        unstub()

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

    def test_log_follow_requires_file_path(self):
        test = HistoryMixin()

        with self.assertRaises(RuntimeError):
            test.log(follow=True)

    def test_log_commits_linewise_follow_requires_file_path(self):
        test = HistoryMixin()

        with self.assertRaises(RuntimeError):
            test._log_commits_linewise("HEAD", None, follow=True)

    def test_filename_at_head_keeps_existing_workdir_path(self):
        test = HistoryMixin()
        when(test).get_repo_path().thenReturn("/repo")
        when(test).to_rel_path("current.py").thenReturn("current.py")

        when(os.path).exists(...).thenReturn(True)

        self.assertEqual(test.filename_at_head("current.py", "abc123"), "current.py")

    def test_filename_at_head_follows_renames_forward(self):
        test = HistoryMixin()
        when(test).get_repo_path().thenReturn("/repo")
        when(test).to_rel_path("old.py").thenReturn("old.py")
        when(test).commit_is_ancestor_of_head("abc123").thenReturn(True)
        when(os.path).exists(...).thenReturn(False)
        when(test).git(
            "log", "--follow", "--format=%H", "--name-status", "-1", "-z",
            "abc123..HEAD", "--", "old.py"
        ).thenReturn("rename1\0\nD\0old.py\0")
        when(test).git(
            "show", "--name-status", "--format=", "-z", "rename1"
        ).thenReturn("R100\0old.py\0new.py\0")
        when(test).git(
            "log", "--follow", "--format=%H", "--name-status", "-1", "-z",
            "abc123..HEAD", "--", "new.py"
        ).thenReturn("change1\0\nM\0new.py\0")
        when(test).git(
            "show", "--name-status", "--format=", "-z", "change1"
        ).thenReturn("M\0new.py\0")

        self.assertEqual(test.filename_at_head("old.py", "abc123"), "new.py")

    def test_find_matching_lineno_between_files_at_commits(self):
        test = HistoryMixin()
        when(test).to_rel_path("old.py").thenReturn("old.py")
        when(test).to_rel_path("new.py").thenReturn("new.py")
        when(test).git(
            "diff", "--no-color", "-U0", "abc123:old.py", "def456:new.py"
        ).thenReturn("@@ -1,0 +2 @@\n+new\n")

        self.assertEqual(
            test.find_matching_lineno_between_files(
                ("abc123", "old.py"),
                ("def456", "new.py"),
                2
            ),
            3
        )

    def test_find_matching_lineno_between_files_in_worktree(self):
        test = HistoryMixin()
        when(test).to_rel_path("old.py").thenReturn("old.py")
        when(test).to_rel_path("new.py").thenReturn("new.py")
        when(test).git(
            "diff", "--no-color", "-U0", "abc123:old.py", "--", "new.py"
        ).thenReturn("@@ -1,0 +2 @@\n+new\n")

        self.assertEqual(
            test.find_matching_lineno_between_files(
                ("abc123", "old.py"),
                (None, "new.py"),
                2
            ),
            3
        )

    def test_find_matching_lineno_in_file_history_from_worktree(self):
        test = HistoryMixin()
        when(test).filename_at_commit("new.py", "abc123").thenReturn("old.py")
        when(test).reverse_find_matching_lineno_between_files(
            ("abc123", "old.py"),
            (None, "new.py"),
            3
        ).thenReturn(2)

        self.assertEqual(
            test.find_matching_lineno_in_file_history(None, "abc123", 3, "new.py"),
            2
        )

    def test_find_matching_lineno_in_file_history_between_commits(self):
        test = HistoryMixin()
        when(test).filename_at_commit("new.py", "abc123").thenReturn("old.py")
        when(test).filename_at_commit("new.py", "def456").thenReturn("new.py")
        when(test).find_matching_lineno_between_files(
            ("abc123", "old.py"),
            ("def456", "new.py"),
            2
        ).thenReturn(3)

        self.assertEqual(
            test.find_matching_lineno_in_file_history("abc123", "def456", 2, "new.py"),
            3
        )

    def test_reverse_adjust_line_according_to_diff(self):
        test = HistoryMixin()
        diff = "@@ -1,0 +2 @@\n+new\n"

        self.assertEqual(test.reverse_adjust_line_according_to_diff(diff, 3), 2)

    def test_reverse_find_matching_lineno_between_files_at_commits(self):
        test = HistoryMixin()
        when(test).to_rel_path("old.py").thenReturn("old.py")
        when(test).to_rel_path("new.py").thenReturn("new.py")
        when(test).git(
            "diff", "--no-color", "-U0", "abc123:old.py", "def456:new.py"
        ).thenReturn("@@ -1,0 +2 @@\n+new\n")

        self.assertEqual(
            test.reverse_find_matching_lineno_between_files(
                ("abc123", "old.py"),
                ("def456", "new.py"),
                3
            ),
            2
        )

    def test_parse_name_status_z_regular_statuses(self):
        self.assertEqual(
            list(parse_name_status_z("M\0modified.py\0D\0deleted.py\0")),
            [FileStatus("M", "modified.py", None), FileStatus("D", "deleted.py", None)]
        )

    def test_parse_name_status_z_renames(self):
        self.assertEqual(
            list(parse_name_status_z("R100\0old.py\0new.py\0")),
            [FileStatus("R100", "old.py", "new.py")]
        )

    def test_parse_name_status_z_copies(self):
        self.assertEqual(
            list(parse_name_status_z("C100\0source.py\0copy.py\0")),
            [FileStatus("C100", "source.py", "copy.py")]
        )

    def test_parse_name_status_z_ignores_empty_records(self):
        self.assertEqual(list(parse_name_status_z("")), [])
