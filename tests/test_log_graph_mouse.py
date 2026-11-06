from __future__ import annotations

import sublime

from unittesting import DeferrableTestCase

from GitSavvy.core.commands import worktrees
from GitSavvy.core.git_mixins.worktrees import WorktreeLocation
from GitSavvy.core.interfaces.log_graph_mouse import PRESS_KEY, gs_log_graph_mouse
from GitSavvy.tests.mockito import mock, unstub, verify, when


class TestLongPressCheckoutActions(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_branch_without_upstream(self) -> None:
        menu = self.branch_menu(active=False, upstream=None)
        self.assertEqual(["Checkout here", "Checkout in a worktree", "-", "Push..."],
                         [label for label, _, _ in menu])
        self.assertEqual(("gs_checkout_branch", {"branch": "feature"}), menu[0][1:])
        self.assertEqual(
            ("gs_checkout_in_worktree", {"start_point": "feature"}), menu[1][1:]
        )

    def test_current_branch_with_upstream(self) -> None:
        menu = self.branch_menu(
            active=True, upstream=mock({"remote": "origin", "branch": "master", "canonical_name": "origin/master"}),
            worktree_path="/repo/current", is_current=True
        )
        self.assertEqual(["Checkout in a worktree", "-", "Push", "Pull", "Fetch origin/master"],
                         [label for label, _, _ in menu])
        self.assertEqual(
            ("gs_checkout_in_worktree", {"start_point": "abcdef0"}), menu[0][1:]
        )
        self.assertEqual(("gs_pull", {}), menu[3][1:])
        self.assertEqual(("gs_fetch", {"remote": "origin", "refspec": "master"}), menu[4][1:])

    def test_available_branch_with_upstream(self) -> None:
        menu = self.branch_menu(active=False, upstream=mock({"remote": "fork", "branch": "topic/nested", "canonical_name": "fork/topic/nested"}))
        self.assertEqual([
            "Checkout here", "Checkout in a worktree", "-", "Push",
            "Update feature from fork/topic/nested (ff-only)", "Fetch fork/topic/nested"
        ], [label for label, _, _ in menu])
        self.assertEqual(
            ("gs_fetch", {"remote": "fork", "refspec": "topic/nested:feature"}), menu[4][1:]
        )
        self.assertEqual(
            ("gs_fetch", {"remote": "fork", "refspec": "topic/nested"}), menu[5][1:]
        )

    def test_sha_menu_starts_with_detached_checkout_actions(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        view.run_command("append", {"characters": "* abcdef0 subject\n"})
        target = sublime.Region(2, 9)
        view.sel().clear()
        view.sel().add(target)
        view.settings().set("git_savvy.log_graph_view", True)
        view.settings().set(PRESS_KEY, [1, 2, 2, 9])
        command = gs_log_graph_mouse(view)
        when(command).target_at(2).thenReturn(target)
        when(command).branch_at(2).thenReturn(None)
        when(command).fixup_actions(2, "abcdef0").thenReturn([])
        when(command).cherry_pick_and_revert_actions(["abcdef0"]).thenReturn([])
        when(view).match_selector(2, "string.other.rebase-hint.git-savvy").thenReturn(False)
        when(view).match_selector(2, "constant.numeric.graph.commit-hash.git-savvy").thenReturn(True)
        captured = []
        when(view).show_popup_menu(...).thenAnswer(lambda *args: captured.append(args))
        window = mock()
        when(view).window().thenReturn(window)
        when(window).active_view().thenReturn(view)
        when(window).run_command(...).thenReturn(None)

        command.show_menu(1, 2, "abcdef0")

        labels, on_done, _ = captured[0]
        self.assertEqual(["Checkout here", "Checkout in a worktree", "-"], labels[:3])
        on_done(0)
        verify(window).run_command("gs_checkout_branch", {"branch": "abcdef0", "detach": True})
        on_done(1)
        verify(window).run_command(
            "gs_checkout_in_worktree", {"start_point": "abcdef0"}
        )

    def test_cached_worktree_location_detaches_even_without_branch_worktreepath(self) -> None:
        menu = self.branch_menu(active=False, upstream=None, worktree_path="/repo/feature")
        self.assertEqual([
            "Checkout in a worktree", "Open worktree in a new window", "-", "Push..."
        ], [label for label, _, _ in menu])
        self.assertEqual(
            ("gs_checkout_in_worktree", {"start_point": "abcdef0"}), menu[0][1:]
        )
        self.assertEqual(
            ("gs_open_worktree_in_new_window", {"worktree": "/repo/feature"}), menu[1][1:]
        )

    def test_branch_checked_out_elsewhere_offers_fetch_but_not_pull(self) -> None:
        menu = self.branch_menu(
            active=False, upstream=mock({"remote": "origin", "branch": "master", "canonical_name": "origin/master"}), worktree_path="/repo/feature"
        )
        self.assertEqual([
            "Checkout in a worktree", "Open worktree in a new window", "-", "Push", "Fetch origin/master"
        ], [label for label, _, _ in menu])

    def test_open_worktree_command_only_opens_the_existing_checkout(self) -> None:
        command = worktrees.gs_open_worktree_in_new_window(mock())
        when(command).get_worktrees().thenRaise(AssertionError("Redundant worktree query"))
        when(command).create_new_worktree(...).thenRaise(AssertionError("Existing worktree"))
        when(command).open_worktree_in_new_window("/repo/feature").thenReturn(None)

        worktrees.gs_open_worktree_in_new_window.run.__wrapped__(command, worktree="/repo/feature")

        verify(command).open_worktree_in_new_window("/repo/feature")

    def test_worktree_checkout_defaults_to_resolved_head(self) -> None:
        command = worktrees.gs_checkout_in_worktree(mock())
        when(command).resolve_commitish("HEAD").thenReturn("abcdef0")
        when(command).create_new_worktree("abcdef0").thenReturn("/repo/new")
        when(command).open_worktree_in_new_window("/repo/new", then=command.show_graph).thenReturn(None)

        worktrees.gs_checkout_in_worktree.run.__wrapped__(command)

        verify(command).resolve_commitish("HEAD")
        verify(command).create_new_worktree("abcdef0")
        verify(command).open_worktree_in_new_window("/repo/new", then=command.show_graph)

    def test_worktree_checkout_passes_available_branch_to_git(self) -> None:
        self.check_worktree_checkout("feature")

    def test_worktree_checkout_passes_sha_to_git(self) -> None:
        self.check_worktree_checkout("abcdef0")

    def branch_menu(
        self, *, active: bool, upstream: object, worktree_path: str | None = None,
        is_current: bool = False
    ) -> list[tuple[str, str, dict]]:
        command = gs_log_graph_mouse(mock())
        when(command).current_state().thenReturn({
            "worktree_locations": [WorktreeLocation(worktree_path, "feature", None, is_current)]
            if worktree_path else []
        })
        when(command).get_worktree_locations().thenRaise(AssertionError("Discovery in menu"))
        when(command).get_worktrees().thenRaise(AssertionError("Git query in menu"))
        when(command).get_branches().thenReturn([
            mock({
                "name": "feature", "is_remote": False, "active": active,
                "upstream": upstream, "worktree_path": None, "commit_hash": "abcdef0"
            })
        ])
        return command.branch_actions("feature")

    def check_worktree_checkout(self, start_point: str) -> None:
        command = worktrees.gs_checkout_in_worktree(mock())
        when(command).get_worktrees().thenRaise(AssertionError("Redundant worktree query"))
        when(command).create_new_worktree(start_point).thenReturn("/repo/new")
        when(command).open_worktree_in_new_window("/repo/new", then=command.show_graph).thenReturn(None)

        worktrees.gs_checkout_in_worktree.run.__wrapped__(command, start_point)

        verify(command).create_new_worktree(start_point)
        verify(command).open_worktree_in_new_window("/repo/new", then=command.show_graph)
