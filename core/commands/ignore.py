import os

import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


IGNORE_PATTERN_PROMPT = "Enter pattern to ignore:"
UNSTAGED_WORKING_STATUSES = ("M", "D")


class GsIgnoreCommand(WindowCommand, GitCommand):

    """
    Add a `.gitignore` entry for the provided relative path or pattern
    at the Git repo's root.
    """

    def run(self, file_path_or_pattern):
        if not file_path_or_pattern:
            file_path_or_pattern = self.file_path_or_pattern

        self.add_ignore(os.path.join("/", file_path_or_pattern))
        sublime.status_message("Ignored file `{}`.".format(file_path_or_pattern))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsIgnorePatternCommand(WindowCommand, GitCommand):

    """
    Prompt the user for an ignore pattern and, once entered, create
    a corresponding `.gitignore` entry at the Git repo's root.
    """

    def run(self, pre_filled=None):
        self.window.show_input_panel(IGNORE_PATTERN_PROMPT, pre_filled or "", self.on_done, None, None)

    def on_done(self, ignore_pattern):
        self.add_ignore(ignore_pattern)
        sublime.status_message("Ignored pattern `{}`.".format(ignore_pattern))
        util.view.refresh_gitsavvy(self.window.active_view())


class GsAssumeUnchangedCommand(WindowCommand, GitCommand):

    """
    Prompt the user with a quick panel of unstaged files.  After the selection
    is made, temporarily treat selected file as unchanged.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self._unstaged_files = tuple(
            f.path for f in self.get_status()
            if f.working_status in UNSTAGED_WORKING_STATUSES
            )

        self.window.show_quick_panel(
            self._unstaged_files,
            self.on_selection,
            flags=sublime.MONOSPACE_FONT
        )

    def on_selection(self, index):
        if index == -1:
            return
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

        self._ignored_files = tuple(f[1] for f in all_file_lines if f[0] == "h")

        if not self._ignored_files:
            self.window.show_quick_panel(["No files are assumed unchanged."], None)
        else:
            self.window.show_quick_panel(
                self._ignored_files,
                self.on_selection,
                flags=sublime.MONOSPACE_FONT
            )

    def on_selection(self, index):
        if index == -1:
            return

        fpath = self._ignored_files[index]
        self.git("update-index", "--no-assume-unchanged", fpath)

        util.view.refresh_gitsavvy(self.window.active_view())

        self.window.run_command("gs_restore_assumed_unchanged")
