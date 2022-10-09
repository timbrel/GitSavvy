import sublime
from sublime_plugin import WindowCommand

from .checkout import gs_checkout_remote_branch
from ..git_command import GitCommand, GitSavvyError
from ..ui_mixins.quick_panel import show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from ...common import util
from GitSavvy.core import store


__all__ = (
    "gs_worktree_create",
    "gs_worktree_create_for_remote",
)


class gs_worktree_create(WindowCommand, GitCommand):

    """
    Display a panel of all local branches.
    Then prompt the user for a path.
    """

    def run(self, branch=None):
        sublime.set_timeout_async(lambda: self.run_async(branch))

    def run_async(self, branch=None):
        if branch:
            self.on_branch_selection(branch)
        else:
            show_branch_panel(
                self.on_branch_selection,
                local_branches_only=True,
                ignore_current_branch=True,
                selected_branch=store.current_state(self.repo_path)["last_branches"][-2]
            )

    def on_branch_selection(self, branch):
        self.target_branch = branch
        show_single_line_input_panel(
            initial_text="../{0}".format(branch),
            caption="New worktree path",
            on_done=self.on_path_provided,
        )

    def on_path_provided(self, path):
        self.create_worktree(self.target_branch, path)

    def create_worktree(self, branch, path):
        # type: (str, str) -> None
        try:
            self.git_throwing_silently(
                "worktree", "add",
                path,
                branch
            )
        except GitSavvyError as e:
            e.show_error_panel()
            raise

        self.window.status_message(
            "Created worktree `{}` for branch `{}`.".format(path, branch))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_worktree_create_for_remote(WindowCommand, GitCommand):

    """
    Display a panel of all local branches.
    Then prompt the user for a path.
    """

    def run(self, remote_branch=None):
        sublime.set_timeout_async(lambda: self.run_async(remote_branch))

    def run_async(self, remote_branch):
        gs_checkout_remote_branch(remote_branch=remote_branch)
        self.on_branch_selection(remote_branch)

    def on_branch_selection(self, branch):
        self.target_branch = branch

        show_single_line_input_panel(
            initial_text="../{0}".format(branch),
            caption="New worktree path",
            on_done=self.on_path_provided,
        )

    def on_path_provided(self, path):
        branch = self.target_branch
        try:
            self.git_throwing_silently(
                "worktree", "add",
                path,
            )
        except GitSavvyError as e:
            e.show_error_panel()
            raise

        self.window.status_message(
            "Created worktree `{}` for branch `{}`.".format(path, branch))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)
