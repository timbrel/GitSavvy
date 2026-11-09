from __future__ import annotations

from collections import deque

from unittesting import DeferrableTestCase

from GitSavvy.core.git_command import GitCommand
from GitSavvy.core.git_mixins import status
from GitSavvy.core.git_mixins.status import FileStatus
from GitSavvy.tests.mockito import unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


SHA = "a" * 40
FILE_FIELDS = "N... 100644 100644 100644 {0} {0}".format(SHA)
CONFLICT_FIELDS = "N... 100644 100644 100644 100644 {0} {0} {0}".format(SHA)


class TestPorcelainStatus(DeferrableTestCase):
    def setUp(self) -> None:
        self.git = GitCommand()
        self.state = {"last_branches": deque([None, None])}
        when(self.git).current_state().thenReturn(self.state)
        when(self.git).update_store(...).thenAnswer(self.state.update)
        when(self.git).in_rebase().thenReturn(False)
        when(self.git).in_merge().thenReturn(False)
        when(self.git).in_cherry_pick().thenReturn(False)
        when(self.git).in_revert().thenReturn(False)
        when(self.git).in_bisect().thenReturn(False)
        when(status.ci_status).on_head_observed(...).thenReturn(None)

    def tearDown(self) -> None:
        unstub()

    @p.expand([
        ("main", SHA, "main", SHA, False),
        ("feature/naïve-☺", SHA, "feature/naïve-☺", SHA, False),
        ("(detached)", SHA, None, SHA, True),
        ("main", "(initial)", "main", None, False),
        ("main", "b" * 64, "main", "b" * 64, False)
    ])
    def test_head_identity_comes_from_the_single_status_query(
        self, head, oid, expected_branch, expected_sha, detached
    ) -> None:
        self.read_status(["# branch.oid " + oid, "# branch.head " + head])

        actual = self.state["head"]
        self.assertEqual(actual.commit_hash, expected_sha)
        self.assertEqual(actual.branch, expected_branch)
        self.assertEqual(actual.detached, detached)
        self.assertTrue(actual.clean)
        verify(self.git).git(
            "status", "--porcelain=v2", "-z", "-b",
            custom_environ={"GIT_OPTIONAL_LOCKS": "0"}
        )

    def test_ci_status_observes_each_head(self) -> None:
        self.read_status(["# branch.oid " + SHA, "# branch.head main"])

        verify(status.ci_status).on_head_observed(self.git, self.state["head"])

    def test_headers_can_be_reordered_and_extended(self) -> None:
        self.read_status([
            "# future.header ignored", "# branch.ab +3 -2", "# branch.head main",
            "# branch.upstream origin/main", "# branch.oid " + SHA
        ])
        actual = self.state["head"]
        self.assertEqual(actual.commit_hash, SHA)
        self.assertEqual(actual.remote, "origin/main")
        self.assertEqual(actual.ahead, "3")
        self.assertEqual(actual.behind, "2")
        self.assertFalse(actual.gone)
        self.assertTrue(actual.clean)
        self.assertEqual(self.state["short_status"], "main+3-2")

    @p.expand([(SHA,), ("(initial)",)])
    def test_missing_upstream_keeps_its_name_and_gone_status(self, oid) -> None:
        self.read_status([
            "# branch.oid " + oid, "# branch.head main", "# branch.upstream origin/main"
        ])
        actual = self.state["head"]
        self.assertEqual(actual.remote, "origin/main")
        self.assertTrue(actual.gone)
        self.assertIsNone(actual.ahead)
        self.assertIsNone(actual.behind)
        self.assertIn("The remote branch is gone.", self.state["long_status"])

    @p.expand([
        ("1 .M " + FILE_FIELDS + " file with spaces", FileStatus("file with spaces", None, "", "M")),
        ("1 A. " + FILE_FIELDS + " added", FileStatus("added", None, "A", "")),
        ("1 MM " + FILE_FIELDS + " both", FileStatus("both", None, "M", "M")),
        ("1 .D " + FILE_FIELDS + " deleted", FileStatus("deleted", None, "", "D")),
        ("? untracked\nfile", FileStatus("untracked\nfile", None, "?", "?")),
        ("?  leading space", FileStatus(" leading space", None, "?", "?")),
        ("! ignored", FileStatus("ignored", None, "!", "!")),
        (
            "1 .M S.MU 160000 160000 160000 {0} {0} submodule".format(SHA),
            FileStatus("submodule", None, "", "M")
        )
    ])
    def test_file_records_preserve_paths_and_statuses(self, record, expected) -> None:
        working = self.read_status(["# branch.oid " + SHA, "# branch.head main", record])
        self.assertFalse(self.state["head"].clean)
        self.assertEqual(self.state["short_status"], "main*")
        files = set(working.staged_files + working.unstaged_files + working.untracked_files)
        self.assertEqual(files, {expected})

    @p.expand([("R",), ("C",)])
    def test_rename_and_copy_records_consume_the_following_original_path(self, status) -> None:
        original = "# branch.oid not-a-header\nold name"
        working = self.read_status([
            "# branch.oid " + SHA, "# branch.head main",
            "2 {}. {} {}100 new name".format(status, FILE_FIELDS, status), original,
            "? another file"
        ])
        self.assertEqual(working.staged_files, [FileStatus("new name", original, status, "")])
        self.assertEqual(working.untracked_files, [FileStatus("another file", None, "?", "?")])
        self.assertEqual(self.state["head"].commit_hash, SHA)

    @p.expand([("DD",), ("AU",), ("UD",), ("UA",), ("DU",), ("AA",), ("UU",)])
    def test_unmerged_records_are_conflicts(self, xy) -> None:
        working = self.read_status([
            "# branch.oid " + SHA, "# branch.head main",
            "u {} {} conflicted file".format(xy, CONFLICT_FIELDS)
        ])
        self.assertEqual(working.merge_conflicts, [FileStatus("conflicted file", None, xy[0], xy[1])])
        self.assertEqual(working.staged_files, [])
        self.assertEqual(working.unstaged_files, [])

    @p.expand([("main", "main", False), ("(detached)", None, True)])
    def test_status_reports_head_and_renamed_paths_together(self, head, branch, detached) -> None:
        # Captured from `git status --porcelain=v2 -z -b` after staging a
        # rename. Replay the wire format without creating a repository.
        output = (
            "# branch.oid ebd7b2bb12c2e9d447032d8a8c9ca272ed4c0ed2\x00"
            "# branch.head {}\x00"
            "2 R. N... 100644 100644 100644 "
            "d95f3ad14dee633a758d2e331151e950dd13e4ed "
            "d95f3ad14dee633a758d2e331151e950dd13e4ed "
            "R100 new name Ω.txt\x00old name Ω.txt\x00"
        ).format(head)
        when(self.git).git(
            "status", "--porcelain=v2", "-z", "-b",
            custom_environ={"GIT_OPTIONAL_LOCKS": "0"}
        ).thenReturn(output)

        working = self.git.get_working_dir_status()

        actual = self.state["head"]
        self.assertEqual(actual.commit_hash, "ebd7b2bb12c2e9d447032d8a8c9ca272ed4c0ed2")
        self.assertEqual(actual.branch, branch)
        self.assertEqual(actual.detached, detached)
        self.assertFalse(actual.clean)
        self.assertEqual(working.staged_files, [
            FileStatus("new name Ω.txt", "old name Ω.txt", "R", "")
        ])
        self.assertEqual(working.unstaged_files, [])
        self.assertEqual(working.untracked_files, [])
        verify(self.git).git(
            "status", "--porcelain=v2", "-z", "-b",
            custom_environ={"GIT_OPTIONAL_LOCKS": "0"}
        )

    def read_status(self, records):
        when(self.git).git(
            "status", "--porcelain=v2", "-z", "-b",
            custom_environ={"GIT_OPTIONAL_LOCKS": "0"}
        ).thenReturn("\x00".join(records) + "\x00")
        return self.git.get_working_dir_status()
