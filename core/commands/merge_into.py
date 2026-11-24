from __future__ import annotations

from functools import partial

import sublime
import sublime_plugin

from ..base_commands import Args, GsWindowCommand, Kont
from ..exceptions import GitSavvyError
from ..interfaces.util import refresh_gitsavvy_interfaces
from ..runtime import on_worker
from ..ui__quick_panel import noop, show_actions_panel
from ..ui_mixins.quick_panel import show_branch_panel

from typing import Callable


__all__ = (
    "gs_merge_current_branch_into",
    "GsWindowCleanupHook"
)


BranchName = str


def use_current_branch(cmd: GsWindowCommand, args: Args, done: Kont) -> None:
    current_branch = cmd.get_current_branch()
    if current_branch:
        done(current_branch.name)
    else:
        cmd.window.status_message("Not available when detached")


def ask_for_other_local_branch(cmd: GsWindowCommand, args: Args, done: Kont) -> None:
    show_branch_panel(done, local_branches_only=True, ignore_current_branch=True)


class gs_merge_current_branch_into(GsWindowCommand):
    defaults = {
        "source": use_current_branch,
        "target": ask_for_other_local_branch
    }

    @on_worker
    def run(self, source: BranchName, target: BranchName) -> None:
        repo_path = self.repo_path

        @on_worker
        def merge(destination_path: str, source_branch: str, temporary: bool = False) -> None:
            try:
                self.git_throwing_silently(
                    "merge", "--no-squash", "--commit", "--no-autostash",
                    f"refs/heads/{source_branch}",
                    working_dir=destination_path
                )
            except GitSavvyError as err:
                if "Automatic merge failed; fix conflicts and then commit the result." in err.stdout:
                    show_actions_panel(self.window, [
                        (
                            "Abort, merge had edit conflicts",
                            partial(remove_worktree, destination_path, force=True)
                            if temporary else
                            partial(abort_merge, destination_path)
                        ),
                        (
                            "Open staging area in a new window",
                            partial(
                                open_worktree_window, destination_path,
                                temporary=temporary, cleanup_hook=temporary
                            )
                        )
                    ], on_cancel=partial(leave_for_later, destination_path))

                else:
                    def pre_check_failure(message):
                        show_actions_panel(self.window, [
                            noop(message),
                            (
                                "Open it in a new window",
                                partial(open_worktree_window, destination_path, temporary=temporary)
                            )
                        ])

                    if (
                        "would be overwritten by merge" in err.stderr
                        or "would be overwritten by checkout" in err.stderr
                        or "not uptodate. Cannot merge." in err.stderr  # codespell:ignore uptodate
                    ):
                        pre_check_failure(f"Abort, {target} has local changes")

                    elif (
                        "Merging is not possible because you have unmerged files." in err.stderr
                        or "You have not concluded your merge (MERGE_HEAD exists)." in err.stderr
                    ):
                        pre_check_failure(f"Abort, {target} is already in a merge")

                    elif (
                        "You have not concluded your cherry-pick (CHERRY_PICK_HEAD exists)." in err.stderr
                    ):
                        pre_check_failure(f"Abort, {target} is in a cherry-pick")

                    else:
                        err.show_error_panel()
                        if temporary:
                            leave_for_later(destination_path)
            else:
                if temporary:
                    remove_worktree(destination_path, force=True)
                self.window.status_message(f"Merged {source} into {target}")

            finally:
                refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

        @on_worker
        def abort_merge(destination_path):
            self.git("merge", "--abort", working_dir=destination_path)
            refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

        @on_worker
        def remove_worktree(destination_path, *, force):
            self.remove_worktree(destination_path, force=force)
            refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

        def leave_for_later(destination_path):
            sublime.status_message(f"Merge worktree kept at {destination_path}.")

        def open_worktree_window(path: str, *, temporary: bool = False, cleanup_hook: bool = False):
            def opened(window: sublime.Window) -> None:
                window.run_command("gs_show_status")
                if cleanup_hook:
                    register_cleanup(window, partial(cleanup, path))

            self.open_worktree_in_new_window(
                path, then=opened, copy_project_only_if_ignored=temporary
            )

        def cleanup(destination_path: str) -> None:
            if self.git(
                "rev-parse", "--verify", "--quiet", "MERGE_HEAD",
                working_dir=destination_path,
                throw_on_error=False,
                show_panel_on_error=False
            ):
                return

            self.git(
                "worktree", "remove", destination_path,
                working_dir=repo_path,
                throw_on_error=False,
                show_panel_on_error=False
            )

        try:
            worktree = next(wt for wt in self.get_worktrees() if wt.branch_name == target)
        except StopIteration:
            path = self.create_new_worktree(target, copy_project=False)
            merge(path, source, temporary=True)
        else:
            path = worktree.path
            show_actions_panel(self.window, [
                noop(f"Abort, {target} is checked out elsewhere"),
                (
                    "Continue in its worktree",
                    partial(merge, path, source)
                )
            ])


registered_windows: dict[sublime.Window, Callable[[], None]] = {}


def register_cleanup(window: sublime.Window, callback: Callable[[], None]) -> None:
    registered_windows[window] = callback


class GsWindowCleanupHook(sublime_plugin.EventListener):
    def on_pre_close_window(self, window) -> None:
        callback = registered_windows.pop(window, None)
        if callback:
            sublime.set_timeout_async(callback, 1000)
