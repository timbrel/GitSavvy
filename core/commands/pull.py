import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


class GsPullCommand(WindowCommand, GitCommand):

    """
    Through a series of panels, allow the user to pull from a remote branch.
    """

    def run(self, local_branch_name=None, rebase=False):
        self.local_branch_name = local_branch_name
        self.rebase = rebase
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_branch_panel(
            self.on_branch_selection,
            ask_remote_first=True,
            selected_branch=self.local_branch_name)

    def on_branch_selection(self, branch):
        if not branch:
            return

        selected_remote, selected_remote_branch = branch.split("/", 1)

        sublime.set_timeout_async(
            lambda: self.do_pull(selected_remote, selected_remote_branch))

    def do_pull(self, remote, remote_branch):
        """
        Perform `git pull remote branch`.
        """
        sublime.status_message("Starting pull...")
        self.pull(remote=remote, remote_branch=remote_branch, rebase=self.rebase)
        sublime.status_message("Pull complete.")
        util.view.refresh_gitsavvy(self.window.active_view())
