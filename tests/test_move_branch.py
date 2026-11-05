from unittesting import DeferrableTestCase

from GitSavvy.core.commands import branch
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.core.interfaces.log_graph_main_actions import gs_log_graph_action
from GitSavvy.core.types import FullHash
from GitSavvy.tests.mockito import mock, unstub, verify, when


OLD = FullHash("718ab2b9" + "0" * 32)


class TestMoveBranch(DeferrableTestCase):
    def setUp(self) -> None:
        self.window = mock()
        self.command = branch.gs_move_branch(self.window)
        when(self.command).resolve("refs/heads/topic").thenReturn(OLD)
        when(self.command).git(...).thenReturn("")
        when(self.window).status_message(...).thenReturn(None)
        when(branch.ref_undo).add_branch_move_undo(...).thenReturn(None)
        when(branch.interface_util).refresh_gitsavvy_interfaces(self.window).thenReturn(None)

    def tearDown(self) -> None:
        unstub()

    def test_known_tip_needs_only_the_mutation_and_records_undo(self) -> None:
        self.command.run("topic", "origin/topic", 42, previous_tip=OLD)
        verify(self.command, times=0).resolve(...)
        verify(self.command).git("branch", "--force", "--", "topic", "origin/topic")
        verify(branch.ref_undo).add_branch_move_undo(self.command, "topic", OLD, 42)
        verify(branch.interface_util).refresh_gitsavvy_interfaces(self.window)

    def test_graph_fast_forward_delegates_with_its_known_tip(self) -> None:
        when(self.window).run_command(...).thenReturn(None)
        gs_log_graph_action(self.window).move_branch("topic", "origin/topic", previous_tip=OLD)
        verify(self.window).run_command("gs_move_branch", {
            "branch": "topic", "target": "origin/topic", "previous_tip": OLD
        })

    def test_standalone_call_resolves_only_the_old_tip_for_undo(self) -> None:
        self.command.run("topic", "40e3d394", 42)
        verify(self.command).resolve("refs/heads/topic")
        verify(self.command, times=1).resolve(...)
        verify(self.command).git("branch", "--force", "--", "topic", "40e3d394")
        verify(branch.ref_undo).add_branch_move_undo(self.command, "topic", OLD, 42)

    def test_git_refusal_does_not_fall_back_to_checkout_or_reset(self) -> None:
        error = GitSavvyError("Branch is checked out", show_panel=False)
        when(self.command).git("branch", "--force", "--", "topic", "40e3d394").thenRaise(error)
        with self.assertRaises(GitSavvyError):
            self.command.run("topic", "40e3d394", 42, previous_tip=OLD)
        verify(self.command, times=1).git(...)
        verify(branch.ref_undo, times=0).add_branch_move_undo(...)
        verify(branch.interface_util, times=0).refresh_gitsavvy_interfaces(...)

    def test_invalid_target_is_left_to_git_without_recording_undo(self) -> None:
        error = GitSavvyError("Not a valid object name", show_panel=False)
        when(self.command).git("branch", "--force", "--", "topic", "missing").thenRaise(error)
        with self.assertRaises(GitSavvyError):
            self.command.run("topic", "missing", 42, previous_tip=OLD)
        verify(self.command, times=0).resolve(...)
        verify(branch.ref_undo, times=0).add_branch_move_undo(...)
