from pathlib import Path
import os
import tempfile
from unittest import TestCase

from GitSavvy.core import cat_file, debug
from GitSavvy.core.exceptions import AbortedGitCall, DetachedView, GitSavvyError
from GitSavvy.core.git_command import GitCommand
from GitSavvy.tests.mockito import unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class TestCommitRefResolution(TestCase):
    def tearDown(self) -> None:
        unstub()

    def test_repository_discovery_failure_preserves_git_error_mapping(self) -> None:
        git = GitCommand()
        when(git).get_repo_path().thenRaise(ValueError("Not a git repository."))
        when(GitSavvyError).show_error_panel().thenReturn(None)
        with self.assertRaises(GitSavvyError) as raised:
            git.resolve_commit_refs(["HEAD"])
        self.assertEqual("Not a git repository.", str(raised.exception))
        self.assertTrue(raised.exception.show_panel)
        verify(GitSavvyError, times=1).show_error_panel()

    def test_detached_view_is_logged_without_panel_and_reraised(self) -> None:
        git = GitCommand()
        error = DetachedView("View already closed.")
        when(git).get_repo_path().thenRaise(error)
        when(GitSavvyError).show_error_panel().thenRaise(AssertionError("Unexpected error panel"))
        when(debug).log_error(...).thenReturn(None)
        with self.assertRaises(DetachedView) as raised:
            git.resolve_commit_refs(["HEAD"])
        self.assertIs(error, raised.exception)
        verify(GitSavvyError, times=0).show_error_panel()
        verify(debug, times=1).log_error("View already closed.")

    @p.expand([("show_panel", True), ("suppress_panel", False), ("ignore", False)])
    def test_full_resolution_preserves_discovery_error_policy(self, policy, panel) -> None:
        git = DiscoveringGit()
        when(git).get_repo_path().thenRaise(ValueError("Not a git repository."))
        when(GitSavvyError).show_error_panel().thenReturn(None)
        with self.assertRaises(GitSavvyError) as raised:
            git.resolve("HEAD", on_error=policy)
        self.assertEqual("Not a git repository.", str(raised.exception))
        self.assertEqual(panel, raised.exception.show_panel)
        verify(GitSavvyError, times=int(panel)).show_error_panel()

    def test_full_resolution_preserves_detached_view_logging(self) -> None:
        git = DiscoveringGit()
        error = DetachedView("View already closed.")
        when(git).get_repo_path().thenRaise(error)
        when(GitSavvyError).show_error_panel().thenRaise(AssertionError("Unexpected error panel"))
        when(debug).log_error(...).thenReturn(None)
        with self.assertRaises(DetachedView) as raised:
            git.resolve("HEAD")
        self.assertIs(error, raised.exception)
        verify(debug, times=1).log_error("View already closed.")
        verify(GitSavvyError, times=0).show_error_panel()

    def test_full_hash_resolution_uses_metadata_for_any_object_type(self) -> None:
        git = ModernGit()
        when(git).git(...).thenRaise(AssertionError("Unexpected Git process"))
        for kind in ("commit", "tree", "blob", "tag"):
            oid = "a" * 40
            when(cat_file).read_object_metadata(
                git, [kind], show_panel_on_error=False
            ).thenReturn([f"{oid} {kind}"])
            self.assertEqual(oid, git.resolve(kind))
        verify(git, times=0).git(...)

    @p.expand([("HEAD", True), ("HEAD@{1}", False)])
    def test_resolution_keeps_rev_parse_for_short_or_reflog_refs(self, ref, short) -> None:
        git = ModernGit()
        when(cat_file).read_object_metadata(...).thenRaise(AssertionError("Unexpected metadata query"))
        when(git).git(
            "rev-parse", "--verify", "--short" if short else None, ref,
            throw_on_error=True, show_panel_on_error=True
        ).thenReturn("abcdef0\n")
        self.assertEqual("abcdef0", git.resolve(ref, short=short))
        verify(cat_file, times=0).read_object_metadata(...)

    @p.expand([("ignore", False, False), ("suppress_panel", True, False), ("show_panel", True, True)])
    def test_missing_metadata_preserves_rev_parse_error_policy(self, policy, throw, panel) -> None:
        git = ModernGit()
        when(cat_file).read_object_metadata(
            git, ["missing"], show_panel_on_error=False
        ).thenReturn(["missing missing"])
        when(git).git(
            "rev-parse", "--verify", None, "missing",
            throw_on_error=throw, show_panel_on_error=panel
        ).thenReturn("")
        self.assertEqual(None if policy == "ignore" else "", git.resolve("missing", on_error=policy))
        verify(git, times=1).git(...)
        verify(cat_file, times=1).read_object_metadata(...)

    @p.expand([((2, 44, 1), True), ((2, 45, 0), False)])
    def test_unsupported_contexts_keep_rev_parse(self, version, reftable) -> None:
        git = ModernGit()
        git.git_version = version
        git.repo_uses_reftable = reftable
        when(cat_file).read_object_metadata(...).thenRaise(AssertionError("Unexpected metadata query"))
        when(git).git("rev-parse", "--verify", None, "HEAD",
                       throw_on_error=True, show_panel_on_error=True).thenReturn("a" * 40 + "\n")
        self.assertEqual("a" * 40, git.resolve("HEAD"))
        verify(cat_file, times=0).read_object_metadata(...)

    @p.expand([((2, 44, 1), True), ((2, 45, 0), False)])
    def test_unsupported_metadata_batch_uses_one_shot_git(self, version, reftable) -> None:
        git = ModernGit()
        git.git_version = version
        git.repo_uses_reftable = reftable
        when(git).get_repo_path().thenReturn("/repo")
        when(cat_file).context_files(...).thenRaise(AssertionError("Unexpected freshness checks"))
        when(git).git(
            "cat-file", cat_file.BATCH_CHECK, stdin="HEAD\nmissing\n",
            show_panel_on_error=False
        ).thenReturn("a" * 40 + " commit\nmissing missing\n")
        self.assertEqual(
            ["a" * 40 + " commit", "missing missing"],
            cat_file.read_object_metadata(git, ["HEAD", "missing"], show_panel_on_error=False)
        )
        verify(git, times=1).git(...)
        verify(cat_file, times=0).context_files(...)

    def test_transport_failure_falls_back_without_opening_a_panel(self) -> None:
        git = ModernGit()
        when(cat_file).read_object_metadata(git, ["HEAD"], show_panel_on_error=False).thenRaise(
            GitSavvyError("reader failed", show_panel=False)
        )
        when(git).git("rev-parse", "--verify", None, "HEAD",
                       throw_on_error=False, show_panel_on_error=False).thenReturn("a" * 40 + "\n")
        self.assertEqual("a" * 40, git.resolve("HEAD", on_error="ignore"))

    def test_aborted_query_is_not_retried(self) -> None:
        git = ModernGit()
        when(cat_file).read_object_metadata(...).thenRaise(AbortedGitCall("aborted", show_panel=False))
        when(git).git(...).thenRaise(AssertionError("Unexpected retry"))
        with self.assertRaises(AbortedGitCall):
            git.resolve("HEAD", on_error="ignore")

    def test_no_query_without_requested_refs(self) -> None:
        git = GitCommand()
        when(git).git(...).thenRaise(AssertionError("Unexpected Git query"))
        self.assertEqual({}, git.resolve_commit_refs([]))
        verify(git, times=0).git(...)

    def test_one_batch_deduplicates_refs_and_omits_missing_or_noncommit_objects(self) -> None:
        git = GitCommand()
        sha1, sha256 = "a" * 40, "b" * 64
        when(cat_file).read_object_metadata(
            git, ["main", "REVERT_HEAD", "missing", "blob"]
        ).thenReturn([f"{sha1} commit", f"{sha256} commit", "missing missing", f"{'c' * 40} blob"])
        self.assertEqual(
            {"main": sha1, "REVERT_HEAD": sha256},
            git.resolve_commit_refs(["main", "REVERT_HEAD", "missing", "blob", "main"])
        )
        verify(cat_file, times=1).read_object_metadata(...)

    def test_reflog_decorations_omit_failures_and_deduplicate_selectors(self) -> None:
        git = GitCommand()
        when(cat_file).read_object_metadata(...).thenRaise(AssertionError("Unexpected metadata query"))
        when(git).git(
            "rev-parse", "--verify", "--quiet", "HEAD@{0}^{commit}",
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("a" * 40 + "\n")
        when(git).git(
            "rev-parse", "--verify", "--quiet", "HEAD@{999999}^{commit}",
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("")
        self.assertEqual(
            {"HEAD@{0}": "a" * 40},
            git.resolve_commit_refs(["HEAD@{999999}", "HEAD@{0}", "HEAD@{0}"])
        )
        verify(git, times=2).git(...)
        verify(cat_file, times=0).read_object_metadata(...)

    @p.expand([(False,), (True,)])
    def test_ref_backend_is_cached_across_command_instances(self, reftable) -> None:
        with tempfile.TemporaryDirectory(prefix="gitsavvy-backend-cache-") as folder:
            common = Path(folder) / ".git"
            common.mkdir()
            when(os.path).isdir(str(common / "reftable")).thenReturn(reftable)
            for _ in range(2):
                git = GitCommand()
                when(git).get_repo_path().thenReturn(folder)
                self.assertEqual(reftable, git.repo_uses_reftable)
            verify(os.path, times=1).isdir(str(common / "reftable"))

class DiscoveringGit(GitCommand):
    git_version = (2, 45, 0)


class ModernGit(DiscoveringGit):
    repo_uses_reftable = True
