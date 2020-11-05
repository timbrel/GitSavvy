from ..git_command import GitCommand
from ..runtime import enqueue_on_worker
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel
from GitSavvy.core.base_commands import GsWindowCommand


__all__ = (
    "gs_fetch",
)


MYPY = False
if MYPY:
    from GitSavvy.core.base_commands import Args, Kont


def ask_for_remote(cmd, args, done):
    # type: (GsWindowCommand, Args, Kont) -> None
    show_remote_panel(done, allow_direct=True, show_option_all=True)


class gs_fetch(GsWindowCommand, GitCommand):
    """
    Display a panel of all git remotes for active repository and
    do a `git fetch` asynchronously.
    """
    defaults = {
        "remote": ask_for_remote
    }

    def run(self, remote):
        # type: (str) -> None
        enqueue_on_worker(self.do_fetch, remote)

    def do_fetch(self, remote):
        # type: (str) -> None
        fetch_all = remote == "<ALL>"
        if fetch_all:
            self.window.status_message("Start fetching all remotes...")
        else:
            self.window.status_message("Start fetching {}...".format(remote))

        self.fetch(None if fetch_all else remote)
        self.window.status_message("Fetch complete.")
        util.view.refresh_gitsavvy_interfaces(self.window)
