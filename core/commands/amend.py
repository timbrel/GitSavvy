import os

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand


class GsAmendCommand(WindowCommand, GitCommand):
    def run(self):
        self.window.run_command("gs_commit", {"amend": True})


class GsQuickStageCurrentFileAndAmendCommand(GsAmendCommand, GitCommand):
    def run(self):
        self.git("add", "--", self.file_path)
        sublime.status_message("staged {}".format(self.get_rel_path(self.file_path)))
        super().run()
