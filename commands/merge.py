import sublime
from sublime_plugin import WindowCommand

from .base_command import BaseCommand
# from ..common import util


class GsMergeCommand(WindowCommand, BaseCommand):

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
        branch_name = self._branches[index]
        self.git("merge", "--log", branch_name)
