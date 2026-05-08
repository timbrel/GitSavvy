import os

from unittesting import DeferrableTestCase
from GitSavvy.tests.mockito import unstub, when, verify
from GitSavvy.tests.parameterized import parameterized as p

from GitSavvy.core.git_mixins.history import (
    CommitHistoryInfo,
    FileHistoryEntry,
    FileHistoryInfo,
    FileStatus,
    HistoryMixin,
    parse_file_history_log,
    parse_name_status_z
)


RS = "\x1e"
US = "\x1f"
NUL = "\0"


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

    def test_file_history_log_warms_commit_file_caches(self):
        test = HistoryMixin()
        when(test).git("log", ...).thenReturn(
            f"{RS}c5{US}2026-05-07 10:00:00 +0200{US}newest{NUL}"
            f"\nM{NUL}tests/instancemethods_test.py{NUL}"
            f"{RS}c4{US}2026-04-03 10:00:00 +0200{US}rename to tests{NUL}"
            f"\nR100{NUL}mockito/tests/instancemethods_test.py{NUL}tests/instancemethods_test.py{NUL}"
            f"{RS}c3{US}2026-03-02 10:00:00 +0200{US}middle{NUL}"
            f"\nM{NUL}mockito/tests/instancemethods_test.py{NUL}"
            f"{RS}c2{US}2026-02-01 10:00:00 +0200{US}rename to mockito/tests{NUL}"
            f"\nR099{NUL}mockito_test/instancemethods_test.py{NUL}mockito/tests/instancemethods_test.py{NUL}"
            f"{RS}c1{US}2026-01-02 10:00:00 +0200{US}oldest{NUL}"
            f"\nM{NUL}mockito_test/instancemethods_test.py{NUL}"
        )
        when(test).get_repo_path().thenReturn("/repo")
        p = lambda p: os.path.normpath(os.path.join("/repo", p))

        file_cache = {}
        commit_cache = {}
        test._fetch_info_for_commit_file_path_pairs(
            p("tests/instancemethods_test.py"),
            file_cache=file_cache,
            commit_cache=commit_cache
        )

        self.assertEqual(file_cache, {
            ("c5", p("tests/instancemethods_test.py")):
                FileHistoryInfo(p("tests/instancemethods_test.py"), "c4"),
            ("c4", p("tests/instancemethods_test.py")):
                FileHistoryInfo(p("tests/instancemethods_test.py"), "c3"),
            ("c3", p("tests/instancemethods_test.py")):
                FileHistoryInfo(p("mockito/tests/instancemethods_test.py"), "c2"),
            ("c2", p("tests/instancemethods_test.py")):
                FileHistoryInfo(p("mockito/tests/instancemethods_test.py"), "c1"),
            ("c1", p("tests/instancemethods_test.py")):
                FileHistoryInfo(p("mockito_test/instancemethods_test.py"), None)
        })
        self.assertEqual(commit_cache, {
            "c5": CommitHistoryInfo("newest", "2026-5-7"),
            "c4": CommitHistoryInfo("rename to tests", "2026-4-3"),
            "c3": CommitHistoryInfo("middle", "2026-3-2"),
            "c2": CommitHistoryInfo("rename to mockito/tests", "2026-2-1"),
            "c1": CommitHistoryInfo("oldest", "2026-1-2")
        })
        verify(test).git(...)

    def test_fetch_info_without_file_path_warms_commit_only_cache(self):
        test = HistoryMixin()
        when(test).git("log", ...).thenReturn(
            f"{RS}c3{US}2026-05-07 10:00:00 +0200{US}newest{NUL}"
            f"{RS}c2{US}2026-04-03 10:00:00 +0200{US}middle{NUL}"
            f"{RS}c1{US}2026-01-02 10:00:00 +0200{US}oldest{NUL}"
        )

        file_cache = {}
        commit_cache = {}
        test._fetch_info_for_commit_file_path_pairs(
            file_cache=file_cache,
            commit_cache=commit_cache
        )

        self.assertEqual(file_cache, {
            ("c3", None): FileHistoryInfo(None, "c2"),
            ("c2", None): FileHistoryInfo(None, "c1"),
            ("c1", None): FileHistoryInfo(None, None)
        })
        self.assertEqual(commit_cache, {
            "c3": CommitHistoryInfo("newest", "2026-5-7"),
            "c2": CommitHistoryInfo("middle", "2026-4-3"),
            "c1": CommitHistoryInfo("oldest", "2026-1-2")
        })
        verify(test).git(...)

    def test_fetch_info_does_not_mark_truncated_history_as_initial(self):
        test = HistoryMixin()
        when(test).git("log", ...).thenReturn(
            f"{RS}c3{US}2026-05-07 10:00:00 +0200{US}newest{NUL}"
            f"{RS}c2{US}2026-04-03 10:00:00 +0200{US}middle{NUL}"
            f"{RS}c1{US}2026-01-02 10:00:00 +0200{US}oldest fetched{NUL}"
        )

        file_cache = {}
        commit_cache = {}
        test._fetch_info_for_commit_file_path_pairs(
            file_cache=file_cache,
            commit_cache=commit_cache,
            limit=2
        )

        self.assertEqual(file_cache, {
            ("c3", None): FileHistoryInfo(None, "c2"),
            ("c2", None): FileHistoryInfo(None, "c1")
        })
        self.assertEqual(commit_cache, {
            "c3": CommitHistoryInfo("newest", "2026-5-7"),
            "c2": CommitHistoryInfo("middle", "2026-4-3")
        })

    def test_filename_at_head_keeps_existing_workdir_path(self):
        test = HistoryMixin()
        when(test).get_repo_path().thenReturn("/repo")
        when(test).to_rel_path("current.py").thenReturn("current.py")

        when(os.path).exists(...).thenReturn(True)

        self.assertEqual(test.filename_at_head("current.py", "abc123"), "current.py")

    def test_filename_at_head_follows_renames_forward(self):
        test = HistoryMixin()
        when(test).get_repo_path().thenReturn("/repo")
        when(test).commit_is_ancestor_of_head("abc123").thenReturn(True)

        when(test).git("log", ...) \
            .thenReturn("rename1\0\nD\0old.py\0") \
            .thenReturn("change1\0\nM\0new.py\0")
        when(test).git("show", ...) \
            .thenReturn("R100\0old.py\0new.py\0") \
            .thenReturn("M\0new.py\0")

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

    def test_parse_file_history_log(self):
        self.assertEqual(
            list(parse_file_history_log(
                f"{RS}c2{US}2026-04-03 10:00:00 +0200{US}rename{NUL}"
                f"\nR100{NUL}old.py{NUL}new.py{NUL}"
            )),
            [FileHistoryEntry(
                "c2",
                "2026-4-3",
                "rename",
                FileStatus("R100", "old.py", "new.py")
            )]
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
