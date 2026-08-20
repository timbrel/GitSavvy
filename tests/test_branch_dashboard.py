from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.git_mixins.branches import Branch
from GitSavvy.core.git_mixins.worktrees import Worktree
from GitSavvy.core.interfaces.branch import BranchInterface
from GitSavvy.core.types import FullHash, ShortHash


class TestBranchDashboard(DeferrableTestCase):
    def test_worktrees_render_without_ahead_behind_information(self) -> None:
        interface = BranchRenderer.__new__(BranchRenderer)
        interface.state = {
            "descriptions": {},
            "worktree_deletions_in_progress": set()
        }
        branches = [
            branch("main", "a", active=True, worktree_path="/repo"),
            branch("feature", "b", worktree_path="/repo-feature")
        ]
        worktrees = [
            Worktree(
                "/repo-detached", FullHash("c" * 40), None,
                False, False, False
            )
        ]

        output = interface.render_branch_list(
            branches, worktrees, sort_by_recent=False,
            group_by_distance_to_head=True
        )

        self.assertIn("▸ aaaaaaa main", output)
        self.assertIn("<bbbbbbb> feature", output)
        self.assertIn("checked out at: /repo-feature", output)
        self.assertIn("<ccccccc> (DETACHED)", output)
        self.assertIn("checked out at: /repo-detached", output)


class BranchRenderer(BranchInterface):
    def to_short_hash(self, commit_hash: FullHash | ShortHash) -> ShortHash:
        return ShortHash(commit_hash[:7])

    def nice_path(self, path: str) -> str:
        return path


def branch(
    name: str,
    hash_char: str,
    *,
    active: bool = False,
    worktree_path: str | None = None
) -> Branch:
    return Branch(
        name=name,
        remote=None,
        canonical_name=name,
        commit_hash=FullHash(hash_char * 40),
        commit_msg="",
        active=active,
        is_remote=False,
        committerdate=0,
        human_committerdate="now",
        relative_committerdate="now",
        upstream=None,
        distance_to_head=None,
        worktree_path=worktree_path
    )
