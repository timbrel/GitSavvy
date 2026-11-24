from unittesting import DeferrableTestCase

from GitSavvy.core.interfaces import log_graph_rebase_actions as actions
from GitSavvy.tests.mockito import mock, unstub, verify, when


class TestRebaseRefPrompt(DeferrableTestCase):
    def tearDown(self) -> None:
        unstub()

    def test_accept_suggested_branch_for_extract(self) -> None:
        self.check_prompt("topic", "", False, False)

    def test_accept_suggested_branch_for_copy(self) -> None:
        self.check_prompt("topic", "", True, False)

    def test_retry_text_does_not_become_suggested_branch(self) -> None:
        self.check_prompt("other", "other", False, True)

    def test_copy_accepts_existing_local_counterpart(self) -> None:
        self.check_prompt("topic", "", True, False, backup=True)

    def test_active_branch_still_warns(self) -> None:
        self.check_prompt("topic", "", False, True, active=True)

    def check_prompt(
        self, entered: str, initial_text: str, copy: bool, warns: bool,
        backup: bool = False, active: bool = False
    ) -> None:
        view = mock()
        window = mock()
        command = mock({"view": view, "window": window})
        branches = [mock({"name": "topic", "active": active}),
                    mock({"name": "other", "active": False})]
        if backup:
            branches.append(mock({"name": "topic--local", "active": False}))
        when(command).get_local_branches().thenReturn(branches)
        when(actions).suggested_ref_branch(view, ...).thenReturn("topic")
        when(actions).show_single_line_input_panel(...).thenAnswer(
            lambda caption, text, on_done: on_done(entered)
        )
        when(actions).show_actions_panel(...).thenReturn(None)
        results = []

        actions.ask_for_ref(command, {"copy": copy}, results.append, initial_text)

        self.assertEqual([] if warns else ["refs/heads/" + entered], results)
        verify(actions, times=1 if warns else 0).show_actions_panel(window, ...)
