import sublime
from sublime_plugin import WindowCommand

from ..git_mixins import GitLabRemotesMixin
from ...core.git_command import GitCommand
from ...core.ui_mixins.quick_panel import show_branch_panel


__all__ = (
    "gs_gitlab_configure_remote",
)


class gs_gitlab_configure_remote(WindowCommand, GitLabRemotesMixin, GitCommand):

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_branch_panel(
            self.on_branch_selection,
            ask_remote_first=True,
            selected_branch=self.get_integrated_branch_name()
        )

    def on_branch_selection(self, branch):
        """
        After the user selects a branch, configure integrated remote branch.
        """
        remote, remote_branch = branch.split("/", 1)

        self.git("config", "--local", "--unset-all", "GitSavvy.glRemote", throw_on_error=False)
        self.git("config", "--local", "--add", "GitSavvy.glRemote", remote)

        self.git("config", "--local", "--unset-all", "GitSavvy.glBranch", throw_on_error=False)
        self.git("config", "--local", "--add", "GitSavvy.glBranch", remote_branch)

        sublime.status_message("Successfully configured GitLab integration.")
