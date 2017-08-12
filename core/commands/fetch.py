import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel


class GsFetchCommand(WindowCommand, GitCommand):

    """
    Display a panel of all git remotes for active repository and
    do a `git fetch` asynchronously.
    """

    def run(self, remote=None):
        if remote:
            return self.do_fetch(remote)

        show_remote_panel(self.on_remote_selection, show_option_all=True)

    def on_remote_selection(self, remote):
        if not remote:
            return
        if remote is True:
            sublime.set_timeout_async(lambda: self.do_fetch())
        else:
            sublime.set_timeout_async(lambda: self.do_fetch(remote))

    def do_fetch(self, remote=None):
        if remote is None:
            sublime.status_message("Starting fetch all remotes...")
        else:
            sublime.status_message("Starting fetch {}...".format(remote))

        self.fetch(remote)
        sublime.status_message("Fetch complete.")
        util.view.refresh_gitsavvy(self.window.active_view())
