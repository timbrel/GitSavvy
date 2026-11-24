from __future__ import annotations

import inspect

import sublime

from unittesting import DeferrableTestCase

from GitSavvy.core import runtime
from GitSavvy.core.base_commands import make_on_done_fn
from GitSavvy.core.commands import merge_into
from GitSavvy.core.exceptions import GitSavvyError
from GitSavvy.tests.mockito import ANY, mock, unstub, verify, when
from GitSavvy.tests.parameterized import parameterized as p


SOURCE = "feature"
TARGET = "main"
PATH = "/repo-main"
REPO = "/repo"
MERGE_ARGS = ("merge", "--no-squash", "--commit", "--no-autostash", "refs/heads/feature")
CONFLICT = "Automatic merge failed; fix conflicts and then commit the result."


class TestMergeInto(DeferrableTestCase):
    def setUp(self) -> None:
        self.window = mock()
        self.command = merge_into.gs_merge_current_branch_into(self.window)
        self.panels = []
        self.worker_tasks = []
        when(runtime).enqueue_on_worker(...).thenAnswer(
            lambda fn, *args, **kwargs: self.worker_tasks.append(lambda: fn(*args, **kwargs))
        )
        when(self.command).get_repo_path().thenReturn(REPO)
        self.cleanups = dict(merge_into.registered_windows)
        merge_into.registered_windows.clear()
        when(merge_into).show_actions_panel(...).thenAnswer(self.capture_panel)
        when(merge_into).refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True).thenReturn(None)
        when(self.window).status_message(...).thenReturn(None)
        when(sublime).status_message(...).thenReturn(None)
        when(self.command).get_worktrees().thenReturn([])
        when(self.command).create_new_worktree(TARGET, copy_project=False).thenReturn(PATH)
        when(self.command).git_throwing_silently(*MERGE_ARGS, working_dir=PATH).thenReturn("")
        when(self.command).remove_worktree(PATH, force=True).thenReturn(None)
        when(self.command).git("merge", "--abort", working_dir=PATH).thenReturn("")

    def tearDown(self) -> None:
        merge_into.registered_windows.clear()
        merge_into.registered_windows.update(self.cleanups)
        unstub()

    def test_temporary_worktree_is_removed_after_success(self) -> None:
        self.run_merge()

        verify(self.command).get_worktrees()
        verify(self.command).create_new_worktree(TARGET, copy_project=False)
        verify(self.command).git_throwing_silently(*MERGE_ARGS, working_dir=PATH)
        verify(self.command).remove_worktree(PATH, force=True)
        verify(self.window).status_message("Merged feature into main")
        verify(merge_into, times=2).refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
        self.assertEqual([], self.panels)

    def test_existing_worktree_requires_confirmation(self) -> None:
        self.use_existing_worktree()
        self.run_merge()

        self.assertEqual(
            ["Abort, main is checked out elsewhere", "Continue in its worktree"],
            list(self.panels[-1][0])
        )
        self.choose("Abort, main is checked out elsewhere")
        verify(self.command, times=0).git_throwing_silently(...)
        verify(self.command, times=0).create_new_worktree(...)
        verify(self.command, times=0).remove_worktree(...)

    def test_existing_worktree_is_kept_after_success(self) -> None:
        self.use_existing_worktree()
        self.run_merge()
        self.choose("Continue in its worktree")

        verify(self.command).git_throwing_silently(*MERGE_ARGS, working_dir=PATH)
        verify(self.command, times=0).create_new_worktree(...)
        verify(self.command, times=0).remove_worktree(...)
        verify(self.window).status_message("Merged feature into main")

    def test_abort_conflict_removes_only_temporary_worktree(self) -> None:
        self.fail_merge(stdout=CONFLICT)
        self.run_merge()
        verify(self.command, times=0).remove_worktree(...)

        self.choose("Abort, merge had edit conflicts")

        verify(self.command).remove_worktree(PATH, force=True)
        verify(self.command, times=0).git(...)
        verify(self.window, times=0).status_message(...)

    def test_abort_conflict_in_existing_worktree_uses_merge_abort(self) -> None:
        self.use_existing_worktree()
        self.fail_merge(stdout=CONFLICT)
        self.run_merge()
        self.choose("Continue in its worktree")
        self.choose("Abort, merge had edit conflicts")

        verify(self.command).git("merge", "--abort", working_dir=PATH)
        verify(self.command, times=0).remove_worktree(...)

    def test_dismissing_conflict_keeps_worktree_and_reports_path(self) -> None:
        self.fail_merge(stdout=CONFLICT)
        self.run_merge()
        self.panels[-1][1]()

        verify(self.command, times=0).remove_worktree(...)
        verify(sublime).status_message(f"Merge worktree kept at {PATH}.")
        verify(merge_into).refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

    def test_opening_temporary_conflict_shows_status_and_registers_cleanup(self) -> None:
        opened = self.stub_open_window(only_if_ignored=True)
        self.fail_merge(stdout=CONFLICT)
        self.run_merge()
        self.choose("Open staging area in a new window")

        verify(opened).run_command("gs_show_status")
        self.assertEqual({opened}, set(merge_into.registered_windows))
        self.assertTrue(callable(merge_into.registered_windows[opened]))
        verify(self.command, times=0).remove_worktree(...)

    def test_opening_existing_conflict_does_not_register_cleanup(self) -> None:
        # Review regression: a user-owned worktree must never be auto-removed.
        self.use_existing_worktree()
        self.stub_open_window()
        self.fail_merge(stdout=CONFLICT)
        self.run_merge()
        self.choose("Continue in its worktree")
        self.choose("Open staging area in a new window")

        self.assertEqual({}, merge_into.registered_windows)

    @p.expand([
        ("would be overwritten by merge", "has local changes"),
        ("would be overwritten by checkout", "has local changes"),
        ("not uptodate. Cannot merge.", "has local changes"),  # codespell:ignore uptodate
        ("Merging is not possible because you have unmerged files.", "is already in a merge"),
        ("You have not concluded your merge (MERGE_HEAD exists).", "is already in a merge"),
        ("You have not concluded your cherry-pick (CHERRY_PICK_HEAD exists).", "is in a cherry-pick")
    ])
    def test_precondition_failure_offers_open_without_cleanup(self, stderr: str, reason: str) -> None:
        self.use_existing_worktree()
        opened = self.stub_open_window()
        error = self.fail_merge(stderr=stderr)
        self.run_merge()
        self.choose("Continue in its worktree")

        self.assertEqual([f"Abort, main {reason}", "Open it in a new window"], list(self.panels[-1][0]))
        self.choose("Open it in a new window")

        verify(opened).run_command("gs_show_status")
        verify(error, times=0).show_error_panel()
        verify(self.command, times=0).remove_worktree(...)
        self.assertEqual({}, merge_into.registered_windows)

    def test_opening_temporary_precondition_failure_only_copies_ignored_project(self) -> None:
        opened = self.stub_open_window(only_if_ignored=True)
        self.fail_merge(stderr="would be overwritten by merge")
        self.run_merge()
        self.choose("Open it in a new window")

        verify(opened).run_command("gs_show_status")
        self.assertEqual({}, merge_into.registered_windows)

    def test_unknown_failure_shows_error_and_keeps_temporary_worktree(self) -> None:
        error = self.fail_merge(stderr="fatal: refusing to merge unrelated histories")
        self.run_merge()

        verify(error).show_error_panel()
        verify(self.command, times=0).remove_worktree(...)
        verify(sublime).status_message(f"Merge worktree kept at {PATH}.")
        verify(merge_into).refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
        self.assertEqual([], self.panels)

    def test_unknown_failure_in_existing_worktree_shows_error(self) -> None:
        self.use_existing_worktree()
        error = self.fail_merge(stderr="fatal: refusing to merge unrelated histories")
        self.run_merge()
        self.choose("Continue in its worktree")

        verify(error).show_error_panel()
        verify(self.command, times=0).remove_worktree(...)
        verify(sublime, times=0).status_message(...)

    def test_detached_head_stops_source_provider(self) -> None:
        when(self.command).get_current_branch().thenReturn(None)
        values = []

        merge_into.use_current_branch(self.command, {}, lambda *a, **kw: values.append((a, kw)))

        self.assertEqual([], values)
        verify(self.window).status_message("Not available when detached")

    def test_default_source_produces_arguments_accepted_by_run(self) -> None:
        when(self.command).get_current_branch().thenReturn(mock({"name": SOURCE, "commit_hash": "abc123"}))
        args = {"target": TARGET}
        done = make_on_done_fn(lambda: None, args, "source")

        merge_into.use_current_branch(self.command, args, done)

        self.assertEqual(SOURCE, args["source"])
        inspect.signature(self.command.run).bind(**args)

    def test_target_provider_only_offers_other_local_branches(self) -> None:
        done = lambda value: None
        when(merge_into).show_branch_panel(...).thenReturn(None)

        merge_into.ask_for_other_local_branch(self.command, {"source": SOURCE}, done)

        verify(merge_into).show_branch_panel(done, local_branches_only=True, ignore_current_branch=True)

    def test_cleanup_uses_supported_window_close_event(self) -> None:
        # Review regression: Sublime dispatches on_pre_close_window, not on_window_close.
        self.assertTrue(callable(getattr(merge_into.GsWindowCleanupHook(), "on_pre_close_window", None)))

    def test_cleanup_preserves_an_unfinished_merge_even_with_a_clean_index(self) -> None:
        opened = self.stub_open_window(only_if_ignored=True)
        self.fail_merge(stdout=CONFLICT)
        self.run_merge()
        self.choose("Open staging area in a new window")
        when(self.command).git(
            "rev-parse", "--verify", "--quiet", "MERGE_HEAD", working_dir=PATH,
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("abc123\n")
        when(self.command).git(
            "worktree", "remove", PATH, working_dir=REPO,
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("")

        merge_into.registered_windows[opened]()

        verify(self.command).git(
            "rev-parse", "--verify", "--quiet", "MERGE_HEAD", working_dir=PATH,
            throw_on_error=False, show_panel_on_error=False
        )
        verify(self.command, times=0).git(
            "worktree", "remove", PATH, working_dir=REPO,
            throw_on_error=False, show_panel_on_error=False
        )

    @p.expand([("changed",), ("closed",)])
    def test_cleanup_keeps_original_repository(self, window_state: str) -> None:
        opened = self.stub_open_window(only_if_ignored=True)
        self.fail_merge(stdout=CONFLICT)
        self.run_merge()
        # Change context before opening the resolution window, not just before closing it.
        if window_state == "changed":
            when(self.command).get_repo_path().thenReturn("/other-repo")
        else:
            when(self.command).get_repo_path().thenRaise(AssertionError("Original window closed"))
        self.choose("Open staging area in a new window")
        scheduled = []
        when(sublime).set_timeout_async(...).thenAnswer(lambda fn, delay: scheduled.append((fn, delay)))
        when(self.command).git(
            "rev-parse", "--verify", "--quiet", "MERGE_HEAD", working_dir=PATH,
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("")
        when(self.command).git(
            "worktree", "remove", PATH, working_dir=REPO,
            throw_on_error=False, show_panel_on_error=False
        ).thenReturn("")

        listener = merge_into.GsWindowCleanupHook()
        listener.on_pre_close_window(opened)
        listener.on_pre_close_window(opened)

        self.assertEqual({}, merge_into.registered_windows)
        self.assertEqual(1, len(scheduled))
        cleanup, delay = scheduled[0]
        self.assertEqual(1000, delay)
        cleanup()
        verify(self.command).git(
            "worktree", "remove", PATH, working_dir=REPO,
            throw_on_error=False, show_panel_on_error=False
        )
        verify(self.command).get_repo_path()

    def test_cleanup_listener_is_exported(self) -> None:
        self.assertIn("GsWindowCleanupHook", merge_into.__all__)

    def run_merge(self) -> None:
        merge_into.gs_merge_current_branch_into.run.__wrapped__(self.command, SOURCE, TARGET)
        self.drain_worker()

    def drain_worker(self) -> None:
        while self.worker_tasks:
            self.worker_tasks.pop(0)()

    def use_existing_worktree(self) -> None:
        when(self.command).get_worktrees().thenReturn([mock({"branch_name": TARGET, "path": PATH})])

    def fail_merge(self, *, stdout: str = "", stderr: str = "") -> GitSavvyError:
        error = GitSavvyError("", stdout=stdout, stderr=stderr, show_panel=False)
        when(error).show_error_panel().thenReturn(None)
        when(self.command).git_throwing_silently(*MERGE_ARGS, working_dir=PATH).thenRaise(error)
        return error

    def capture_panel(self, window, actions, *, on_cancel=lambda: None) -> None:
        self.assertIs(window, self.window)
        self.panels.append((dict(actions), on_cancel))

    def choose(self, label: str) -> None:
        self.panels[-1][0][label]()
        self.drain_worker()

    def stub_open_window(self, *, only_if_ignored: bool = False):
        opened = mock()
        when(opened).run_command("gs_show_status").thenReturn(None)
        when(self.command).open_worktree_in_new_window(
            PATH, then=ANY, copy_project_only_if_ignored=only_if_ignored
        ).thenAnswer(lambda path, *, then, copy_project_only_if_ignored: then(opened))
        return opened
