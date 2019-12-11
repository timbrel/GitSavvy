import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel


START_PUSH_MESSAGE = "Starting push..."
END_PUSH_MESSAGE = "Push complete."
PUSH_TO_BRANCH_NAME_PROMPT = "Enter remote branch name:"
SET_UPSTREAM_PROMPT = ("You have not set an upstream for the active branch.  "
                       "Would you like to set one?")
CONFIRM_FORCE_PUSH = ("You are about to `git push {}`. Would you  "
                      "like to proceed?")


class PushBase(GitCommand):
    set_upstream = False

    def do_push(self, remote, branch, force=False, force_with_lease=False, remote_branch=None):
        """
        Perform `git push remote branch`.
        """
        if self.savvy_settings.get("confirm_force_push", True):
            if force:
                if not sublime.ok_cancel_dialog(CONFIRM_FORCE_PUSH.format("--force")):
                    return
            elif force_with_lease:
                if not sublime.ok_cancel_dialog(CONFIRM_FORCE_PUSH.format("--force--with-lease")):
                    return

        self.window.status_message(START_PUSH_MESSAGE)
        self.push(
            remote,
            branch,
            set_upstream=self.set_upstream,
            force=force,
            force_with_lease=force_with_lease,
            remote_branch=remote_branch
        )
        self.window.status_message(END_PUSH_MESSAGE)
        util.view.refresh_gitsavvy(self.window.active_view())


class GsPushCommand(WindowCommand, PushBase):

    """
    Push current branch.
    """

    def run(self, local_branch_name=None, force=False, force_with_lease=False):
        self.force = force
        self.force_with_lease = force_with_lease
        self.local_branch_name = local_branch_name
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        if not self.local_branch_name:
            self.local_branch_name = self.get_current_branch_name()

        upstream = self.get_local_branch(self.local_branch_name).tracking

        if upstream:
            remote, remote_branch = upstream.split("/", 1)
            self.do_push(
                remote,
                self.local_branch_name,
                remote_branch=remote_branch,
                force=self.force,
                force_with_lease=self.force_with_lease)
        elif self.savvy_settings.get("prompt_for_tracking_branch"):
            if sublime.ok_cancel_dialog(SET_UPSTREAM_PROMPT):
                self.window.run_command("gs_push_to_branch_name", {
                    "local_branch_name": self.local_branch_name,
                    "set_upstream": True,
                    "force": self.force,
                    "force_with_lease": self.force_with_lease
                })
        else:
            # if `prompt_for_tracking_branch` is false, ask for a remote and perform
            # push current branch to a remote branch with the same name
            self.window.run_command("gs_push_to_branch_name", {
                "local_branch_name": self.local_branch_name,
                "branch_name": self.local_branch_name,
                "set_upstream": False,
                "force": self.force,
                "force_with_lease": self.force_with_lease
            })


class GsPushToBranchCommand(WindowCommand, PushBase):

    """
    Through a series of panels, allow the user to push to a specific remote branch.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_branch_panel(self.on_branch_selection, ask_remote_first=True)

    def on_branch_selection(self, branch):
        if not branch:
            return
        current_local_branch = self.get_current_branch_name()
        selected_remote, selected_branch = branch.split("/", 1)
        sublime.set_timeout_async(
            lambda: self.do_push(
                selected_remote, current_local_branch, remote_branch=selected_branch))


class GsPushToBranchNameCommand(WindowCommand, PushBase):

    """
    Prompt for remote and remote branch name, then push.
    """

    def run(
            self,
            local_branch_name=None,
            branch_name=None,
            set_upstream=False,
            force=False,
            force_with_lease=False):
        if local_branch_name:
            self.local_branch_name = local_branch_name
        else:
            self.local_branch_name = self.get_current_branch_name()

        self.branch_name = branch_name
        self.set_upstream = set_upstream
        self.force = force
        self.force_with_lease = force_with_lease
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_remote_panel(self.on_remote_selection)

    def on_remote_selection(self, remote):
        """
        After the user selects a remote, prompt the user for a branch name.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if not remote:
            return

        self.selected_remote = remote

        if self.branch_name:
            self.on_entered_branch_name(self.branch_name)
        else:
            show_single_line_input_panel(
                PUSH_TO_BRANCH_NAME_PROMPT,
                self.local_branch_name,
                self.on_entered_branch_name
            )

    def on_entered_branch_name(self, branch):
        """
        Push to the remote that was previously selected and provided branch
        name.
        """
        sublime.set_timeout_async(lambda: self.do_push(
            self.selected_remote,
            self.local_branch_name,
            force=self.force,
            force_with_lease=self.force_with_lease,
            remote_branch=branch))
