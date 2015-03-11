import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


COMMIT_MSG_PROMPT = "Commit message:"


class GsQuickCommitCommand(WindowCommand, GitCommand):

    """
    Present the user with a input panel where they can enter a commit message.
    Once provided, perform a commit with that message.
    """

    def run(self):
        self.window.show_input_panel(
            COMMIT_MSG_PROMPT,
            "",
            lambda msg: sublime.set_timeout_async(lambda: self.on_done(msg), 0),
            None,
            None
            )

    def on_done(self, commit_message):
        self.git("commit", "-q", "-F", "-", stdin=commit_message)
        sublime.status_message("Committed successfully.")
        util.view.refresh_gitsavvy(self.window.active_view())


class GsQuickStageCurrentFileCommitCommand(WindowCommand, GitCommand):

    """
    Present the user with a input panel where they can enter a commit message.
    Once provided, stage the current file and perform a commit with the
    provided message.
    """

    def run(self):
        self.window.show_input_panel(
            COMMIT_MSG_PROMPT,
            "",
            lambda msg: sublime.set_timeout_async(lambda: self.on_done(msg), 0),
            None,
            None
            )

    def on_done(self, commit_message):
        self.git("add", "--", self.file_path)
        self.git("commit", "-q", "-F", "-", stdin=commit_message)
        sublime.status_message("Committed successfully.")
        util.view.refresh_gitsavvy(self.window.active_view())
