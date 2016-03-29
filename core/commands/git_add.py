"""
Implements a special view that displays an editable diff of unstaged changes.
"""

import os
import sys

import sublime
from sublime_plugin import WindowCommand, TextCommand, EventListener

from ..git_command import GitCommand, GitSavvyError
from ...common import util


TITLE = "GIT-ADD: {}"


class GsAddEditCommand(WindowCommand, GitCommand):

    """
    Create a new view to display the project's unstaged changes.
    """

    def run(self, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(**kwargs), 0)

    def run_async(self):
        git_add_view = util.view.get_scratch_view(self, "git_add", read_only=False)
        git_add_view.set_name(TITLE.format(os.path.basename(self.repo_path)))
        git_add_view.set_syntax_file("Packages/GitSavvy/syntax/diff.sublime_syntax")
        git_add_view.settings().set("git_savvy.repo_path", self.repo_path)
        git_add_view.settings().set("translate_tabs_to_spaces", False)

        self.window.focus_view(git_add_view)
        git_add_view.sel().clear()
        git_add_view.run_command("gs_add_edit_refresh")

        super_key = "SUPER" if sys.platform == "darwin" else "CTRL"
        message = "Press {}-Enter to apply the diff.  Close the window to cancel.".format(super_key)
        sublime.message_dialog(message)


class GsAddEditRefreshCommand(TextCommand, GitCommand):

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

        self.view.run_command("gs_replace_view_text", {"text": stdout})


class GsAddEditCommitCommand(TextCommand, GitCommand):

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
