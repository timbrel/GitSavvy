from __future__ import annotations

from unittesting import DeferrableTestCase

from GitSavvy.core.commands import checkout
from GitSavvy.core.commands.checkout import CheckoutBranchInputHandler, gs_checkout_branch
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.core.git_mixins.branches import Branch
from GitSavvy.core.types import FullHash
from GitSavvy.tests.mockito import mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


class TestCheckoutBranch(DeferrableTestCase):
    def setUp(self) -> None:
        self.window = mock()
        self.command = gs_checkout_branch(self.window)
        when(self.command).get_repo_path().thenReturn("/repo")
        when(self.command).get_remotes().thenReturn({"origin": "url", "fork": "url"})
        when(self.command).current_state().thenReturn({"last_branches": [None, "main"]})
        when(self.command).git_throwing_silently(...).thenReturn("")
        when(self.window).status_message(...).thenReturn(None)
        when(checkout.util.view).refresh_gitsavvy_interfaces(
            self.window, refresh_sidebar=True
        ).thenReturn(None)

    def tearDown(self) -> None:
        unstub()

    def test_lists_locals_then_missing_remote_branches_in_remote_order(self) -> None:
        handler = self._handler([
            branch("busy", worktree_path="/repo-busy"),
            branch("local"),
            branch("main", active=True, worktree_path="/repo/"),
            branch("local", remote="fork"),
            branch("topic", remote="fork"),
            branch("main", remote="origin"),
            branch("new", remote="origin"),
            branch("topic", remote="origin")
        ])

        items, index = handler.list_items()

        self.assertEqual([item.text for item in items], [
            "busy", "local", "main", "origin/new", "origin/topic", "fork/topic"
        ])
        self.assertEqual([item.annotation for item in items], [
            "worktree", "", "", "remote", "remote", "remote"
        ])
        self.assertEqual(index, 0)
        self.assertEqual(handler.name(), "branch")

    def test_preselects_the_previous_local_branch(self) -> None:
        when(self.command).current_state().thenReturn({"last_branches": ["previous", "main"]})
        handler = self._handler([branch("main"), branch("previous")])

        items, index = handler.list_items()

        self.assertEqual(items[index].text, "previous")

    @p.expand([("local", None), ("remote", "origin")])
    def test_single_group_has_one_item(self, label: str, remote: str | None) -> None:
        handler = self._handler([branch("feature", remote=remote)])
        items, _ = handler.list_items()
        self.assertEqual(len(items), 1)

    def test_empty_list_has_a_non_actionable_message(self) -> None:
        handler = self._handler([])
        items, _ = handler.list_items()
        self.assertEqual(items[0].text, "There are no branches available.")
        self.assertFalse(handler.validate(items[0].value))
        self.assertFalse(handler.validate("not-a-branch"))

    def test_local_and_remote_refs_with_the_same_display_name_are_distinct(self) -> None:
        handler = self._handler([
            branch("origin/topic"), branch("topic", remote="origin")
        ])
        items, _ = handler.list_items()
        self.assertNotEqual(items[0].value, items[1].value)
        self.assertEqual(len(handler.branches), 2)

    def test_enter_checks_out_a_local_branch_without_detaching(self) -> None:
        self._select(branch("feature"))

        verify(self.command).git_throwing_silently("checkout", None, "feature")
        verify(self.window).status_message("Checked out `feature`.")
        verify(checkout.util.view).refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def test_enter_creates_a_local_branch_tracking_the_selected_remote(self) -> None:
        self._select(branch("feature/nested", remote="origin"))

        verify(self.command).git_throwing_silently(
            "checkout", None, "-b", "feature/nested", "--track",
            "refs/remotes/origin/feature/nested"
        )
        verify(self.window).status_message(
            "Checked out `origin/feature/nested` as local branch `feature/nested`."
        )

    def test_enter_opens_a_branch_in_another_worktree(self) -> None:
        when(self.command).open_worktree_in_new_window("/repo-busy").thenReturn(None)

        self._select(branch("busy", worktree_path="/repo-busy"))

        verify(self.command).open_worktree_in_new_window("/repo-busy")
        verify(self.command, times=0).git_throwing_silently(...)

    def test_enter_does_not_open_the_current_worktree(self) -> None:
        self._select(branch("main", active=True, worktree_path="/repo/"))

        verify(self.command).git_throwing_silently("checkout", None, "main")

    @p.expand([
        ("local", None, None, "refs/heads/feature"),
        ("remote", "origin", None, "refs/remotes/origin/feature"),
        ("worktree", None, "/repo-feature", "refs/heads/feature")
    ])
    def test_ctrl_enter_checks_out_detached(
        self, label: str, remote: str | None, worktree_path: str | None, ref: str
    ) -> None:
        self._select(branch("feature", remote=remote, worktree_path=worktree_path), ctrl=True)

        verify(self.command).git_throwing_silently("checkout", None, "--detach", ref)

    @p.expand([
        (None, None, "to checkout"),
        ("origin", None, "to checkout as `feature`"),
        (None, "/repo-feature", "to open in a new window")
    ])
    def test_preview_explains_enter_and_ctrl_enter(
        self, remote: str | None, worktree_path: str | None, expected: str
    ) -> None:
        handler = self._handler([branch("feature", remote=remote, worktree_path=worktree_path)])
        items, _ = handler.list_items()

        self.assertEqual(
            handler.preview(items[0].value), f"[enter] {expected}, [ctrl+enter] to checkout detached"
        )
        self.assertEqual(handler.preview(None), "Select a branch.")

    def test_cancel_does_not_affect_a_subsequent_direct_checkout(self) -> None:
        handler = self._handler([branch("feature", remote="origin")])
        handler.confirm("refs/remotes/origin/feature", {"modifier_keys": {"ctrl": True}})
        handler.cancel()

        self.command.run("FETCH_HEAD")

        verify(self.command).git_throwing_silently("checkout", None, "FETCH_HEAD")

    def test_modifier_state_is_consumed_after_checkout(self) -> None:
        self._select(branch("feature"), ctrl=True)

        self.command.run("main")

        verify(self.command).git_throwing_silently("checkout", None, "main")

    def test_invoking_without_arguments_opens_the_input_palette(self) -> None:
        when(self.window).run_command("show_overlay", {
            "overlay": "command_palette", "command": "gs_checkout_branch"
        }).thenReturn(None)

        self.command.run()

        verify(self.window).run_command("show_overlay", {
            "overlay": "command_palette", "command": "gs_checkout_branch"
        })
        self.assertIsNone(self.command.input({"branch": "main"}))

    @p.expand([
        ("local", "feature", False, None),
        ("detached", "refs/heads/feature", True, None),
        ("tracking", "refs/remotes/origin/feature", False, "feature")
    ])
    def test_dirty_worktree_retry_preserves_the_checkout_mode(
        self, label: str, ref: str, detach: bool, local_name: str | None
    ) -> None:
        checkout_args = (
            ("--detach", ref) if detach else
            ("-b", local_name, "--track", ref) if local_name else
            (ref,)
        )
        error = GitSavvyError("dirty", stderr=checkout.DIRTY_WORKTREE_MESSAGE, show_panel=False)
        when(self.command).git_throwing_silently("checkout", None, *checkout_args).thenRaise(error)
        panels = []
        when(checkout).show_actions_panel(self.window, ...).thenAnswer(
            lambda window, actions: panels.append(actions)
        )

        self.command.on_branch_selection(ref, detach=detach, local_name=local_name)
        panels[0][1][1]()

        verify(self.command).git_throwing_silently("checkout", "--merge", *checkout_args)

    def _handler(self, branches: list[Branch]) -> CheckoutBranchInputHandler:
        when(self.command).get_branches().thenReturn(branches)
        handler = self.command.input({})
        assert handler is not None
        return handler

    def _select(self, selected: Branch, *, ctrl: bool = False) -> None:
        handler = self._handler([selected])
        items, _ = handler.list_items()
        value = items[0].value
        self.assertTrue(handler.want_event())
        self.assertTrue(handler.validate(value))
        handler.confirm(value, {"modifier_keys": {"ctrl": ctrl}})
        self.command.run(value)


def branch(
    name: str, *, remote: str | None = None,
    active: bool = False, worktree_path: str | None = None
) -> Branch:
    return Branch(
        name=name,
        remote=remote,
        canonical_name=f"{remote}/{name}" if remote else name,
        commit_hash=FullHash("a" * 40),
        commit_msg="",
        active=active,
        is_remote=remote is not None,
        committerdate=0,
        human_committerdate="now",
        relative_committerdate="now",
        upstream=None,
        distance_to_head=None,
        worktree_path=worktree_path
    )
