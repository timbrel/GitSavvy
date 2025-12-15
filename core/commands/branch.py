from functools import lru_cache
import inspect
import re
import sublime

from . import push
from ..git_command import GitSavvyError
from ..ui_mixins.input_panel import show_single_line_input_panel
from ...common import util
from GitSavvy.core.base_commands import ask_for_local_branch, GsWindowCommand
from ..ui__quick_panel import noop, show_actions_panel
from GitSavvy.core.utils import uprint


__all__ = (
    "gs_create_branch",
    "gs_rename_branch",
    "gs_unset_tracking_information",
    "gs_delete_branch",
)


from typing import Callable, TypeVar
from GitSavvy.core.base_commands import Args, Kont
T = TypeVar("T")


NEW_BRANCH_PROMPT = "Branch name:"
NEW_BRANCH_INVALID = "`{}` is a invalid branch name.\nRead more on $(man git-check-ref-format)"
DELETE_UNDO_MESSAGE = """\
GitSavvy: Deleted branch ({0}), in case you want to undo, run:
  $ git branch {0} {1}
"""
BRANCH_ALREADY_EXISTS_MESSAGE = "a branch named '{0}' already exists"
RECREATE_BRANCH_UNDO_MESSAGE = """\
GitSavvy: Re-created branch '{0}', in case you want to undo, run:
  $ git branch --force {0} {1}
"""

EXTRACT_COMMIT = re.compile(r"\(was (.+)\)")
NOT_MERGED_WARNING = re.compile(r"the branch.*is not fully merged", re.I)
CANT_DELETE_USED_BRANCH = re.compile(r"cannot delete branch .+ (checked out|used by worktree) at ", re.I)


def just(value):
    # type: (T) -> Callable[..., T]
    return lambda: value


def call_with_wanted_args(fn, args):
    # type: (Callable[..., T], Args) -> T
    fs = _signature(fn)
    return fn(**{k: args[k] for k in fs.parameters.keys() if k in args})


@lru_cache()
def _signature(fn):
    return inspect.signature(fn)


def ask_for_name(caption=just(NEW_BRANCH_PROMPT), initial_text=just("")):
    def handler(cmd, args, done, initial_text_=None):
        # type: (GsWindowCommand, Args, Kont, str) -> None
        def done_(branch_name):
            branch_name = branch_name.strip().replace(" ", "-")
            if not branch_name:
                return None
            if not cmd.validate_branch_name(branch_name):
                sublime.error_message(NEW_BRANCH_INVALID.format(branch_name))
                handler(cmd, args, done, initial_text_=branch_name)
                return None
            done(branch_name)

        show_single_line_input_panel(
            call_with_wanted_args(caption, args),
            initial_text_ or call_with_wanted_args(initial_text, args),
            done_
        )
    return handler


class gs_create_branch(GsWindowCommand):
    defaults = {
        "branch_name": ask_for_name(),
    }

    def run(self, branch_name, start_point=None, force=False):
        # type: (str, str, bool) -> None
        try:
            self.git_throwing_silently(
                "branch",
                "--force" if force else None,
                branch_name,
                start_point
            )
        except GitSavvyError as e:
            if BRANCH_ALREADY_EXISTS_MESSAGE.format(branch_name) in e.stderr and not force:
                def overwrite_action():
                    old_hash = self.git("rev-parse", branch_name).strip()
                    uprint(RECREATE_BRANCH_UNDO_MESSAGE.format(branch_name, old_hash))

                    self.window.run_command("gs_create_branch", {
                        "branch_name": branch_name,
                        "start_point": start_point,
                        "force": True,
                    })

                show_actions_panel(self.window, [
                    noop(f"Abort, a branch named '{branch_name}' already exists."),
                    (
                        f'Re-create the branch at {start_point or "HEAD"}.',
                        overwrite_action
                    )
                ])
                return

            else:
                e.show_error_panel()
                raise

        self.window.status_message("Created {}{}".format(
            branch_name,
            " at {}".format(start_point) if start_point else "")
        )
        util.view.refresh_gitsavvy_interfaces(self.window)


class gs_rename_branch(GsWindowCommand):
    defaults = {
        "branch": push.take_current_branch_name,
        "new_name": ask_for_name(
            caption=lambda branch: "Enter new branch name (for {}):".format(branch),
            initial_text=lambda branch: branch,
        ),
    }

    def run(self, branch, new_name):
        # type: (str, str) -> None
        if branch == new_name:
            return
        self.git("branch", "-m", branch, new_name)
        self.window.status_message("Renamed {} -> {}".format(branch, new_name))
        util.view.refresh_gitsavvy_interfaces(self.window)


class gs_unset_tracking_information(GsWindowCommand):
    defaults = {
        "branch": push.take_current_branch_name,
    }

    def run(self, branch):
        self.git("branch", branch, "--unset-upstream")
        self.window.status_message("Removed the upstream information for {}".format(branch))
        util.view.refresh_gitsavvy_interfaces(self.window)


class gs_delete_branch(GsWindowCommand):
    defaults = {
        "branch": ask_for_local_branch,
    }

    @util.actions.destructive(description="delete a local branch")
    def run(self, branch, force=False):
        # type: (str, bool) -> None
        if force:
            rv = self.git("branch", "-D", branch)
        else:
            try:
                rv = self.git_throwing_silently("branch", "-d", branch)
            except GitSavvyError as e:
                if NOT_MERGED_WARNING.search(e.stderr):
                    self.offer_force_deletion(branch)
                    return
                if (
                    CANT_DELETE_USED_BRANCH.search(e.stderr)
                    and branch == self.get_current_branch_name()
                ):
                    self.offer_detaching_head(branch)
                    return
                e.show_error_panel()
                raise

        match = EXTRACT_COMMIT.search(rv.strip())
        if match:
            commit = match.group(1)
            uprint(DELETE_UNDO_MESSAGE.format(branch, commit))
        self.window.status_message(
            "Deleted local branch ({}).".format(branch)
            + (" Open Sublime console for undo instructions." if match else "")
        )
        util.view.refresh_gitsavvy_interfaces(self.window)

    def offer_force_deletion(self, branch_name):
        # type: (str) -> None
        show_actions_panel(self.window, [
            noop("Abort, '{}' is not fully merged.".format(branch_name)),
            (
                "Delete anyway.",
                lambda: self.window.run_command("gs_delete_branch", {
                    "branch": branch_name,
                    "force": True
                })
            )
        ])

    def offer_detaching_head(self, branch):
        def kont():
            self.git("checkout", branch, "--detach")
            self.window.run_command("gs_delete_branch", {"branch": branch, "force": False})

        show_actions_panel(self.window, [
            noop("Abort, '{}' is checked out.".format(branch)),
            ("Detach HEAD and delete.", kont)
        ])
