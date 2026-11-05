from typing import Callable

import sublime
from unittesting import DeferrableTestCase

from GitSavvy.core.interfaces import log_graph_branch_drag as drag
from GitSavvy.core.interfaces.log_graph import GsLogGraphCursorListener
from GitSavvy.core.types import FullHash
from GitSavvy.core.view import replace_view_content
from GitSavvy.tests.mockito import mock, unstub, verify, when


CONTENT = (
    "\n  REPO: test\n\n"
    "● 40e3d394 First commit\n"
    "● 718ab2b9 (throttling, origin/throttling) Second commit\n"
    "● c0d0646f (other, tag: v1) Third commit\n"
)


class TestBranchDrag(DeferrableTestCase):
    def setUp(self) -> None:
        self.view = sublime.active_window().new_file()
        self.view.set_scratch(True)
        self.view.assign_syntax("Packages/GitSavvy/syntax/graph.sublime-syntax")
        replace_view_content(self.view, CONTENT)
        self.view.set_read_only(True)
        self.view.settings().set("git_savvy.log_graph_view", True)
        # Keep unrelated graph rendering out of these mouse-command tests.
        when(GsLogGraphCursorListener).is_applicable(...).thenReturn(False)
        self.branches = [
            mock({"name": "throttling", "is_remote": False, "active": False, "worktree_path": None,
                  "commit_hash": FullHash("718ab2b9" + "0" * 32)}),
            mock({"name": "other", "is_remote": False, "active": False, "worktree_path": None,
                  "commit_hash": FullHash("c0d0646f" + "0" * 32)}),
            mock({"name": "throttling", "is_remote": True}),
        ]
        when(drag.gs_log_graph_branch_drag).current_state().thenReturn({"branches": self.branches})
        when(drag.gs_log_graph_branch_drag).get_branches().thenReturn(self.branches)
        when(drag.gs_log_graph_branch_drag).get_repo_path().thenReturn("/repo")
        when(sublime.View).window_to_text(...).thenAnswer(lambda xy: int(xy[0]))
        self.menus = []
        when(sublime.View).show_popup_menu(...).thenAnswer(
            lambda labels, callback, point: self.menus.append((labels, callback, point))
        )
        self.scheduled = []
        when(sublime).set_timeout(...).thenAnswer(
            lambda callback, *args: self.scheduled.append(callback)
        )

    def tearDown(self) -> None:
        drag.cancel_branch_drag(self.view)
        self.view.close()
        unstub()

    def test_hold_starts_drag_when_branch_metadata_is_not_cached(self) -> None:
        for cached in ({}, {"branches": []}):
            when(drag.gs_log_graph_branch_drag).current_state().thenReturn(cached)
            self.start(activate=False)
            self.assertIsNotNone(self.state())
            token = self.state()["token"]
            drag.activate_held_drag(self.view, token)
            self.assertEqual(self.state()["phase"], "dragging")
            self.assertIn("(|, origin/throttling)", self.text())
            self.cancel()

    def test_uncached_non_draggable_branches_keep_the_whole_label_selected(self) -> None:
        when(drag.gs_log_graph_branch_drag).current_state().thenReturn({})
        self.branches[0].worktree_path = "/other/worktree"
        for label in ("origin/throttling", "throttling"):
            self.start(label, activate=False)
            self.assertIsNone(self.state())
            self.assertEqual(self.view.substr(self.view.sel()[0]), label)
            self.assertEqual(self.text(), CONTENT)
        self.assertEqual(self.scheduled, [])

    def test_simple_double_click_only_selects_without_lookup_or_edits(self) -> None:
        version = self.view.change_count()
        self.start(activate=False)
        self.assertEqual(self.state()["phase"], "armed")
        self.assertEqual(self.text(), CONTENT)
        point = CONTENT.index("throttling")
        self.assertEqual(list(self.view.sel()), [sublime.Region(point, point + len("throttling"))])
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        self.assert_restored()
        for callback in self.scheduled[:]:
            callback()
        self.assertEqual(self.view.change_count(), version)
        self.assertEqual(self.menus, [])
        verify(drag.gs_log_graph_branch_drag, times=0).get_branches()

    def test_double_click_selects_branch_name_components(self) -> None:
        replace_view_content(self.view, CONTENT.replace("throttling", "feature/foo-bar"))
        self.branches[0].name = "feature/foo-bar"
        version = self.view.change_count()
        for part in ("feature", "foo", "bar"):
            self.start(part, activate=False)
            self.assertEqual(self.view.substr(self.view.sel()[0]), part)
            drag.GsLogGraphBranchDragListener().on_selection_modified(self.view)
            self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
            self.assertEqual(self.view.substr(self.view.sel()[0]), part)
            self.assertIsNone(self.state())
        self.assertEqual(self.view.change_count(), version)
        verify(drag.gs_log_graph_branch_drag, times=0).get_branches()

    def test_moving_to_another_line_activates_without_hold_timeout(self) -> None:
        self.start(activate=False)
        point = self.text().index("40e3d394")
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(self.text().index("throttling"), point))
        drag.GsLogGraphBranchDragListener().on_selection_modified(self.view)
        self.assertEqual(self.state()["phase"], "dragging")
        self.assertEqual(self.state()["target"], "40e3d394")
        self.assertIn("(throttling) First commit", self.text())
        self.cancel()
        self.assert_restored()

    def test_remote_branch_and_commit_hash_do_not_start_dragging(self) -> None:
        for text in ("origin/throttling", "40e3d394"):
            self.start(text)
            self.assertIsNone(self.state())
            self.assertEqual(self.text(), CONTENT)

    def test_remote_double_click_keeps_the_whole_label_selected(self) -> None:
        point = CONTENT.index("origin/throttling") + len("origin/th")
        version = self.view.change_count()
        self.view.run_command("gs_log_graph_branch_drag", {
            "event": {"x": point, "y": 0, "button": 1}
        })
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        self.assertEqual(self.view.substr(self.view.sel()[0]), "origin/throttling")
        self.assertIsNone(self.state())
        self.assertEqual(self.view.change_count(), version)
        self.assertEqual(self.scheduled, [])
        verify(drag.gs_log_graph_branch_drag, times=0).get_branches()

    def test_start_marks_local_branch_and_ignores_a_second_start(self) -> None:
        self.start()
        token = self.state()["token"]
        self.assertIn("(|, origin/throttling)", self.text())
        self.start("other")
        self.assertEqual(self.state()["token"], token)
        self.assertIn("(other, tag: v1)", self.text())
        self.cancel()
        self.assert_restored()

    def test_crossing_source_previews_name_and_source_release_aborts(self) -> None:
        self.start()
        self.update("40e3d394")
        self.assertIn("(throttling) First commit", self.text())
        self.assert_cursor_after("(throttling")
        self.update("718ab2b9")
        self.assertEqual(self.text(), CONTENT)
        self.assert_cursor_after("(throttling")
        self.update("40e3d394")
        self.assertIn("(|, origin/throttling)", self.text())
        self.update("718ab2b9")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        self.assert_restored()
        self.assertEqual(self.menus, [])

    def test_existing_decorations_and_menu_dismissal(self) -> None:
        self.start()
        self.update("c0d0646f")
        self.assertIn("(other, tag: v1, throttling)", self.text())
        self.assert_cursor_after("(other, tag: v1, throttling")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        labels, done, point = self.menus[-1]
        self.assertEqual(labels, ["Move 'throttling' here"])
        self.assertEqual(point, self.view.sel()[0].b)
        self.assertEqual(self.state()["phase"], "confirming")
        done(-1)
        self.assert_restored()

    def test_native_release_without_finish_opens_confirmation(self) -> None:
        callback = self.schedule_native_release()
        self.assertEqual(self.state()["phase"], "dragging")
        callback()
        self.assertEqual(self.state()["phase"], "confirming")
        self.assertEqual(len(self.menus), 1)
        callback()
        self.assertEqual(len(self.menus), 1)
        self.menus[-1][1](-1)
        self.assert_restored()

    def test_normal_finish_takes_precedence_over_native_release_callback(self) -> None:
        callback = self.schedule_native_release()
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        callback()
        self.assertEqual(self.state()["phase"], "confirming")
        self.assertEqual(len(self.menus), 1)

    def test_native_release_callback_cannot_finish_new_session(self) -> None:
        callback = self.schedule_native_release()
        self.cancel()
        self.start()
        self.update("40e3d394")
        token = self.state()["token"]
        callback()
        self.assertEqual(self.state()["token"], token)
        self.assertEqual(self.state()["phase"], "dragging")

    def test_native_release_callback_ignores_resumed_selection(self) -> None:
        callback = self.schedule_native_release()
        with drag.updating(self.view):
            cursor = self.view.sel()[0].b
            self.view.sel().clear()
            self.view.sel().add(sublime.Region(0, cursor))
        callback()
        self.assertEqual(self.state()["phase"], "dragging")

    def test_native_release_callback_ignores_closed_session(self) -> None:
        callback = self.schedule_native_release()
        self.cancel()
        callback()
        self.assert_restored()

    def test_other_worktree_branch_does_not_start_dragging(self) -> None:
        self.branches[0].worktree_path = "/other/worktree"
        self.branches[0].name = "feature/foo-bar"
        content = CONTENT.replace("throttling", "feature/foo-bar")
        replace_view_content(self.view, content)
        version = self.view.change_count()
        self.start("foo")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        self.assertEqual(self.view.substr(self.view.sel()[0]), "feature/foo-bar")
        self.assertIsNone(self.state())
        self.assertEqual(self.text(), content)
        self.assertEqual(self.view.change_count(), version)
        self.assertEqual(self.menus, [])
        self.assertEqual(self.scheduled, [])
        verify(drag.gs_log_graph_branch_drag, times=0).get_branches()

    def test_confirmation_restores_preview_before_dispatching_move(self) -> None:
        self.start()
        self.update("40e3d394")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        moves = []
        when(sublime.Window).run_command("gs_move_branch", ...).thenAnswer(
            lambda command, args: moves.append((args, self.text(), self.state()))
        )
        self.menus[-1][1](0)
        self.assert_restored()
        self.assertEqual(moves, [({
            "branch": "throttling", "target": "40e3d394", "previous_tip": "718ab2b9" + "0" * 32,
            "undo_owner": self.view.id()
        }, CONTENT, None)])
        self.menus[-1][1](0)
        self.assertEqual(len(moves), 1)

    def test_current_branch_uses_checkout_with_existing_merge_recovery(self) -> None:
        self.branches[0].active = True
        self.branches[0].worktree_path = "/repo"
        self.start()
        self.update("40e3d394")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        checkouts = []
        when(sublime.Window).run_command("gs_checkout_new_branch", ...).thenAnswer(
            lambda command, args: checkouts.append((args, self.text()))
        )
        self.menus[-1][1](0)
        self.assert_restored()
        self.assertEqual(checkouts, [({
            "branch_name": "throttling", "start_point": "40e3d394", "force": True
        }, CONTENT)])

    def test_confirmation_requires_menu_phase_and_session_token(self) -> None:
        self.start()
        token = self.state()["token"]
        self.update("40e3d394")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "confirm", "token": token})
        self.assertEqual(self.state()["phase"], "dragging")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        self.view.run_command("gs_log_graph_branch_drag", {"action": "confirm"})
        self.assertEqual(self.state()["phase"], "confirming")
        self.cancel()
        self.assert_restored()

    def test_stale_menu_and_native_tracking_callbacks_cannot_touch_new_drag(self) -> None:
        self.start()
        native_callback = self.scheduled[-1]
        self.update("40e3d394")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        old_menu = self.menus[-1][1]
        self.cancel()
        self.start("other")
        token = self.state()["token"]
        text = self.text()
        old_menu(0)
        old_menu(-1)
        native_callback()
        self.assertEqual(self.state()["token"], token)
        self.assertEqual(self.text(), text)
        self.cancel()
        self.assert_restored()

    def test_redraw_cancels_confirmation_and_preserves_new_buffer(self) -> None:
        self.start()
        self.update("40e3d394")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        callback = self.menus[-1][1]
        redrawn = CONTENT.replace("First commit", "Refreshed commit")
        replace_view_content(self.view, redrawn)
        self.assertIsNone(self.state())
        callback(0)
        self.assertEqual(self.text(), redrawn)

    def test_reload_cleanup_restores_preview_and_invalidates_menu(self) -> None:
        self.start()
        self.update("40e3d394")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        callback = self.menus[-1][1]
        drag.reset_branch_drags()
        self.assert_restored()
        callback(0)
        self.assert_restored()

    def test_release_off_commit_aborts(self) -> None:
        self.start()
        self.update("REPO")
        self.view.run_command("gs_log_graph_branch_drag", {"action": "finish"})
        self.assert_restored()
        self.assertEqual(self.menus, [])

    def test_native_tracking_is_not_started_after_cancellation(self) -> None:
        self.start()
        callback = self.scheduled[-1]
        self.cancel()
        when(self.view).run_command("drag_select", ...).thenReturn(None)
        callback()
        verify(self.view, times=0).run_command("drag_select", ...)

    def schedule_native_release(self) -> Callable[[], None]:
        self.start()
        self.update("40e3d394")
        self.scheduled.clear()
        drag.GsLogGraphBranchDragListener().on_selection_modified(self.view)
        self.assertEqual(len(self.scheduled), 1)
        return self.scheduled[0]

    def start(self, text: str = "throttling", activate: bool = True) -> None:
        point = self.text().index(text)
        if text == "throttling":
            self.assertIsNotNone(drag.branch_region_at(self.view, point), self.view.scope_name(point))
        self.view.run_command("gs_log_graph_branch_drag", {
            "event": {"x": point, "y": 0, "button": 1}
        })
        if activate and (state := self.state()) is not None and state["phase"] == "armed":
            drag.activate_held_drag(self.view, state["token"])

    def update(self, text: str) -> None:
        self.view.run_command("gs_log_graph_branch_drag", {
            "action": "update", "point": self.text().index(text)
        })

    def cancel(self) -> None:
        self.view.run_command("gs_log_graph_branch_drag", {"action": "cancel"})

    def state(self) -> dict | None:
        return self.view.settings().get(drag.DRAG_KEY)

    def text(self) -> str:
        return self.view.substr(sublime.Region(0, self.view.size()))

    def assert_restored(self) -> None:
        self.assertEqual(self.text(), CONTENT)
        self.assertIsNone(self.state())
        self.assertTrue(self.view.is_read_only())
        for key in (drag.SOURCE_KEY, drag.PREVIEW_KEY, drag.DESTINATION_KEY):
            self.assertEqual(self.view.get_regions(key), [])

    def assert_cursor_after(self, text: str) -> None:
        point = self.text().index(text) + len(text)
        self.assertEqual(list(self.view.sel()), [sublime.Region(point)])
