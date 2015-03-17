import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..constants import MERGE_CONFLICT_PORCELAIN_STATUSES
from ...common import util


class GsMergeCommand(WindowCommand, GitCommand):

    """
    Display a list of branches available to merge against the active branch.
    When selected, perform merge with specified branch.
    """

    def run(self):
        sublime.set_timeout_async(lambda: self.run_async(), 1)

    def run_async(self):
        self._branches = tuple(branch for branch in self.get_branches() if not branch.active)
        self._entries = tuple(self._generate_entry(branch) for branch in self._branches)

        self.window.show_quick_panel(
            self._entries,
            self.on_selection
        )

    @staticmethod
    def _generate_entry(branch):
        entry = branch.name_with_remote
        addl_info = []

        if branch.tracking:
            addl_info.append("tracking remote " + branch.tracking)

        if branch.tracking_status:
            addl_info.append(branch.tracking_status)

        if addl_info:
            entry += "(" + " - ".join(addl_info) + ")"

        return entry

    def on_selection(self, index):
        if index == -1:
            return
        branch = self._branches[index]
        try:
            self.git("merge", "--log", branch.name_with_remote)
        finally:
            util.view.refresh_gitsavvy(self.window.active_view())


class GsAbortMergeCommand(WindowCommand, GitCommand):

    """
    Reset all files to pre-merge conditions, and abort the merge.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.git("reset", "--merge")
        util.view.refresh_gitsavvy(self.window.active_view())


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
