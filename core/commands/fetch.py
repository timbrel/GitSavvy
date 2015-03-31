import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


ALL_REMOTES = "All remotes."


class GsFetchCommand(WindowCommand, GitCommand):

    """
    Display a panel of all git remotes for active repository and
    do a `git fetch` asynchronously.
    """

    def run(self):
        self.remotes = list(self.get_remotes().keys())

        if not self.remotes:
            self.window.show_quick_panel(["There are no remotes available."], None)
        else:
            if len(self.remotes) > 1:
                self.remotes.append(ALL_REMOTES)

            pre_selected_idx = (self.remotes.index(self.last_remote_used)
                                if self.last_remote_used in self.remotes
                                else 0)

            self.window.show_quick_panel(
                self.remotes,
                self.on_selection,
                flags=sublime.MONOSPACE_FONT,
                selected_index=pre_selected_idx
                )

    def on_selection(self, remotes_index):
        # User cancelled.
        if remotes_index == -1:
            return
        remote = self.remotes[remotes_index]

        if remote == ALL_REMOTES:
            sublime.set_timeout_async(lambda: self.do_fetch())
        else:
            self.last_remote_used = remote
            sublime.set_timeout_async(lambda: self.do_fetch(remote))

    def do_fetch(self, remote=None):
        sublime.status_message("Starting fetch...")
        self.fetch(remote)
        sublime.status_message("Fetch complete.")
        util.view.refresh_gitsavvy(self.window.active_view())
