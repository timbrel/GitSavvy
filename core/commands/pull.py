from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel
from GitSavvy.core.base_commands import Args, GsCommand, GsWindowCommand, Kont
from GitSavvy.core.fns import flatten, unique
from GitSavvy.core.runtime import on_worker
from GitSavvy.core import store

from typing import Callable, List, Optional, Sequence
from ..git_mixins.branches import Branch


__all__ = (
    "gs_pull",
    "gs_pull_from_branch",
)


class GsPullBase(GsWindowCommand):
    def do_pull(self, remote, remote_branch, rebase):
        # type: (str, str, Optional[bool]) -> None
        """
        Perform `git pull remote branch`.
        """
        self.window.status_message("Starting pull...")
        output = self.pull(remote, remote_branch, rebase).strip()
        self.window.status_message(
            output if output == "Already up to date." else "Pull complete."
        )
        util.view.refresh_gitsavvy(self.window.active_view())


class gs_pull(GsPullBase):
    """
    Pull from remote tracking branch if it is found. Otherwise, use GsPullFromBranchCommand.
    """

    @on_worker
    def run(self, rebase=None):
        upstream = self.get_upstream_for_active_branch()
        if upstream:
            self.do_pull(upstream.remote, upstream.branch, rebase)
        else:
            self.window.run_command("gs_pull_from_branch", {"rebase": rebase})


def ask_for_branch(self, args, done):
    # type: (GsCommand, Args, Kont) -> None
    last_used_branch = store.current_state(self.repo_path).get("last_branch_used_to_pull_from")

    def _done(branch):
        store.update_state(self.repo_path, {"last_branch_used_to_pull_from": branch})
        done(branch)

    show_branch_panel(
        _done,
        ask_remote_first=False,
        ignore_current_branch=True,
        selected_branch=last_used_branch
    )


class gs_pull_from_branch(GsPullBase):
    """
    Through a series of panels, allow the user to pull from a branch.
    """
    defaults = {
        "branch": ask_for_branch
    }

    @on_worker
    def run(self, branch, rebase=None):
        # type: (str, bool) -> None
        sources: Sequence[Callable[[], List[Branch]]] = (
            # Typically, `ask_for_branch`s `show_branch_panel` has just called
            # `get_branches` so the cached value in the store should be fresh
            # and good to go.
            lambda: store.current_state(self.repo_path).get("branches", []),
            self.get_branches,
        )
        branches = unique(flatten(getter() for getter in sources))
        for branch_ in branches:
            if branch_.canonical_name == branch:
                self.do_pull(branch_.remote or ".", branch_.name, rebase)
                break
        else:
            self.window.status_message(
                f"fatal: the name '{branch}' is not in the list of the current branches")
