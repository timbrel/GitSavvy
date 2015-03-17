import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


NEW_BRANCH_PROMPT = "Branch name:"


class GsCheckoutBranchCommand(WindowCommand, GitCommand):

    """
    Display a panel of all local branches.  Change to the branch the
    user selected.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        stdout = self.git("branch", "--no-color", "--no-column")
        branch_entries = (branch.strip() for branch in stdout.split("\n") if branch)

        # The line with the active branch will begin with an asterisk.
        self.local_inactive_branches = [branch for branch in branch_entries if not branch[0] == "*"]

        if not self.local_inactive_branches:
            self.window.show_quick_panel(["There are no branches available."], None)
        else:
            self.window.show_quick_panel(
                self.local_inactive_branches,
                self.on_selection,
                flags=sublime.MONOSPACE_FONT
                )

    def on_selection(self, branch_name_index):
        if branch_name_index == -1:
            return

        branch_name = self.local_inactive_branches[branch_name_index]
        self.git("checkout", branch_name)
        sublime.status_message("Checked out `{}` branch.".format(branch_name))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsCheckoutNewBranchCommand(WindowCommand, GitCommand):

    """
    Prompt the user for a new branch name, create it, and check it out.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        self.window.show_input_panel(NEW_BRANCH_PROMPT, "", self.on_done, None, None)

    def on_done(self, branch_name):
        self.git("checkout", "-b", branch_name)
        sublime.status_message("Created and checked out `{}` branch.".format(branch_name))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsCheckoutRemoteBranchCommand(WindowCommand, GitCommand):

    """
    Display a panel of all remote branches.  When the user makes a selection,
    create a corresponding local branch, and set it to the HEAD of the
    selected branch.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        self.remote_branches = self.get_remote_branches()
        self.window.show_quick_panel(
            self.remote_branches,
            self.on_selection,
            flags=sublime.MONOSPACE_FONT
            )

    def on_selection(self, remote_branch_index):
        if remote_branch_index == -1:
            return

        remote_branch = self.remote_branches[remote_branch_index]
        local_name = remote_branch.split("/", 1)[1]
        self.git("checkout", "-b", local_name, "--track", remote_branch)
        sublime.status_message("Checked out `{}` as local branch `{}`.".format(remote_branch, local_name))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsCheckoutCurrentFileCommand(WindowCommand, GitCommand):

    """
    Reset the current active file to HEAD.
    """

    def run(self):
        if self.file_path:
            self.checkout_file(self.file_path)
            sublime.status_message("Successfully checked out {} from head.".format(self.file_path))
