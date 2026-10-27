from __future__ import annotations

from contextlib import nullcontext
from functools import partial

from unittesting import DeferrableTestCase

from GitSavvy.common.util import actions
from GitSavvy.core import runtime
from GitSavvy.core.commands import log_graph_main_actions as graph
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.core.interfaces import branch
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


PATH = "/repo-feature"
DIRTY = f"fatal: '{PATH}' contains modified or untracked files, use --force to delete it\n"
LOCKED = "fatal: cannot remove a locked working tree"


class TestWorktreeDeletion(DeferrableTestCase):
    def setUp(self) -> None:
        self.window = mock()
        self.view = mock()
        self.settings = mock()
        when(self.view).settings().thenReturn(self.settings)
        when(self.settings).set(...).thenReturn(None)
        self.command = branch.gs_branches_delete(self.view)
        self.command.window = self.window
        self.command.interface = mock({"state": {"worktree_deletions_in_progress": set()}})
        when(self.command.interface).just_render().thenReturn(None)
        when(self.command).git(...).thenReturn("")
        when(branch.util.view).refresh_gitsavvy(self.view).thenReturn(None)
        when(branch).busy_indicator(self.view, start_after=0.5).thenReturn(nullcontext())
        when(actions.app_settings).get("prompt_before_destructive_action").thenReturn(False)
        when(runtime).run_on_new_thread(...).thenAnswer(lambda fn, *a, **kw: fn(*a, **kw))
        self.panels = []
        when(self.window).show_quick_panel(...).thenAnswer(
            lambda items, on_done, **kw: self.panels.append((items, on_done))
        )

    def tearDown(self) -> None:
        unstub()

    def test_clean_worktree_is_removed_without_a_recovery_panel(self) -> None:
        self.command.delete_worktree(PATH, False)

        verify(self.command).git(
            "worktree", "remove", None, PATH,
            throw_on_error=True, show_panel_on_error=False
        )
        self.assertEqual(self.panels, [])
        self._assert_refreshed()

    @p.expand([
        (False, "", "Abort, worktree is locked."),
        (False, ", lock reason: build in progress", "Abort, worktree is locked (build in progress)."),
        (True, ", lock reason: build in progress", "Abort, worktree is locked (build in progress).")
    ])
    def test_locked_worktree_offers_unlock_and_preserves_force(
        self, force: bool, suffix: str, message: str
    ) -> None:
        self._fail_removal(LOCKED + suffix + "\nuse 'remove -f -f' to override or unlock first\n", force)

        self.command.delete_worktree(PATH, force)

        self.assertEqual(self.panels[0][0], [message, "Unlock and delete the worktree"])
        verify(self.command, times=0).git("worktree", "unlock", PATH, ...)
        self._assert_refreshed()
        when(self.command).git("worktree", "remove", "-f" if force else None, PATH, ...).thenReturn("")
        self.panels[0][1](1)

        verify(self.command).git("worktree", "unlock", PATH, ...)
        verify(self.command, times=0).git("worktree", "remove", None if force else "-f", PATH, ...)

    def test_dirty_worktree_offers_open_or_force_delete(self) -> None:
        self._fail_removal(DIRTY)
        when(self.command).open_worktree_in_new_window(PATH).thenReturn(None)

        self.command.delete_worktree(PATH, False)

        self.assertEqual(self.panels[0][0], [
            "Abort, worktree contains modified or untracked files.",
            "Open worktree in a new window",
            "Delete anyway"
        ])
        self._assert_refreshed()
        self.panels[0][1](1)
        verify(self.command).open_worktree_in_new_window(PATH)
        verify(self.command, times=0).git("worktree", "remove", "-f", PATH, ...)

        self.panels[0][1](2)

        verify(self.command).git("worktree", "remove", "-f", PATH, ...)

    @p.expand([(0,), (-1,)])
    def test_abort_or_cancel_does_not_delete_or_unlock(self, index: int) -> None:
        self._fail_removal(LOCKED + "\n")
        self.command.delete_worktree(PATH, False)

        self.panels[0][1](index)

        verify(self.command, times=1).git("worktree", "remove", None, PATH, ...)
        verify(self.command, times=0).git("worktree", "unlock", PATH, ...)

    def test_unlocking_a_dirty_worktree_still_requires_force_confirmation(self) -> None:
        self._fail_removal(LOCKED + ", lock reason: build in progress\n")
        self.command.delete_worktree(PATH, False)
        self._fail_removal(DIRTY)

        self.panels[0][1](1)

        self.assertEqual(len(self.panels), 2)
        self.assertEqual(self.panels[1][0][-1], "Delete anyway")
        verify(self.command, times=0).git("worktree", "remove", "-f", PATH, ...)
        self._assert_refreshed()

    def test_unrecognized_errors_are_shown_and_raised(self) -> None:
        error = self._fail_removal("fatal: permission denied\n")
        when(error).show_error_panel().thenReturn(None)

        with self.assertRaises(GitSavvyError):
            self.command.delete_worktree(PATH, False)

        verify(error).show_error_panel()
        self.assertEqual(self.panels, [])
        self._assert_refreshed()

    def test_failed_unlock_does_not_retry_removal(self) -> None:
        self._fail_removal(LOCKED + "\n")
        self.command.delete_worktree(PATH, False)
        error = GitSavvyError("unlock failed", show_panel=False)
        when(self.command).git("worktree", "unlock", PATH, ...).thenRaise(error)
        when(error).show_error_panel().thenReturn(None)

        with self.assertRaises(GitSavvyError):
            self.panels[0][1](1)

        verify(error).show_error_panel()
        verify(self.command, times=1).git("worktree", "remove", None, PATH, ...)
        self._assert_refreshed()

    def _fail_removal(self, stderr: str, force: bool = False) -> GitSavvyError:
        error = GitSavvyError("removal failed", stderr=stderr, show_panel=False)
        when(self.command).git(
            "worktree", "remove", "-f" if force else None, PATH, ...
        ).thenRaise(error)
        return error

    def _assert_refreshed(self) -> None:
        self.assertEqual(self.command.interface.state["worktree_deletions_in_progress"], set())
        verify(branch.util.view, atleast=1).refresh_gitsavvy(self.view)
        verify(self.settings, atleast=1).set("git_savvy.update_view_in_a_blocking_manner", True)


class TestGraphWorktreeDeletion(TestWorktreeDeletion):
    command_class = graph.gs_log_graph_action

    def setUp(self) -> None:
        super().setUp()
        self.command = self.command_class(self.window)
        self.command.delete_worktree = partial(graph.delete_worktree_from_graph, self.command)
        when(self.command).git(...).thenReturn("")
        when(graph.util.view).refresh_gitsavvy_interfaces(
            self.window, refresh_sidebar=True
        ).thenReturn(None)

    def _assert_refreshed(self) -> None:
        verify(graph.util.view, atleast=1).refresh_gitsavvy_interfaces(
            self.window, refresh_sidebar=True
        )


class TestGraphDecorationWorktreeDeletion(TestGraphWorktreeDeletion):
    command_class = graph.gs_log_graph_delete_decoration
