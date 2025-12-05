import os

import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.input_panel import show_single_line_input_panel
from ..ui__quick_panel import show_noop_panel, show_panel


IGNORE_PATTERN_PROMPT = "Enter pattern to ignore:"


class GsIgnoreCommand(WindowCommand, GitCommand):

    """
    Add a `.gitignore` entry for the provided relative path or pattern
    at the Git repo's root.
    """

    def run(self, file_path_or_pattern=None):
        if not file_path_or_pattern:
            file_path_or_pattern = self.file_path

        self.add_ignore(os.path.join("/", file_path_or_pattern))
        self.window.status_message("Ignored file `{}`.".format(file_path_or_pattern))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsIgnorePatternCommand(WindowCommand, GitCommand):

    """
    Prompt the user for an ignore pattern and, once entered, create
    a corresponding `.gitignore` entry at the Git repo's root.
    """

    def run(self, pre_filled=None):
        show_single_line_input_panel(IGNORE_PATTERN_PROMPT, pre_filled or "", self.on_done)

    def on_done(self, ignore_pattern):
        self.add_ignore(ignore_pattern)
        self.window.status_message("Ignored pattern `{}`.".format(ignore_pattern))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsAssumeUnchangedCommand(WindowCommand, GitCommand):

    """
    Prompt the user with a quick panel of unstaged files.  After the selection
    is made, temporarily treat selected file as unchanged.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self._unstaged_files = [
            f.path for f in self.get_working_dir_status().unstaged_files
        ]

        show_panel(self.window, self._unstaged_files, self.on_selection)

    def on_selection(self, index):
        fpath = self._unstaged_files[index]
        self.git("update-index", "--assume-unchanged", fpath)

        util.view.refresh_gitsavvy(self.window.active_view())


class GsRestoreAssumedUnchangedCommand(WindowCommand, GitCommand):

    """
    Show the user a quick panel of previously temporarily-ignored files.  When
    the selection is made, remove the temporarily-ignored behavior for that file,
    and display the panel again.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        all_file_lines = (
            line.split(" ", 1)
            for line in self.git("ls-files", "-v").split("\n")
        )

        self._ignored_files = [f[1] for f in all_file_lines if f[0] == "h"]

        if not self._ignored_files:
            show_noop_panel(self.window, "No files are assumed unchanged.")
        else:
            show_panel(self.window, self._ignored_files, self.on_selection)

    def on_selection(self, index):
        fpath = self._ignored_files[index]
        self.git("update-index", "--no-assume-unchanged", fpath)

        util.view.refresh_gitsavvy(self.window.active_view())

        self.window.run_command("gs_restore_assumed_unchanged")
