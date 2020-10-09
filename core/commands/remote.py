import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_remote_panel
from ..ui_mixins.input_panel import show_single_line_input_panel


class GsRemoteAddCommand(WindowCommand, GitCommand):
    """
    Add remotes
    """

    def run(self, url=None):
        # Get remote name from user
        if url:
            self.on_enter_remote(url)
        else:
            show_single_line_input_panel("Remote URL", "", self.on_enter_remote, None, None)

    def on_enter_remote(self, input_url):
        self.url = input_url
        owner = self.username_from_url(input_url)

        show_single_line_input_panel("Remote name", owner, self.on_enter_name, None, None)

    def on_enter_name(self, remote_name):
        self.git("remote", "add", remote_name, self.url)
        if sublime.ok_cancel_dialog("Your remote was added successfully.  Would you like to fetch from this remote?"):
            self.window.run_command("gs_fetch", {"remote": remote_name})


class GsRemoteRemoveCommand(WindowCommand, GitCommand):
    """
    Remove remotes
    """

    def run(self):
        show_remote_panel(self.on_remote_selection, show_url=True)

    def on_remote_selection(self, remote):
        if not remote:
            return

        @util.actions.destructive(description="remove a remote")
        def remove():
            self.git("remote", "remove", remote)
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_status_bar=False)

        remove()


class GsRemoteRenameCommand(WindowCommand, GitCommand):
    """
    Reame remotes
    """

    def run(self):
        show_remote_panel(self.on_remote_selection, show_url=True)

    def on_remote_selection(self, remote):
        if not remote:
            return

        self.remote = remote
        show_single_line_input_panel("Remote name", remote, self.on_enter_name, None, None)

    def on_enter_name(self, new_name):
        self.git("remote", "rename", self.remote, new_name)
        self.window.status_message("remote {} was renamed as {}.".format(self.remote, new_name))
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_status_bar=False)
