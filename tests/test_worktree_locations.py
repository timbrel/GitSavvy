from __future__ import annotations

import os
import subprocess
import tempfile

from unittesting import DeferrableTestCase

from GitSavvy.core.git_mixins.worktrees import WorktreesMixin
from GitSavvy.tests.mockito import mock, unstub, verify, when


class TestWorktreeLocations(DeferrableTestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(unstub)
        self.root = directory.name
        self.repo = os.path.join(self.root, "main")
        self.git("init", self.repo)
        self.git("-C", self.repo, "-c", "user.name=Test", "-c", "user.email=test@example.com",
                 "commit", "--allow-empty", "-m", "initial")
        self.common_dir = os.path.join(self.repo, ".git")

    def test_matches_git_from_main_and_linked_checkout(self) -> None:
        attached = os.path.join(self.root, "feature space")
        self.git("-C", self.repo, "worktree", "add", "-b", "feature", attached)
        for name in ("detached-one", "detached-two"):
            self.git("-C", self.repo, "worktree", "add", "--detach", os.path.join(self.root, name))
        self.assert_matches_git(self.repo, self.common_dir)
        git_dir = self.git("-C", attached, "rev-parse", "--absolute-git-dir").strip()
        self.assert_matches_git(attached, git_dir)

    def test_packed_refs_do_not_require_attached_hash_resolution(self) -> None:
        self.git("-C", self.repo, "pack-refs", "--all")
        self.assert_matches_git(self.repo, self.common_dir)

    def test_refresh_observes_detach_and_removal(self) -> None:
        linked = os.path.join(self.root, "feature")
        self.git("-C", self.repo, "worktree", "add", "-b", "feature", linked)
        self.assert_matches_git(self.repo, self.common_dir)
        self.git("-C", linked, "checkout", "--detach")
        self.assert_matches_git(self.repo, self.common_dir)
        self.git("-C", self.repo, "worktree", "remove", linked)
        self.assert_matches_git(self.repo, self.common_dir)

    def test_bare_repository_has_only_linked_locations(self) -> None:
        bare = os.path.join(self.root, "bare.git")
        self.git("clone", "--bare", self.repo, bare)
        linked = os.path.join(self.root, "bare-linked")
        self.git("-C", bare, "worktree", "add", "--detach", linked)
        self.common_dir = bare
        self.assert_matches_git(bare, bare)
        git_dir = self.git("-C", linked, "rev-parse", "--absolute-git-dir").strip()
        self.assert_matches_git(linked, git_dir)

    def test_current_separate_gitdir_uses_known_checkout_path(self) -> None:
        separate = os.path.join(self.root, "metadata")
        self.git("-C", self.repo, "init", "--separate-git-dir", separate)
        command = mock({"repo_path": self.repo, "git_dir": separate, "git_common_dir": separate})
        when(command).update_store(...).thenReturn(None)
        locations = WorktreesMixin.get_worktree_locations(command)
        # Git's list can report the separate gitdir as the main checkout;
        # our narrower reader knows this checkout's actual root.
        self.assertEqual([self.repo], [wt.path for wt in locations])
        self.assertTrue(locations[0].is_current)

    def assert_matches_git(self, repo: str, git_dir: str) -> None:
        command = mock({"repo_path": repo, "git_dir": git_dir, "git_common_dir": self.common_dir})
        when(command).update_store(...).thenReturn(None)
        when(command).git(...).thenRaise(AssertionError("Filesystem reader must not launch Git"))
        output = self.git("-C", repo, "worktree", "list", "--porcelain")
        complete = WorktreesMixin._parse_worktree_entries(command, output.splitlines())
        locations = WorktreesMixin.get_worktree_locations(command)
        # Git may expand Windows 8.3 aliases. Compare filesystem identities,
        # not spellings or the porcelain parser's literal is_current check.
        expected = [
            (os.path.normcase(os.path.realpath(wt.path)), wt.branch_name,
             wt.commit_hash if wt.is_detached else None, os.path.samefile(wt.path, repo))
            for wt in complete
        ]
        actual = [
            (os.path.normcase(os.path.realpath(wt.path)),
             wt.branch_name, wt.detached_commit, wt.is_current)
            for wt in locations
        ]
        self.assertCountEqual(expected, actual)
        verify(command).update_store({"worktree_locations": locations})

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", *args], stderr=subprocess.PIPE).decode("utf-8")
