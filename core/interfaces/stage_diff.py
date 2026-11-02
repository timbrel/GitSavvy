"""
Implements a special view that displays an editable diff of unstaged changes.
"""

import os

import sublime


from GitSavvy.core.base_commands import GsTextCommand, GsWindowCommand
from ..view import replace_view_content
from ...common import util


__all__ = (
    "gs_stage_diff",
    "gs_stage_diff_apply",
)


TITLE = "STAGE-DIFF: {}"
MESSAGE = "Press {}-Enter to apply the diff.  Close the window to cancel.".format(util.super_key)


class gs_stage_diff(GsWindowCommand):

    """
    Create a new view to display the project's unstaged changes.
    """

    def run(self):
        repo_path = self.repo_path
        stage_diff_view = util.view.create_scratch_view(self.window, "git_stage_diff", {
            "syntax": "Packages/GitSavvy/syntax/diff.sublime_syntax",
            "title": TITLE.format(os.path.basename(repo_path)),
            "read_only": False,
            "git_savvy.repo_path": repo_path,
        })

        stdout = self.git("diff", "--no-color")
        content = MESSAGE + "\n\n" + stdout
        replace_view_content(stage_diff_view, content)


class gs_stage_diff_apply(GsTextCommand):

    """
    Apply the commit as it is presented in the view to the index. Then close the view.
    """

    def run(self, edit):
        diff_content = self.view.substr(sublime.Region(0, self.view.size()))
        self.git("apply", "--cached", "-", stdin=diff_content)
        self.view.close()
