import sublime
from sublime_plugin import WindowCommand

from .base_command import BaseCommand


COMMIT_MSG_PROMPT = "Commit message:"


class GgQuickCommitCommand(WindowCommand, BaseCommand):

    def run(self):
        self.window.show_input_panel(COMMIT_MSG_PROMPT, "", self.on_done, None, None)

    def on_done(self, commit_message):
        self.git("commit", "-q", "-F", "-", stdin=commit_message)
        sublime.status_message("Committed successfully.")
