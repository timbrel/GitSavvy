import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


class GsPull(WindowCommand, GitCommand):
    """
    Pull from remote tracking branch if it is found. Otherwise, use GsPullFromBranchCommand.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        # honor the `pull.rebase` config implictly
        rebase = self.git("config", "pull.rebase", throw_on_stderr=False) or False
        if rebase and rebase.strip() == "true":
            rebase = True
        upstream = self.get_upstream_for_active_branch()
        if upstream:
            remote, remote_branch = upstream.split("/", 1)
            self.pull(remote=remote, remote_branch=remote_branch, rebase=rebase)
        else:
            self.window.run_command("gs_pull_from_branch", {"rebase": rebase})


class GsPullFromBranchCommand(WindowCommand, GitCommand):

    """
    Through a series of panels, allow the user to pull from a remote branch.
    """

    def run(self, rebase=False):
        self.rebase = rebase
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_branch_panel(
            self.on_branch_selection,
            ask_remote_first=True)

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
