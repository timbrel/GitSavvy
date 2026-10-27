from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.commands import log_graph_main_actions as actions
from GitSavvy.core.git_mixins.worktrees import WorktreeLocation
from GitSavvy.core.types import FullHash, ShortHash
from GitSavvy.tests.mockito import mock, unstub, verify, when


COMMIT = ShortHash("abcdef0")


class TestGraphWorktreeActions(DeferrableTestCase):
    def setUp(self) -> None:
        self.window = mock()
        self.view = mock()
        when(self.view).id().thenReturn(42)
        self.command = actions.gs_log_graph_action(self.window)
        when(self.command)._get_file_path(self.view).thenReturn(None)
        when(self.command).in_bisect().thenReturn(False)
        when(actions).describe_head(self.view, ...).thenReturn(None)
        when(actions).next_decorated_commit_down(self.view, ...).thenReturn(None)

    def tearDown(self) -> None:
        unstub()

    def test_checked_out_branch_is_opened_not_checked_out_or_deleted(self) -> None:
        wt = worktree("/repo/feature", "feature")
        info = {"commit": COMMIT, "local_branches": ["feature", "available"]}
        when(self.command).open_worktree_in_new_window(wt.path).thenReturn(None)
        menu = self.menu(info, [wt])

        self.assertIn("Open feature in a new window", menu)
        self.assertNotIn("Checkout 'feature'", menu)
        self.assertIn("Checkout 'available'", menu)
        self.assertNotIn("Delete branch 'feature'", menu)
        self.assertIn("Delete worktree at feature", menu)
        self.assertIn("Delete branch 'available'", menu)
        menu["Open feature in a new window"]()
        verify(self.command).open_worktree_in_new_window(wt.path)

    def test_single_detached_worktree_uses_generic_open_and_hash_for_delete(self) -> None:
        menu = self.menu({"commit": COMMIT}, [worktree("/repo/detached")])

        self.assertIn("Open detached worktree in a new window", menu)
        self.assertIn(f"Delete worktree at {COMMIT}", menu)

    def test_multiple_detached_worktrees_use_shortened_paths_and_correct_targets(self) -> None:
        worktrees = [worktree("/repo/topic-one"), worktree("/repo/topic-two")]
        when(actions).delete_worktree_from_graph(...).thenReturn(None)
        menu = self.menu({"commit": COMMIT}, worktrees)
        for wt, suffix in zip(worktrees, ["one", "two"]):
            self.assertIn(f"Open {suffix} in a new window", menu)
            menu[f"Delete worktree at {suffix}"]()
            verify(actions).delete_worktree_from_graph(self.command, wt.path)

    def test_current_and_unrelated_worktrees_are_not_offered(self) -> None:
        worktrees = [
            worktree("/repo/current", "current", is_current=True),
            worktree("/repo/elsewhere", "elsewhere"),
            worktree("/repo/old", commit="1234567")
        ]
        self.assertEqual([], actions.worktree_targets(
            {"commit": COMMIT, "local_branches": ["current"]}, worktrees
        ))

    def test_current_detached_worktree_still_disambiguates_other_targets(self) -> None:
        targets = actions.worktree_targets({"commit": COMMIT}, [
            worktree("/repo/topic-one", is_current=True),
            worktree("/repo/topic-two")
        ])
        self.assertEqual(["two"], [name for _, name in targets])

    def test_delete_decoration_menu_also_replaces_branch_deletion(self) -> None:
        command = actions.gs_log_graph_delete_decoration(self.window)
        when(command).current_state().thenReturn({
            "worktree_locations": [worktree("/repo/feature", "feature")]
        })
        when(command).get_worktrees().thenRaise(AssertionError("Git query on keypress"))
        when(command).get_worktree_locations().thenRaise(AssertionError("Filesystem scan on keypress"))
        menu = dict(command.delete_actions(self.view, {
            "commit": COMMIT, "local_branches": ["feature"], "tags": ["v1"]
        }))
        self.assertEqual({"Delete worktree at feature", "Delete tag 'v1'"}, set(menu))

    def test_missing_worktree_state_does_not_query_git(self) -> None:
        info = {"commit": COMMIT, "local_branches": ["feature"]}
        for command_class in (actions.gs_log_graph_action, actions.gs_log_graph_delete_decoration):
            command = command_class(self.window)
            when(command).current_state().thenReturn({})
            when(command).get_worktrees().thenRaise(AssertionError("Git query on keypress"))
            when(command).get_worktree_locations().thenRaise(AssertionError("Filesystem scan on keypress"))
            if command_class is actions.gs_log_graph_action:
                when(command)._get_file_path(self.view).thenReturn(None)
                when(command).in_bisect().thenReturn(False)
                menu = command.actions_for_single_line(
                    self.view, info, {"feature": mock({"upstream": None})}
                )
            else:
                menu = command.delete_actions(self.view, info)
            self.assertIn("Delete branch 'feature'", dict(menu))

    def menu(self, info, worktrees):
        when(self.command).current_state().thenReturn({"worktree_locations": worktrees})
        when(self.command).get_worktrees().thenRaise(AssertionError("Git query on keypress"))
        when(self.command).get_worktree_locations().thenRaise(AssertionError("Filesystem scan on keypress"))
        branches = {name: mock({"upstream": None}) for name in info.get("local_branches", [])}
        return dict(self.command.actions_for_single_line(self.view, info, branches))


def worktree(
    path: str, branch: str | None = None, *, is_current: bool = False, commit: str = COMMIT
) -> WorktreeLocation:
    return WorktreeLocation(path, branch, None if branch else FullHash(commit.ljust(40, "0")), is_current)
