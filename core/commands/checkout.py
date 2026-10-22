from __future__ import annotations
from functools import partial
import os
from typing import Any

import sublime
from sublime_plugin import ListInputHandler, WindowCommand

from . import intra_line_colorizer
from .branch import ask_for_name
from .log import LogMixin
from ..git_command import GitCommand, GitSavvyError
from ..git_mixins.branches import Branch
from ..utils import uprint
from ..view import replace_view_content
from ...common import util
from GitSavvy.core.base_commands import ask_for_branch, GsWindowCommand
from ..ui__quick_panel import noop, show_actions_panel

__all__ = (
    "gs_checkout_branch",
    "gs_checkout_new_branch",
    "gs_checkout_remote_branch",
    "gs_checkout_current_file_at_commit",
    "gs_show_file_diff",
)


DIRTY_WORKTREE_MESSAGE = "Please commit your changes or stash them before you switch branches"
BRANCH_ALREADY_EXISTS_MESSAGE = "a branch named '{0}' already exists"
RECREATE_BRANCH_UNDO_MESSAGE = """\
GitSavvy: Re-created branch '{0}', in case you want to undo, run:
  $ git checkout -B {0} {1}
"""


class gs_checkout_branch(WindowCommand, GitCommand):
    """Check out a local branch, track a remote branch, or open its worktree."""

    _input_handler: CheckoutBranchInputHandler | None = None

    def run(self, branch: str | None = None, detach: bool = False) -> None:
        if branch is None:
            self.window.run_command("show_overlay", {
                "overlay": "command_palette",
                "command": "gs_checkout_branch"
            })
            return

        handler, self._input_handler = self._input_handler, None
        if handler:
            detach = detach or handler.detach
            selected = handler.branches.get(branch)
            if selected:
                if not detach and (path := other_worktree(selected, self.repo_path)):
                    self.open_worktree_in_new_window(path)
                    return
                if not detach and selected.is_remote:
                    self.on_branch_selection(branch, local_name=selected.name)
                    return
                if not detach:
                    branch = selected.name

        self.on_branch_selection(branch, detach=detach)

    def input(self, args: dict[str, Any]) -> CheckoutBranchInputHandler | None:
        if "branch" not in args:
            self._input_handler = CheckoutBranchInputHandler(self)
            return self._input_handler
        return None

    def on_branch_selection(
        self, branch: str, merge: bool = False, *,
        detach: bool = False, local_name: str | None = None
    ) -> None:
        checkout_args = (
            ("--detach", branch) if detach else
            ("-b", local_name, "--track", branch) if local_name else
            (branch,)
        )
        try:
            self.git_throwing_silently(
                "checkout",
                "--merge" if merge else None,
                *checkout_args
            )
        except GitSavvyError as e:
            if DIRTY_WORKTREE_MESSAGE in e.stderr and not merge:
                show_actions_panel(self.window, [
                    noop("Abort, local changes would be overwritten by checkout."),
                    (
                        "Try a 'checkout --merge'.",
                        partial(
                            self.on_branch_selection, branch, merge=True,
                            detach=detach, local_name=local_name
                        )
                    )
                ])
                return
            else:
                e.show_error_panel()
                raise

        display_name = (
            branch.split("/", 2)[2]
            if branch.startswith(("refs/heads/", "refs/remotes/")) else branch
        )
        message = (
            f"Checked out `{display_name}` as local branch `{local_name}`." if local_name else
            f"Checked out `{display_name}` (detached)." if detach else
            f"Checked out `{display_name}`."
        )
        self.window.status_message(message)
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_checkout_new_branch(GsWindowCommand):
    """
    Prompt the user for a new branch name, create it, and check it out.
    """
    defaults = {
        "branch_name": ask_for_name(
            initial_text=lambda start_point: start_point or "",
        ),
    }

    def run(self, branch_name, start_point=None, force=False, merge=False):
        # type: (str, str, bool, bool) -> None
        try:
            self.git_throwing_silently(
                "checkout",
                "-B" if force else "-b",
                branch_name,
                start_point,
                "--merge" if merge else None,
            )
        except GitSavvyError as e:
            if force and DIRTY_WORKTREE_MESSAGE in e.stderr and not merge:
                show_actions_panel(self.window, [
                    noop("Abort, local changes would be overwritten by checkout."),
                    (
                        "Try merging the changes.",
                        lambda: self.window.run_command("gs_checkout_new_branch", {
                            "branch_name": branch_name,
                            "start_point": start_point,
                            "force": force,
                            "merge": True
                        })
                    )
                ])
                return

            elif BRANCH_ALREADY_EXISTS_MESSAGE.format(branch_name) in e.stderr and not force:
                def overwrite_action():
                    old_hash = self.resolve(branch_name)
                    uprint(RECREATE_BRANCH_UNDO_MESSAGE.format(branch_name, old_hash))

                    self.window.run_command("gs_checkout_new_branch", {
                        "branch_name": branch_name,
                        "start_point": start_point,
                        "force": True,
                        "merge": merge
                    })

                show_actions_panel(self.window, [
                    noop(f"Abort, a branch named '{branch_name}' already exists."),
                    (
                        f'Re-create the branch at {start_point or "HEAD"}.',
                        overwrite_action
                    )
                ])
                return

            else:
                e.show_error_panel()
                raise

        self.window.status_message("Created and checked out `{}` branch.".format(branch_name))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_checkout_remote_branch(GsWindowCommand):
    """
    Display a panel of all remote branches.  When the user makes a selection,
    create a corresponding local branch, and set it to the HEAD of the
    selected branch.
    """
    defaults = {
        "remote_branch": ask_for_branch(remote_branches_only=True),
        "local_name": ask_for_name(
            initial_text=lambda remote_branch: remote_branch.split("/", 1)[1],
        ),
    }

    def run(self, remote_branch, local_name):
        self.git("checkout", "-b", local_name, "--track", remote_branch)
        self.window.status_message(
            "Checked out `{}` as local branch `{}`.".format(remote_branch, local_name))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_checkout_current_file_at_commit(LogMixin, WindowCommand, GitCommand):

    """
    Reset the current active file to a given commit.
    """

    def run(self):  # type: ignore[override]
        if self.file_path:
            super().run(file_path=self.file_path)

    def on_highlight(self, commit, file_path=None):
        if not self.app_settings.get("log_show_more_commit_info", True):
            return
        if commit:
            self.window.run_command('gs_show_file_diff', {
                'commit_hash': commit,
                'file_path': file_path
            })

    @util.actions.destructive(description="discard uncommitted changes to file")
    def do_action(self, commit_hash, **kwargs):
        if commit_hash:
            self.checkout_ref(commit_hash, self.file_path)
            self.window.status_message(
                "Successfully checked out {} from {}.".format(
                    self.file_path,
                    self.to_short_hash(commit_hash)
                )
            )
            util.view.refresh_gitsavvy_interfaces(self.window)


class gs_show_file_diff(WindowCommand, GitCommand):
    def run(self, commit_hash, file_path):
        self._commit_hash = commit_hash
        self._file_path = file_path
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        text = self.git(
            "diff",
            "--no-color",
            "-R",
            self._commit_hash,
            '--',
            self._file_path
        )

        view = self.window.create_output_panel("show_file_diff")
        view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime-syntax")
        view.set_read_only(True)
        replace_view_content(view, text)
        self.window.run_command("show_panel", {"panel": "output.show_file_diff"})
        intra_line_colorizer.annotate_intra_line_differences(view)


class CheckoutBranchInputHandler(ListInputHandler):
    def __init__(self, command: gs_checkout_branch) -> None:
        self.command = command
        self.branches: dict[str, Branch] = {}
        self.detach = False

    def name(self) -> str:
        return "branch"

    def list_items(self) -> tuple[list[sublime.ListInputItem], int]:
        branches = self.command.get_branches()
        local_branches = [b for b in branches if b.is_local]
        local_names = {b.name for b in local_branches}
        remote_branches = [
            b for remote in self.command.get_remotes()
            for b in branches
            if b.remote == remote and b.is_remote and b.name not in local_names
        ]
        self.branches = {
            branch_ref(b): b
            for b in (*local_branches, *remote_branches)
        }
        items = [self.branch_item(b) for b in local_branches]
        items.extend(self.branch_item(b) for b in remote_branches)
        if not items:
            items.append(sublime.ListInputItem("There are no branches available.", None))

        previous = self.command.current_state()["last_branches"][-2]
        selected_index = next((
            i for i, item in enumerate(items)
            if (b := self.branches.get(item.value)) and b.is_local and b.name == previous
        ), 0)
        return items, selected_index

    def preview(self, value: str | None) -> str:
        branch = self.branches.get(value) if value else None
        if not branch:
            return "Select a branch."
        if other_worktree(branch, self.command.repo_path):
            action = "to open in a new window"
        elif branch.is_remote:
            action = f"to checkout as `{branch.name}`"
        else:
            action = "to checkout"
        return f"[enter] {action}, [ctrl+enter] to checkout detached"

    def want_event(self) -> bool:
        return True

    def validate(self, value: str | None, event: dict | None = None) -> bool:
        return value in self.branches

    def confirm(self, value: str, event: dict) -> None:
        self.detach = event.get("modifier_keys", {}).get("ctrl", False)

    def cancel(self) -> None:
        self.command._input_handler = None

    def branch_item(self, branch: Branch) -> sublime.ListInputItem:
        return sublime.ListInputItem(
            branch.canonical_name,
            branch_ref(branch),
            annotation=(
                "worktree" if other_worktree(branch, self.command.repo_path) else
                "remote" if branch.is_remote else
                ""
            )
        )


def branch_ref(branch: Branch) -> str:
    namespace = "remotes" if branch.is_remote else "heads"
    return f"refs/{namespace}/{branch.canonical_name}"


def other_worktree(branch: Branch, repo_path: str) -> str | None:
    path = branch.worktree_path
    if path and os.path.normcase(os.path.realpath(path)) != os.path.normcase(os.path.realpath(repo_path)):
        return path
    return None
