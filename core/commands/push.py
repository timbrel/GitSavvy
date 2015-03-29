import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


NO_REMOTES_MESSAGE = "You have not configured any remotes."
START_PUSH_MESSAGE = "Starting push..."
END_PUSH_MESSAGE = "Push complete."
PUSH_TO_BRANCH_NAME_PROMPT = "Enter remote branch name:"
SET_UPSTREAM_PROMPT = ("You have not set an upstream for the active branch.  "
                       "Would you like to set one?")


class PushBase(GitCommand):
    set_upstream = False

    def do_push(self, remote, branch, force=False):
        """
        Perform `git push remote branch`.
        """
        sublime.status_message(START_PUSH_MESSAGE)
        self.push(remote, branch, set_upstream=self.set_upstream, force=force)
        sublime.status_message(END_PUSH_MESSAGE)
        util.view.refresh_gitsavvy(self.window.active_view())


class GsPushCommand(WindowCommand, PushBase):

    """
    Perform a normal `git push`.
    """

    def run(self, force=False):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        if savvy_settings.get("prompt_for_tracking_branch") and not self.get_upstream_for_active_branch():
            if sublime.ok_cancel_dialog(SET_UPSTREAM_PROMPT):
                self.window.run_command("gs_push_to_branch_name", {"set_upstream": True})
        else:
            sublime.set_timeout_async(lambda: self.do_push(None, None, force=force))


class GsPushToBranchCommand(WindowCommand, PushBase):

    """
    Through a series of panels, allow the user to push to a specific remote branch.
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
            self.window.show_quick_panel([NO_REMOTES_MESSAGE], None)
        else:
            pre_selected_idx = (self.remotes.index(self.last_remote_used)
                                if self.last_remote_used in self.remotes
                                else 0)

            self.window.show_quick_panel(
                self.remotes,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT,
                selected_index=pre_selected_idx
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
        self.last_remote_used = self.selected_remote
        selected_remote_prefix = self.selected_remote + "/"

        self.branches_on_selected_remote = [
            branch for branch in self.remote_branches
            if branch.startswith(selected_remote_prefix)
        ]

        current_local_branch = self.get_current_branch_name()

        try:
            pre_selected_idx = self.branches_on_selected_remote.index(
                selected_remote_prefix + current_local_branch)
        except ValueError:
            pre_selected_idx = 0

        def deferred_panel():
            self.window.show_quick_panel(
                self.branches_on_selected_remote,
                self.on_select_branch,
                flags=sublime.MONOSPACE_FONT,
                selected_index=pre_selected_idx
            )

        sublime.set_timeout(deferred_panel)

    def on_select_branch(self, branch_index):
        """
        Determine the actual branch name of the user's selection, and proceed
        to `do_push`.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if branch_index == -1:
            return

        selected_branch = self.branches_on_selected_remote[branch_index].split("/", 1)[1]
        sublime.set_timeout_async(lambda: self.do_push(self.selected_remote, selected_branch))


class GsPushToBranchNameCommand(WindowCommand, PushBase):

    """
    Prompt for remote and remote branch name, then push.
    """

    def run(self, set_upstream=False):
        self.set_upstream = set_upstream
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
            self.window.show_quick_panel([NO_REMOTES_MESSAGE], None)
        else:
            pre_selected_idx = (self.remotes.index(self.last_remote_used)
                                if self.last_remote_used in self.remotes
                                else 0)

            self.window.show_quick_panel(
                self.remotes,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT,
                selected_index=pre_selected_idx
                )

    def on_select_remote(self, remote_index):
        """
        After the user selects a remote, prompt the user for a branch name.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if remote_index == -1:
            return

        self.selected_remote = self.remotes[remote_index]
        self.last_remote_used = self.selected_remote
        current_local_branch = self.get_current_branch_name()

        self.window.show_input_panel(
            PUSH_TO_BRANCH_NAME_PROMPT,
            current_local_branch,
            self.on_entered_branch_name,
            None,
            None
            )

    def on_entered_branch_name(self, branch):
        """
        Push to the remote that was previously selected and provided branch
        name.
        """
        sublime.set_timeout_async(lambda: self.do_push(self.selected_remote, branch))
