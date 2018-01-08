import re
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


NEW_BRANCH_PROMPT = "Branch name:"
NEW_BRANCH_INVALID = "`{}` is a invalid branch name.\nRead more on $(man git-check-ref-format)"
_is_valid_branch_name = re.compile(r"^(?!\.|.*\.\..*|.*@.*|\/)[a-zA-Z0-9\-\_\/\.\u263a-\U0001f645]+(?<!\.lock)(?<!\/)(?<!\.)$")


class GsCheckoutBranchCommand(WindowCommand, GitCommand):

    """
    Display a panel of all local branches.  Change to the branch the
    user selected.
    """

    def run(self, branch=None):
        sublime.set_timeout_async(lambda: self.run_async(branch), 0)

    def run_async(self, branch):
        if branch:
            self.on_branch_selection(branch)
        else:
            show_branch_panel(
                self.on_branch_selection,
                local_branches_only=True,
                ignore_current_branch=True)

    def on_branch_selection(self, branch):
        if not branch:
            return

        self.git("checkout", branch)
        self.window.status_message("Checked out `{}` branch.".format(branch))
        util.view.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)


class GsCheckoutNewBranchCommand(WindowCommand, GitCommand):

    """
    Prompt the user for a new branch name, create it, and check it out.
    """

    def run(self, base_branch=None):
        sublime.set_timeout_async(lambda: self.run_async(base_branch))

    def run_async(self, base_branch=None):
        self.base_branch = base_branch
        v = self.window.show_input_panel(
            NEW_BRANCH_PROMPT, base_branch or "", self.on_done, None, None)
        v.run_command("select_all")

    def on_done(self, branch_name):
        match = _is_valid_branch_name.match(branch_name)
        if not match:
            sublime.error_message(NEW_BRANCH_INVALID.format(branch_name))
            sublime.set_timeout_async(self.run_async(branch_name))
            return None

        self.git(
            "checkout", "-b",
            branch_name,
            self.base_branch if self.base_branch else None)
        self.window.status_message("Created and checked out `{}` branch.".format(branch_name))
        util.view.refresh_gitsavvy(
            self.window.active_view(),
            refresh_sidebar=True,
            interface_reset_cursor=True)


class GsCheckoutRemoteBranchCommand(WindowCommand, GitCommand):

    """
    Display a panel of all remote branches.  When the user makes a selection,
    create a corresponding local branch, and set it to the HEAD of the
    selected branch.
    """

    def run(self, remote_branch=None):
        sublime.set_timeout_async(lambda: self.run_async(remote_branch))

    def run_async(self, remote_branch):
        if remote_branch:
            self.on_branch_selection(remote_branch)
        else:
            show_branch_panel(
                self.on_branch_selection,
                remote_branches_only=True)

    def on_branch_selection(self, remote_branch, local_name=None):
        if not remote_branch:
            return

        self.remote_branch = remote_branch
        if not local_name:
            local_name = remote_branch.split("/", 1)[1]
        v = self.window.show_input_panel(
            NEW_BRANCH_PROMPT,
            local_name,
            self.on_enter_local_name,
            None,
            None)
        v.run_command("select_all")

    def on_enter_local_name(self, branch_name):

        match = _is_valid_branch_name.match(branch_name)
        if not match:
            sublime.error_message(NEW_BRANCH_INVALID.format(branch_name))
            sublime.set_timeout_async(self.on_branch_selection(self.remote_branch, branch_name))
            return None

        self.git("checkout", "-b", branch_name, "--track", self.remote_branch)
        self.window.status_message(
            "Checked out `{}` as local branch `{}`.".format(self.remote_branch, branch_name))
        util.view.refresh_gitsavvy(
            self.window.active_view(),
            refresh_sidebar=True,
            interface_reset_cursor=True
        )


class GsCheckoutCurrentFileCommand(WindowCommand, GitCommand):

    """
    Reset the current active file to HEAD.
    """

    def run(self):
        if self.file_path:
            self.checkout_file(self.file_path)
            self.window.status_message("Successfully checked out {} from head.".format(self.file_path))
