from functools import partial

import sublime

from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.runtime import enqueue_on_worker, run_on_new_thread
from GitSavvy.core.utils import show_actions_panel, noop


__all__ = (
    "gs_push",
    "gs_push_to_branch",
    "gs_push_to_branch_name",
)


from typing import Dict, Sequence, TypeVar
from GitSavvy.core.base_commands import Args, GsCommand, Kont
T = TypeVar("T")


END_PUSH_MESSAGE = "Push complete."
CONFIRM_FORCE_PUSH = ("You are about to `git push {}`. Would you  "
                      "like to proceed?")


class PushMixin(GsWindowCommand):
    def guess_remote_to_push_to(self, available_remotes):
        # type: (Sequence[str]) -> str
        if len(available_remotes) == 0:
            raise RuntimeError("")
        if len(available_remotes) == 1:
            return next(iter(available_remotes))

        last_remote_used = self.current_state().get("last_remote_used_for_push")
        if last_remote_used in available_remotes:
            return last_remote_used  # type: ignore[return-value]

        defaults = dict(
            (key[:-12], val)  # strip trailing ".pushdefault" from key
            for key, val in (
                line.split()
                for line in self.git(
                    "config",
                    "--get-regexp",
                    r".*\.pushdefault",
                    throw_on_error=False
                ).splitlines()
            )
        )  # type: Dict[str, str]
        for key in (defaults.get("gitsavvy"), defaults.get("remote"), "fork", "origin"):
            if key in available_remotes:
                return key  # type: ignore[return-value]
        return next(iter(available_remotes))

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


class gs_push(PushMixin):
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

        upstream = local_branch.upstream
        if upstream:
            remote, remote_branch = upstream.remote, upstream.branch
            kont = partial(
                enqueue_on_worker,
                self.do_push,
                remote,
                local_branch.name,
                remote_branch=remote_branch,
                force=force,
                force_with_lease=force_with_lease
            )
            if not force and not force_with_lease and "behind" in upstream.status:
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
                kont()  # type: ignore[call-arg]

        else:
            self.window.run_command("gs_push_to_branch_name", {
                "local_branch_name": local_branch.name,
                "set_upstream": True,
                "force": force,
                "force_with_lease": force_with_lease
            })


def take_current_branch_name(cmd, args, done):
    # type: (GsWindowCommand, Args, Kont) -> None
    current_branch_name = cmd.get_current_branch_name()
    if current_branch_name:
        done(current_branch_name)
    else:
        cmd.window.status_message("Can't push a detached HEAD.")


def ask_for_remote(cmd, args, done):
    # type: (PushMixin, Args, Kont) -> None
    available_remotes = list(cmd.get_remotes())
    if len(available_remotes) == 0:
        show_actions_panel(cmd.window, [noop("There are no remotes available.")])
        return

    remote = cmd.guess_remote_to_push_to(available_remotes)
    current_branch_name = args["local_branch_name"]

    show_actions_panel(cmd.window, [
        (
            "Push to '{}/{}'".format(remote, current_branch_name),
            lambda: done(remote, branch_name=current_branch_name)
        ),
        (
            "Configure where to push to...",
            lambda: (
                show_actions_panel(
                    cmd.window,
                    [
                        (r, partial(done, r, remember_used_remote=True))
                        for r in available_remotes
                    ],
                    select=available_remotes.index(remote)
                )
                if len(available_remotes) > 1
                else done(remote)
            )
        ),
    ])


def ask_for_branch_name(caption, initial_text):
    def handler(cmd, args, done):
        # type: (GsCommand, Args, Kont) -> None
        show_single_line_input_panel(
            caption(args),
            initial_text(args),
            done
        )
    return handler


def ask_for_remote_branch(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    show_branch_panel(done, ask_remote_first=True)


class gs_push_to_branch_name(PushMixin):
    """
    Prompt for remote and remote branch name, then push.
    """
    defaults = {
        "local_branch_name": take_current_branch_name,
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
        force_with_lease=False,
        remember_used_remote=False,
    ):
        # type: (str, str, str, bool, bool, bool, bool) -> None
        if remember_used_remote:
            run_on_new_thread(self.git, "config", "--local", "gitsavvy.pushdefault", remote)
            self.update_store({"last_remote_used_for_push": remote})

        enqueue_on_worker(
            self.do_push,
            remote,
            local_branch_name,
            remote_branch=branch_name,
            force=force,
            force_with_lease=force_with_lease,
            set_upstream=set_upstream
        )


class gs_push_to_branch(PushMixin):
    """
    Through a series of panels, allow the user to push to a specific remote branch.
    """
    defaults = {
        "local_branch_name": take_current_branch_name,
        "remote_branch": ask_for_remote_branch
    }

    def run(self, local_branch_name, remote_branch):
        # type: (str, str) -> None
        remote, branch_name = remote_branch.split("/", 1)
        enqueue_on_worker(
            self.do_push,
            remote,
            local_branch_name,
            remote_branch=branch_name
        )
