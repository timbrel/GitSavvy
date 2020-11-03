from functools import partial

import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.runtime import enqueue_on_worker
from GitSavvy.core.utils import show_actions_panel, noop


__all__ = (
    "gs_push",
    "gs_push_to_branch",
    "gs_push_to_branch_name",
)


START_PUSH_MESSAGE = "Starting push..."
END_PUSH_MESSAGE = "Push complete."
PUSH_TO_BRANCH_NAME_PROMPT = "Enter remote branch name:"
SET_UPSTREAM_PROMPT = ("You have not set an upstream for the active branch.  "
                       "Would you like to set one?")
CONFIRM_FORCE_PUSH = ("You are about to `git push {}`. Would you  "
                      "like to proceed?")


class PushBase(WindowCommand, GitCommand):
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
                if not sublime.ok_cancel_dialog(CONFIRM_FORCE_PUSH.format("--force-with-lease")):
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


class gs_push(PushBase):
    """
    Push current branch.
    """

    def run(self, local_branch_name=None, force=False, force_with_lease=False):
        # type: (str, bool, bool) -> None
        if local_branch_name:
            local_branch = self.get_local_branch(local_branch_name)
            if not local_branch:
                sublime.message_dialog("'{}' is not a local branch name.")
                return
        else:
            local_branch = self.get_current_branch()
            if not local_branch:
                sublime.message_dialog("Can't push a detached HEAD.")
                return

        upstream = local_branch.tracking
        if upstream:
            remote, remote_branch = upstream.split("/", 1)
            kont = partial(
                enqueue_on_worker,
                self.do_push,
                remote,
                local_branch.name,
                remote_branch=remote_branch,
                force=force,
                force_with_lease=force_with_lease
            )
            if not force and not force_with_lease and "behind" in local_branch.tracking_status:
                show_actions_panel(self.window, [
                    noop(
                        "Abort, '{}' is behind '{}/{}'."
                        .format(local_branch.name, remote, remote_branch)
                    ),
                    (
                        "Forcefully push.",
                        partial(kont, force_with_lease=True)
                    )
                ])
                return
            else:
                kont()

        elif (
            not self.savvy_settings.get("prompt_for_tracking_branch") or
            sublime.ok_cancel_dialog(SET_UPSTREAM_PROMPT)
        ):
            self.window.run_command("gs_push_to_branch_name", {
                "local_branch_name": local_branch.name,
                "set_upstream": True,
                "force": force,
                "force_with_lease": force_with_lease
            })


class gs_push_to_branch(PushBase):
    """
    Through a series of panels, allow the user to push to a specific remote branch.
    """

    def run(self):
        enqueue_on_worker(self.run_async)

    def run_async(self):
        show_branch_panel(self.on_branch_selection, ask_remote_first=True)

    def on_branch_selection(self, branch):
        current_local_branch = self.get_current_branch_name()
        selected_remote, selected_branch = branch.split("/", 1)
        enqueue_on_worker(
            self.do_push,
            selected_remote,
            current_local_branch,
            remote_branch=selected_branch
        )


class gs_push_to_branch_name(PushBase):
    """
    Prompt for remote and remote branch name, then push.
    """

    def run(
        self,
        local_branch_name=None,
        branch_name=None,
        set_upstream=False,
        force=False,
        force_with_lease=False
    ):
        if local_branch_name:
            self.local_branch_name = local_branch_name
        else:
            self.local_branch_name = self.get_current_branch_name()

        self.branch_name = branch_name
        self.set_upstream = set_upstream
        self.force = force
        self.force_with_lease = force_with_lease
        enqueue_on_worker(self.run_async)

    def run_async(self):
        show_remote_panel(self.on_remote_selection, allow_direct=True)

    def on_remote_selection(self, remote):
        """
        After the user selects a remote, prompt the user for a branch name.
        """
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
        enqueue_on_worker(
            self.do_push,
            self.selected_remote,
            self.local_branch_name,
            remote_branch=branch,
            force=self.force,
            force_with_lease=self.force_with_lease
        )
