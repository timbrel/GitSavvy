"""
Implements a special view that displays an editable diff of unstaged changes.
"""

import os
import sys

import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand, GitSavvyError
from ..view import replace_view_content
from ...common import util


TITLE = "STAGE-DIFF: {}"


class GsStageDiffCommand(WindowCommand, GitCommand):

    """
    Create a new view to display the project's unstaged changes.
    """

    def run(self, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs), 0)

    def run_async(self):
        stage_diff_view = util.view.get_scratch_view(self, "git_stage_diff", read_only=False)
        stage_diff_view.set_name(TITLE.format(os.path.basename(self.repo_path)))
        stage_diff_view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime_syntax")
        stage_diff_view.settings().set("git_savvy.repo_path", self.repo_path)

        self.window.focus_view(stage_diff_view)
        stage_diff_view.sel().clear()
        stage_diff_view.run_command("gs_stage_diff_refresh")


class GsStageDiffRefreshCommand(TextCommand, GitCommand):

    """
    Refresh the view with the latest unstaged changes.
    """

    def run(self, edit, cursors=None):
        if self.view.settings().get("git_savvy.disable_diff"):
            return

        try:
            stdout = self.git("diff", "--no-color")
        except GitSavvyError as err:
            # When the output of the above Git command fails to correctly parse,
            # the expected notification will be displayed to the user.  However,
            # once the userpresses OK, a new refresh event will be triggered on
            # the view.
            #
            # This causes an infinite loop of increasingly frustrating error
            # messages, ultimately resulting in psychosis and serious medical
            # bills.  This is a better, though somewhat cludgy, alternative.
            #
            if err.args and type(err.args[0]) == UnicodeDecodeError:
                self.view.settings().set("git_savvy.disable_diff", True)
                return
            raise err

        super_key = "SUPER" if sys.platform == "darwin" else "CTRL"
        message = "Press {}-Enter to apply the diff.  Close the window to cancel.".format(super_key)
        content = message + "\n\n" + stdout
        replace_view_content(self.view, content)


class GsStageDiffApplyCommand(TextCommand, GitCommand):

    """
    Apply the commit as it is presented in the view to the index. Then close the view.
    """

    def run(self, edit):
        sublime.set_timeout_async(lambda: self.run_async(), 0)

    def run_async(self):
        diff_content = self.view.substr(sublime.Region(0, self.view.size()))
        self.git("apply", "--cached", "-", stdin=diff_content)
        self.view.window().focus_view(self.view)
        self.view.window().run_command("close_file")
