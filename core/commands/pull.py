import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


class GsPullCommand(WindowCommand, GitCommand):

    """
    Through a series of panels, allow the user to pull from a remote branch.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        """
        Display a panel of all remotes defined for the repo, then proceed to
        `on_select_remote`.  If no remotes are defined, notify the user and
        proceed no further.
        """
        self.remotes = list(self.get_remotes().keys())
        self.remote_branches = self.get_remote_branches()

        if not self.remotes:
            self.window.show_quick_panel(["There are no remotes available."], None)
        else:
            self.window.show_quick_panel(
                self.remotes,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT
                )

    def on_select_remote(self, remote_index):
        """
        After the user selects a remote, display a panel of branches that are
        present on that remote, then proceed to `on_select_branch`.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if remote_index == -1:
            return

        self.selected_remote = self.remotes[remote_index]
        selected_remote_prefix = self.selected_remote + "/"

        self.branches_on_selected_remote = [
            branch for branch in self.remote_branches
            if branch.startswith(selected_remote_prefix)
        ]

        current_local_branch = self.get_current_branch_name()

        try:
            pre_selected_index = self.branches_on_selected_remote.index(
                selected_remote_prefix + current_local_branch)
        except ValueError:
            pre_selected_index = 0

        def deferred_panel():
            self.window.show_quick_panel(
                self.branches_on_selected_remote,
                self.on_select_branch,
                flags=sublime.MONOSPACE_FONT,
                selected_index=pre_selected_index
            )

        sublime.set_timeout(deferred_panel)

    def on_select_branch(self, branch_index):
        """
        Determine the actual branch name of the user's selection, and proceed
        to `do_pull`.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if branch_index == -1:
            return

        selected_branch = self.branches_on_selected_remote[branch_index].split("/", 1)[1]
        sublime.set_timeout_async(lambda: self.do_pull(self.selected_remote, selected_branch))

    def do_pull(self, remote, branch):
        """
        Perform `git pull remote branch`.
        """
        sublime.status_message("Starting pull...")
        self.pull(remote, branch)
        sublime.status_message("Pull complete.")
        util.view.refresh_gitsavvy(self.window.active_view())
