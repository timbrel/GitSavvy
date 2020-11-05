from functools import partial

import sublime

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import enqueue_on_worker
from GitSavvy.core.utils import show_actions_panel, noop


__all__ = (
    "gs_push",
    "gs_push_to_branch",
    "gs_push_to_branch_name",
)


MYPY = False
if MYPY:
    from GitSavvy.core.base_commands import Args, Kont


END_PUSH_MESSAGE = "Push complete."
CONFIRM_FORCE_PUSH = ("You are about to `git push {}`. Would you  "
                      "like to proceed?")


class PushBase(GsWindowCommand, GitCommand):
    def do_push(
        self,
        remote,
        branch,
        force=False,
        force_with_lease=False,
        remote_branch=None,
        set_upstream=False
    ):
        # type: (str, str, bool, bool, str, bool) -> None
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

        self.window.status_message("Pushing {} to {}...".format(branch, remote))
        self.push(
            remote,
            branch,
            remote_branch=remote_branch,
            force=force,
            force_with_lease=force_with_lease,
            set_upstream=set_upstream
        )
        self.window.status_message(END_PUSH_MESSAGE)
        util.view.refresh_gitsavvy_interfaces(self.window)


class gs_push(PushBase):
    """
    Push current branch.
    """

    def run(self, local_branch_name=None, force=False, force_with_lease=False):
        # type: (str, bool, bool) -> None
        if local_branch_name:
            local_branch = self.get_local_branch_by_name(local_branch_name)
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

        else:
            self.window.run_command("gs_push_to_branch_name", {
                "local_branch_name": local_branch.name,
                "set_upstream": True,
                "force": force,
                "force_with_lease": force_with_lease
            })


def take_current_branch_name(cmd, args, done):
    # type: (PushBase, Args, Kont) -> None
    current_branch_name = cmd.get_current_branch_name()
    if current_branch_name:
        done(current_branch_name)
    else:
        cmd.window.status_message("Can't push a detached HEAD.")


def ask_for_remote(cmd, args, done):
    # type: (GsWindowCommand, Args, Kont) -> None
    show_remote_panel(done, allow_direct=True)


def ask_for_branch_name(caption, initial_text):
    def handler(cmd, args, done):
        # type: (GsWindowCommand, Args, Kont) -> None
        show_single_line_input_panel(
            caption(args),
            initial_text(args),
            done
        )
    return handler


class gs_push_to_branch_name(PushBase):
    """
    Prompt for remote and remote branch name, then push.
    """
    defaults = {
        "local_branch_name": take_current_branch_name,  # type: ignore[dict-item]
        "remote": ask_for_remote,
        "branch_name": ask_for_branch_name(
            caption=lambda args: "Push to {}/".format(args["remote"]),
            initial_text=lambda args: args["local_branch_name"]
        )
    }

    def run(
        self,
        local_branch_name,
        remote,
        branch_name,
        set_upstream=False,
        force=False,
        force_with_lease=False
    ):
        # type: (str, str, str, bool, bool, bool) -> None
        enqueue_on_worker(
            self.do_push,
            remote,
            local_branch_name,
            remote_branch=branch_name,
            force=force,
            force_with_lease=force_with_lease,
            set_upstream=set_upstream
        )


class gs_push_to_branch(PushBase):
    """
    Through a series of panels, allow the user to push to a specific remote branch.
    """

    def run(self):
        # type: () -> None
        enqueue_on_worker(self.run_async)

    def run_async(self):
        # type: () -> None
        show_branch_panel(self.on_branch_selection, ask_remote_first=True)

    def on_branch_selection(self, branch):
        # type: (str) -> None
        current_local_branch = self.get_current_branch_name()
        selected_remote, selected_branch = branch.split("/", 1)
        enqueue_on_worker(
            self.do_push,
            selected_remote,
            current_local_branch,
            remote_branch=selected_branch
        )
