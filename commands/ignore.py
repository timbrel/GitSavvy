import os

import sublime
from sublime_plugin import WindowCommand

from .base_command import BaseCommand


IGNORE_PATTERN_PROMPT = "Enter pattern to ignore:"


class GsIgnoreCommand(WindowCommand, BaseCommand):

    def run(self, file_path):
        if not file_path:
            file_path = self.file_path

        self.add_ignore(os.path.join("/", file_path))
        sublime.status_message("Ignored file `{}`.".format(file_path))


class GsIgnorePatternCommand(WindowCommand, BaseCommand):

    def run(self, pre_filled=None):
        self.window.show_input_panel(IGNORE_PATTERN_PROMPT, pre_filled or "", self.on_done, None, None)

    def on_done(self, ignore_pattern):
        self.add_ignore(ignore_pattern)
        sublime.status_message("Ignored pattern `{}`.".format(ignore_pattern))
        if self.window.active_view().settings().get("git_savvy.status_view"):
            self.window.active_view().run_command("gs_status_refresh")
