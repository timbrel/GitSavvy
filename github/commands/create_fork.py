import sublime
from sublime_plugin import WindowCommand

from ...common import util
from ...core.git_command import GitCommand
from ...core.ui_mixins.quick_panel import PanelCommandMixin
from .. import github, git_mixins

START_CREATE_MESSAGE = "Creating fork of {repo} ..."
END_CREATE_MESSAGE = "Fork created successfully."


__all__ = ['GsCreateForkCommand']


class GsCreateForkCommand(PanelCommandMixin, WindowCommand, GitCommand,
                          git_mixins.GithubRemotesMixin):
    """
    Get list of repos on GitHub associated with the active repo.  Display, and when
    selected, add selection as git remote.
    """
    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        base_remote = github.parse_remote(self.get_integrated_remote_url())
        self.window.status_message(START_CREATE_MESSAGE.format(repo=base_remote))
        result = github.create_fork(base_remote)
        self.window.status_message(END_CREATE_MESSAGE)
        util.debug.add_to_log(("github: fork result:\n{}".format(result)))
        self.window.show_quick_panel(
            ["Fork created, do you want to add it as remote?",
             "Yes",
             "No"],
            self.on_select_action
            )

    def on_select_action(self, idx):
        if idx != 1:
            return

        self.window.run_command("gs_add_fork_as_remote",)
