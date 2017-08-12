import sublime
from sublime_plugin import TextCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel


class GsRemoteAddCommand(TextCommand, GitCommand):
    """
    Add remotes
    """

    def run(self, edit):
        # Get remote name from user
        self.view.window().show_input_panel("Remote URL", "", self.on_enter_remote, None, None)

    def on_enter_remote(self, input_url):
        self.url = input_url
        owner = self.username_from_url(input_url)

        self.view.window().show_input_panel("Remote name", owner, self.on_enter_name, None, None)

    def on_enter_name(self, remote_name):
        self.git("remote", "add", remote_name, self.url)
        if sublime.ok_cancel_dialog("Your remote was added successfully.  Would you like to fetch from this remote?"):
            self.view.window().run_command("gs_fetch", {"remote": remote_name})


class GsRemoteRemoveCommand(TextCommand, GitCommand):
    """
    Remove remotes
    """

    def run(self, edit):
        show_remote_panel(self.on_remote_selection)

    def on_remote_selection(self, remote):
        if not remote:
            return

        @util.actions.destructive(description="remove a remote")
        def remove():
            self.git("remote", "remove", remote)

        remove()
