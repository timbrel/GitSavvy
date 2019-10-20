import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..constants import MERGE_CONFLICT_PORCELAIN_STATUSES
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


class GsMergeCommand(WindowCommand, GitCommand):

    """
    Display a list of branches available to merge against the active branch.
    When selected, perform merge with specified branch.
    """

    def run(self):
        sublime.set_timeout_async(lambda: self.run_async(), 1)

    def run_async(self):
        show_branch_panel(
            self.on_branch_selection,
            ignore_current_branch=True)

    def on_branch_selection(self, branch):
        if not branch:
            return
        try:
            self.git(
                "merge",
                "--log" if self.savvy_settings.get("merge_log") else None,
                branch
            )
        finally:
            util.view.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)


class GsAbortMergeCommand(WindowCommand, GitCommand):

    """
    Reset all files to pre-merge conditions, and abort the merge.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.git("reset", "--merge")
        util.view.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)


class GsRestartMergeForFileCommand(WindowCommand, GitCommand):

    """
    Reset a single file to pre-merge condition, but do not abort the merge.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self._conflicts = tuple(
            f.path for f in self.get_status()
            if (f.index_status, f.working_status) in MERGE_CONFLICT_PORCELAIN_STATUSES
        )

        self.window.show_quick_panel(
            self._conflicts,
            self.on_selection
        )

    def on_selection(self, index):
        if index == -1:
            return
        fpath = self._conflicts[index]
        self.git("checkout", "--conflict=merge", "--", fpath)

        util.view.refresh_gitsavvy(self.window.active_view())
