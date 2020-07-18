import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


__all__ = (
    "gs_pull",
    "gs_pull_from_branch",
)


class GsPullBase(WindowCommand, GitCommand):
    def do_pull(self, remote, remote_branch, rebase):
        """
        Perform `git pull remote branch`.
        """
        self.window.status_message("Starting pull...")
        output = self.pull(remote=remote, remote_branch=remote_branch, rebase=rebase).strip()
        self.window.status_message(
            output if output == "Already up to date." else "Pull complete."
        )
        util.view.refresh_gitsavvy(self.window.active_view())


class gs_pull(GsPullBase):
    """
    Pull from remote tracking branch if it is found. Otherwise, use GsPullFromBranchCommand.
    """

    def run(self, rebase=False):
        self.rebase = rebase
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        rebase = self.rebase
        if not rebase:
            # honor the `pull.rebase` config implictly
            pull_rebase = self.git("config", "pull.rebase", throw_on_stderr=False)
            if pull_rebase and pull_rebase.strip() == "true":
                rebase = True
        upstream = self.get_upstream_for_active_branch()
        if upstream:
            remote, remote_branch = upstream.split("/", 1)
            self.do_pull(remote=remote, remote_branch=remote_branch, rebase=rebase)
        else:
            self.window.run_command("gs_pull_from_branch", {"rebase": rebase})


class gs_pull_from_branch(GsPullBase):
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
            lambda: self.do_pull(selected_remote, selected_remote_branch, self.rebase))
