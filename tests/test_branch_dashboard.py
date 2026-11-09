from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.git_mixins.branches import Branch, Upstream
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

    def test_ci_results_follow_the_tracking_info(self) -> None:
        interface = BranchRenderer.__new__(BranchRenderer)
        interface.state = {
            "descriptions": {"red": "a description"},
            "github_ci_results": {
                ("ok", FullHash("a" * 40)): "success",
                ("red", FullHash("b" * 40)): "failure",
                ("pending", FullHash("c" * 40)): "pending",
                ("no-checks", FullHash("d" * 40)): "no_checks",
                ("moved", FullHash("0" * 40)): "failure",
            },
            "worktree_deletions_in_progress": set()
        }
        branches = [
            branch("ok", "a", upstream=True),
            branch("red", "b", upstream=True),
            branch("pending", "c", upstream=True),
            branch("no-checks", "d", upstream=True),
            branch("moved", "e", upstream=True),
        ]

        output = interface.render_branch_list(
            branches, [], sort_by_recent=False, group_by_distance_to_head=False
        )

        self.assertEqual([
            "    aaaaaaa ok now, (origin/ok) [ok]",
            "    bbbbbbb red (origin/red) [red] - a description",
            "    ccccccc pending (origin/pending) [pending]",
            "    ddddddd no-checks (origin/no-checks)",
            "    eeeeeee moved (origin/moved)",
        ], output.splitlines())


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
    worktree_path: str | None = None,
    upstream: bool = False
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
        upstream=Upstream("origin", name, "origin/" + name, "") if upstream else None,
        distance_to_head=None,
        worktree_path=worktree_path
    )
