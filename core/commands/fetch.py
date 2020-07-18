from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..runtime import enqueue_on_worker
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel


__all__ = (
    "gs_fetch",
)


class gs_fetch(WindowCommand, GitCommand):

    """
    Display a panel of all git remotes for active repository and
    do a `git fetch` asynchronously.
    """

    def run(self, remote=None):
        if remote:
            return enqueue_on_worker(self.do_fetch, remote)

        show_remote_panel(self.on_remote_selection, show_option_all=True, allow_direct=True)

    def on_remote_selection(self, remote):
        if not remote:
            return
        if remote is True:
            enqueue_on_worker(self.do_fetch)
        else:
            enqueue_on_worker(self.do_fetch, remote)

    def do_fetch(self, remote=None):
        if remote is None:
            self.window.status_message("Start fetching all remotes...")
        else:
            self.window.status_message("Start fetching {}...".format(remote))

        self.fetch(remote)
        self.window.status_message("Fetch complete.")
        util.view.refresh_gitsavvy(self.window.active_view())
