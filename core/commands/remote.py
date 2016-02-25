import re
import sublime
from sublime_plugin import TextCommand

from ..git_command import GitCommand
from ...common import util


class GsRemoteAddCommand(TextCommand, GitCommand):
    """
    Add remotes
    """

    def run(self, edit):
        # Get remote name from user
        self.view.window().show_input_panel("Remote URL", "", self.on_select_remote, None, None)

    def on_select_remote(self, input_url):
        self.url = input_url
        # URLs can come in one of following formats format
        # https://github.com/divmain/GitSavvy.git
        #     git@github.com:divmain/GitSavvy.git
        # Kind of funky, but does the job
        _split_url = re.split('/|:', input_url)
        owner = _split_url[-2] if len(_split_url) >= 2 else ''

        self.view.window().show_input_panel("Remote name", owner, self.on_select_name, None, None)

    def on_select_name(self, owner):
        self.git("remote", "add", owner, self.url)


class GsRemoteRemoveCommand(TextCommand, GitCommand):
    """
    Remove remotes
    """

    def run(self, edit):
        self.remotes = list(self.get_remotes().keys())

        if not self.remotes:
            self.view.window().show_quick_panel(["There are no remotes available."], None)
        else:
            self.view.window().show_quick_panel(
                self.remotes,
                self.on_selection,
                flags=sublime.MONOSPACE_FONT,
                selected_index=0
                )

    def on_selection(self, remotes_index):
        if remotes_index == -1:
            return

        @util.actions.destructive(description="remove a remote")
        def remove():
            self.git("remote", "remove", remote)

        remote = self.remotes[remotes_index]
        remove()
