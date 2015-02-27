import sublime
from sublime_plugin import WindowCommand

from ..base_command import BaseCommand


COMMIT_MSG_PROMPT = "Commit message:"


class GsQuickCommitCommand(WindowCommand, BaseCommand):

    """
    Present the user with a input panel where they can enter a commit message.
    Once provided, perform a commit with that message.
    """

    def run(self):
        self.window.show_input_panel(COMMIT_MSG_PROMPT, "", self.on_done, None, None)

    def on_done(self, commit_message):
        self.git("commit", "-q", "-F", "-", stdin=commit_message)
        sublime.status_message("Committed successfully.")


class GsQuickStageCurrentFileCommitCommand(WindowCommand, BaseCommand):

    """
    Present the user with a input panel where they can enter a commit message.
    Once provided, stage the current file and perform a commit with the
    provided message.
    """

    def run(self):
        self.window.show_input_panel(COMMIT_MSG_PROMPT, "", self.on_done, None, None)

    def on_done(self, commit_message):
        self.git("add", "--", self.file_path)
        self.git("commit", "-q", "-F", "-", stdin=commit_message)
        sublime.status_message("Committed successfully.")
