from __future__ import annotations

import sublime

from unittesting import DeferrableTestCase

from GitSavvy.core.commands import worktrees
from GitSavvy.core.git_mixins.worktrees import WorktreeLocation
from GitSavvy.core.interfaces.log_graph_mouse import (
    PRESS_KEY, TARGET_KEY, GsLogGraphMouseListener, gs_log_graph_mouse
)
from GitSavvy.tests.mockito import mock, unstub, verify, when


class TestGraphLabelSelection(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_dismissing_hold_menu_releases_selection_anchor(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        view.run_command("append", {"characters": "* abcdef0 (topic) subject\n* 1234567 next\n"})
        target = sublime.Region(11, 16)
        view.sel().clear()
        view.sel().add(target)
        view.settings().set("git_savvy.log_graph_view", True)
        view.settings().set(PRESS_KEY, [1, 11, target.a, target.b])
        view.settings().set(TARGET_KEY, [target.a, target.b])
        command = gs_log_graph_mouse(view)
        when(command).target_at(11).thenReturn(target)
        when(command).branch_at(11).thenReturn("topic")
        when(command).branch_actions("topic").thenReturn([])
        captured = []
        when(view).show_popup_menu(...).thenAnswer(lambda *args: captured.append(args))

        command.show_menu(1, 11, "abcdef0")
        captured[0][1](-1)
        # The popup can swallow mouse-up, so no mouse release command follows.
        view.run_command("move", {"by": "lines", "forward": True})
        GsLogGraphMouseListener().on_selection_modified(view)
        self.assertTrue(view.sel()[0].empty(), repr(view.sel()[0]))
        self.assertEqual(view.rowcol(view.sel()[0].b)[0], 1)
        self.assertIsNone(view.settings().get(TARGET_KEY))

    def test_horizontal_drag_preserves_initial_label_in_both_directions(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        try:
            view.run_command("append", {"characters": "before feature/foo-bar after"})
            target = sublime.Region(7, 22)
            listener = GsLogGraphMouseListener()
            for selection, expected in (
                (sublime.Region(15, 28), sublime.Region(7, 28)),
                (sublime.Region(18, 0), sublime.Region(22, 0))
            ):
                view.settings().set(PRESS_KEY, [1, 15, target.a, target.b])
                view.settings().set("git_savvy.log_graph_mouse_target", [target.a, target.b])
                view.sel().clear()
                view.sel().add(selection)
                listener.on_selection_modified(view)
                self.assertEqual(list(view.sel()), [expected])
                self.assertIsNone(view.settings().get(PRESS_KEY))
                # Further native tracking must still include the original label.
                view.sel().clear()
                view.sel().add(selection)
                listener.on_selection_modified(view)
                self.assertEqual(list(view.sel()), [expected])
        finally:
            view.close()


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
        self.assertEqual(["Checkout in a new worktree", "-", "Push", "Pull", "Fetch origin/master"],
                         [label for label, _, _ in menu])
        self.assertEqual(
            ("gs_checkout_in_worktree", {"start_point": "abcdef0"}), menu[0][1:]
        )
        self.assertEqual(("gs_pull", {}), menu[3][1:])
        self.assertEqual(("gs_fetch", {"remote": "origin", "refspec": "master"}), menu[4][1:])

    def test_available_branch_with_upstream(self) -> None:
        menu = self.branch_menu(active=False, upstream=mock({"remote": "fork", "branch": "topic/nested", "canonical_name": "fork/topic/nested", "status": ""}))
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

    def test_branch_behind_upstream_offers_move(self) -> None:
        menu = self.branch_menu(active=False, upstream=mock({
            "remote": "origin", "branch": "master", "canonical_name": "origin/master",
            "status": "behind 2"
        }))
        self.assertEqual((
            "Move feature to origin/master", "gs_move_branch",
            {"branch": "feature", "target": "origin/master", "previous_tip": "abcdef0"}
        ), menu[4])

    def test_diverged_branch_still_offers_fetch_update(self) -> None:
        menu = self.branch_menu(active=False, upstream=mock({
            "remote": "origin", "branch": "master", "canonical_name": "origin/master",
            "status": "ahead 1, behind 2"
        }))
        self.assertEqual((
            "Update feature from origin/master (ff-only)", "gs_fetch",
            {"remote": "origin", "refspec": "master:feature"}
        ), menu[4])

    def test_sha_menu_shows_create_before_checkout_actions(self) -> None:
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
        self.assertEqual([
            "Create branch at abcdef0", "Create tag...", "-",
            "Checkout here", "Checkout in a worktree", "-"
        ], labels[:6])
        on_done(3)
        verify(window).run_command("gs_checkout_branch", {"branch": "abcdef0", "detach": True})
        on_done(4)
        verify(window).run_command(
            "gs_checkout_in_worktree", {"start_point": "abcdef0"}
        )

    def test_two_selected_commits_offer_direct_diff(self) -> None:
        view = sublime.active_window().new_file()
        view.set_scratch(True)
        self.addCleanup(view.close)
        view.run_command("append", {"characters": "* abcdef0 newer\n* 1234567 older\n"})
        view.settings().set("git_savvy.log_graph_view", True)
        view.settings().set(PRESS_KEY, [1, 2, 2, 9])
        view.settings().set("git_savvy.multi_selection", [[2, 9], [18, 25]])
        view.sel().clear()
        view.sel().add(sublime.Region(2, 9))
        command = gs_log_graph_mouse(view)
        when(command).target_at(2).thenReturn(sublime.Region(2, 9))
        when(command).branch_at(2).thenReturn(None)
        when(command).cherry_pick_and_revert_actions(["1234567", "abcdef0"]).thenReturn([])
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
        self.assertEqual(["Diff 1234567..abcdef0", "-"], labels[:2])
        on_done(0)
        verify(window).run_command("gs_diff", {
            "base_commit": "1234567", "target_commit": "abcdef0",
            "in_cached_mode": False, "disable_stage": True
        })

    def test_cached_worktree_location_detaches_even_without_branch_worktreepath(self) -> None:
        menu = self.branch_menu(active=False, upstream=None, worktree_path="/repo/feature")
        self.assertEqual([
            "Open worktree in a new window", "Checkout in a new worktree", "-", "Push..."
        ], [label for label, _, _ in menu])
        self.assertEqual(
            ("gs_checkout_in_worktree", {"start_point": "abcdef0"}), menu[1][1:]
        )
        self.assertEqual(
            ("gs_open_worktree_in_new_window", {"worktree": "/repo/feature"}), menu[0][1:]
        )

    def test_branch_checked_out_elsewhere_offers_fetch_but_not_pull(self) -> None:
        menu = self.branch_menu(
            active=False, upstream=mock({"remote": "origin", "branch": "master", "canonical_name": "origin/master"}), worktree_path="/repo/feature"
        )
        self.assertEqual([
            "Open worktree in a new window", "Checkout in a new worktree", "-", "Push", "Fetch origin/master"
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
