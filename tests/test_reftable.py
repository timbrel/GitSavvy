from __future__ import annotations

import os
import subprocess
import tempfile
from unittest import TestCase

from GitSavvy.core import reftable
from GitSavvy.tests.parameterized import parameterized as p


class TestReadHead(TestCase):
    """Compare the reader with Git on stacks that Git wrote itself."""

    def setUp(self) -> None:
        version = self.git("version").split()[2].split(".")
        if tuple(map(int, version[:2])) < (2, 45):
            self.skipTest("Reftable repositories require Git 2.45+")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        self.repo = os.path.join(self.root, "main")
        self.stack = os.path.join(self.repo, ".git", "reftable")

    @p.expand([("sha1", 40), ("sha256", 64)])
    def test_attached_and_detached_heads(self, object_format: str, hash_length: int) -> None:
        self.init(object_format)
        self.assertEqual("ref: refs/heads/main", self.assert_matches_git(self.repo))
        self.git("-C", self.repo, "checkout", "--detach")
        self.assertEqual(hash_length, len(self.assert_matches_git(self.repo)))

    def test_linked_worktrees_have_their_own_stacks(self) -> None:
        self.init()
        attached = os.path.join(self.root, "attached")
        detached = os.path.join(self.root, "detached")
        self.git("-C", self.repo, "worktree", "add", "-b", "topic", attached)
        self.git("-C", self.repo, "worktree", "add", "--detach", detached)
        self.git("-C", attached, "switch", "-c", "renamed")
        self.assertEqual("ref: refs/heads/renamed", self.assert_matches_git(attached))
        self.assert_matches_git(detached)
        self.assertEqual("ref: refs/heads/main", self.assert_matches_git(self.repo))

    def test_newest_table_wins(self) -> None:
        self.init()
        self.git("-C", self.repo, "checkout", "--detach")
        self.git("-C", self.repo, "checkout", "-b", "later")
        self.assertGreater(len(self.tables()), 1)
        self.assertEqual("ref: refs/heads/later", self.assert_matches_git(self.repo))

    def test_deleted_head_shadows_older_tables(self) -> None:
        self.init()
        self.git("-C", self.repo, "update-ref", "-d", "--no-deref", "HEAD")
        self.assertGreater(len(self.tables()), 1)
        self.assertEqual("", self.assert_matches_git(self.repo))

    def test_head_after_a_full_block_of_root_refs(self) -> None:
        # Root refs sort before HEAD; with small blocks they push HEAD into
        # the second, padded ref block of the same table.
        self.init()
        self.git("-C", self.repo, "config", "reftable.blockSize", "256")
        for name in ("AAA", "AAB", "AAC", "AAD", "AAE", "AAF", "AAG", "AAH", "ABA", "ABB"):
            self.git("-C", self.repo, "update-ref", name + "_HEAD", "HEAD")
        self.git("-C", self.repo, "pack-refs", "--all")
        table, = self.tables()
        with open(os.path.join(self.stack, table), "rb") as f:
            f.seek(256)
            self.assertEqual(b"r", f.read(1), "HEAD must not fit into the first block")
        self.assertEqual("ref: refs/heads/main", self.assert_matches_git(self.repo))

    def test_missing_stack_reads_as_missing_head(self) -> None:
        self.assertEqual("", reftable.read_head(os.path.join(self.root, "nope")))

    def test_stack_that_lists_a_vanished_table_reads_as_missing_head(self) -> None:
        self.init()
        with open(os.path.join(self.stack, "tables.list"), "a", encoding="utf-8") as f:
            f.write("0x000000000099-0x000000000099-deadbeef.ref\n")
        self.assertEqual("", reftable.read_head(self.stack))

    def test_unknown_table_version_reads_as_missing_head(self) -> None:
        self.init()
        corrupt_table_version(self.stack)
        self.assertEqual("", reftable.read_head(self.stack))

    def init(self, object_format: str = "sha1") -> None:
        self.git("init", "--initial-branch=main", "--ref-format=reftable",
                 "--object-format=" + object_format, self.repo)
        self.git("-C", self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "--allow-empty", "-m", "initial")

    def tables(self) -> list[str]:
        with open(os.path.join(self.stack, "tables.list"), encoding="utf-8") as f:
            return f.read().split()

    def assert_matches_git(self, checkout: str) -> str:
        git_dir = self.git("-C", checkout, "rev-parse", "--absolute-git-dir").strip()
        actual = reftable.read_head(os.path.join(git_dir, "reftable"))
        self.assertEqual(git_head(checkout), actual)
        return actual

    def git(self, *args: str) -> str:
        # Keep every transaction in its own table so stacks have history.
        env = {**os.environ, "GIT_TEST_REFTABLE_AUTOCOMPACTION": "false"}
        return subprocess.check_output(
            ["git", *args], stderr=subprocess.PIPE, env=env
        ).decode("utf-8")


def git_head(checkout: str) -> str:
    for args, prefix in (
        (("symbolic-ref", "-q", "HEAD"), "ref: "),
        (("rev-parse", "--verify", "-q", "HEAD"), ""),
    ):
        result = subprocess.run(["git", "-C", checkout, *args], capture_output=True)
        if result.returncode == 0:
            return prefix + result.stdout.decode("utf-8").strip()
    return ""


def corrupt_table_version(stack: str) -> None:
    with open(os.path.join(stack, "tables.list"), encoding="utf-8") as f:
        newest = f.read().split()[-1]
    path = os.path.join(stack, newest)
    os.chmod(path, 0o644)  # Git writes tables read-only.
    with open(path, "r+b") as f:
        f.seek(4)
        f.write(b"\x09")
