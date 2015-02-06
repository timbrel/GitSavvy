import os

import sublime
from sublime_plugin import WindowCommand

from .base_command import BaseCommand


IGNORE_PATTERN_PROMPT = "Enter pattern to ignore:"


class GsIgnoreCommand(WindowCommand, BaseCommand):

    """
    Add a `.gitignore` entry for the provided relative path or pattern
    at the Git repo's root.
    """

    def run(self, file_path_or_pattern):
        if not file_path_or_pattern:
            file_path_or_pattern = self.file_path_or_pattern

        self.add_ignore(os.path.join("/", file_path_or_pattern))
        sublime.status_message("Ignored file `{}`.".format(file_path_or_pattern))


class GsIgnorePatternCommand(WindowCommand, BaseCommand):

    """
    Prompt the user for an ignore pattern and, once entered, create
    a corresponding `.gitignore` entry at the Git repo's root.
    """

    def run(self, pre_filled=None):
        self.window.show_input_panel(IGNORE_PATTERN_PROMPT, pre_filled or "", self.on_done, None, None)

    def on_done(self, ignore_pattern):
        self.add_ignore(ignore_pattern)
        sublime.status_message("Ignored pattern `{}`.".format(ignore_pattern))
        if self.window.active_view().settings().get("git_savvy.status_view"):
            self.window.active_view().run_command("gs_status_refresh")
