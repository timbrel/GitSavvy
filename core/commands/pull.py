from ...common import util
from GitSavvy.core.base_commands import GsWindowCommand, ask_for_branch
from GitSavvy.core.fns import flatten, unique
from GitSavvy.core.runtime import on_worker

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
    Pull from remote tracking branch if it is found. Otherwise, use `gs_pull_from_branch`.
    """

    @on_worker
    def run(self, rebase=None):
        upstream = self.get_upstream_for_active_branch()
        if upstream:
            self.do_pull(upstream.remote, upstream.branch, rebase)
        else:
            self.window.run_command("gs_pull_from_branch", {"rebase": rebase})


ask_for_branch_ = ask_for_branch(
    ask_remote_first=False,
    ignore_current_branch=True,
    memorize_key="last_branch_used_to_pull_from"
)


class gs_pull_from_branch(GsPullBase):
    """
    Through a series of panels, allow the user to pull from a branch.
    """
    defaults = {
        "branch": ask_for_branch_
    }

    @on_worker
    def run(self, branch, rebase=None):
        # type: (str, bool) -> None
        sources: Sequence[Callable[[], List[Branch]]] = (
            # Typically, `ask_for_branch_`s `show_branch_panel` has just called
            # `get_branches` so the cached value in the store should be fresh
            # and good to go.
            lambda: self.current_state().get("branches", []),
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
