from unittest import TestCase

from GitSavvy.core.git_command import GitCommand
from GitSavvy.tests.mockito import unstub, verify, when


class TestCommitRefResolution(TestCase):
    def tearDown(self) -> None:
        unstub()

    def test_no_query_without_requested_refs(self) -> None:
        git = GitCommand()
        when(git).git(...).thenRaise(AssertionError("Unexpected Git query"))
        self.assertEqual({}, git.resolve_commit_refs([]))
        verify(git, times=0).git(...)

    def test_one_batch_deduplicates_refs_and_omits_missing_or_noncommit_objects(self) -> None:
        git = GitCommand()
        sha1, sha256 = "a" * 40, "b" * 64
        when(git).git(
            "cat-file", "--batch-check=%(objectname) %(objecttype)",
            stdin="main@{1}\nREVERT_HEAD\nmissing\nblob\n"
        ).thenReturn(f"{sha1} commit\n{sha256} commit\nmissing missing\n{'c' * 40} blob\n")
        self.assertEqual(
            {"main@{1}": sha1, "REVERT_HEAD": sha256},
            git.resolve_commit_refs(["main@{1}", "REVERT_HEAD", "missing", "blob", "main@{1}"])
        )
        verify(git, times=1).git(...)
