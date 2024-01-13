from ..runtime import on_worker
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel
from GitSavvy.core.base_commands import ask_for_branch, GsWindowCommand


__all__ = (
    "gs_fetch",
    "gs_ff_update_branch"
)


from GitSavvy.core.base_commands import Args, GsCommand, Kont


def ask_for_remote(cmd, args, done):
    # type: (GsCommand, Args, Kont) -> None
    show_remote_panel(done, allow_direct=True, show_option_all=True)


class gs_fetch(GsWindowCommand):
    """
    Display a panel of all git remotes for active repository and
    do a `git fetch` asynchronously.
    """
    defaults = {
        "remote": ask_for_remote
    }

    @on_worker
    def run(self, remote, refspec=None):
        fetch_all = remote == "<ALL>"
        if fetch_all:
            self.window.status_message("Start fetching all remotes...")
        else:
            self.window.status_message("Start fetching {}...".format(remote))

        self.fetch(None if fetch_all else remote, refspec)
        self.window.status_message("Fetch complete.")
        util.view.refresh_gitsavvy_interfaces(self.window)


class gs_ff_update_branch(GsWindowCommand):
    defaults = {
        "branch": ask_for_branch(local_branches_only=True)
    }

    @on_worker
    def run(self, branch):
        local_branch = self.get_local_branch_by_name(branch)
        if not local_branch:
            raise RuntimeError(
                "repo and view inconsistent.  "
                "can't fetch more info about branch {}"
                .format(branch)
            )

        if not local_branch.upstream:
            self.window.status_message("{} has no upstream set.".format(branch))
            return

        self.window.run_command("gs_fetch", {
            "remote": local_branch.upstream.remote,
            "refspec": "{}:{}".format(local_branch.upstream.branch, branch)
        })
