import sublime
from sublime_plugin import WindowCommand

from ...core.git_command import GitCommand


NO_REMOTES_MESSAGE = "You have not configured any remotes."


class GsConfigureGithubRemoteCommand(WindowCommand, GitCommand):

    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        """
        Display a panel of all remotes defined for the repo, then proceed to
        `on_select_remote`.  If no remotes are defined, notify the user and
        proceed no further.
        """
        self.remotes = list(self.get_remotes().keys())
        self.remote_branches = self.get_remote_branches()

        if not self.remotes:
            self.window.show_quick_panel([NO_REMOTES_MESSAGE], None)
        else:
            self.window.show_quick_panel(
                self.remotes,
                self.on_select_remote,
                flags=sublime.MONOSPACE_FONT
                )

    def on_select_remote(self, remote_index):
        """
        After the user selects a remote, display a panel of branches that are
        present on that remote, then proceed to `on_select_branch`.
        """
        # If the user pressed `esc` or otherwise cancelled.
        if remote_index == -1:
            return

        selected_remote = self.remotes[remote_index]
        self.git("config", "--local", "--unset-all", "GitSavvy.ghRemote", throw_on_stderr=False)
        self.git("config", "--local", "--add", "GitSavvy.ghRemote", selected_remote)
        sublime.status_message("Successfully configured GitHub integration.")
