import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


__all__ = (
    "gs_merge",
    "gs_abort_merge",
    "gs_restart_merge_for_file",
)


class gs_merge(WindowCommand, GitCommand):

    """
    Display a list of branches available to merge against the active branch.
    When selected, perform merge with specified branch.
    """

    def run(self):
        sublime.set_timeout_async(lambda: self.run_async(), 1)

    def run_async(self):
        show_branch_panel(
            self.on_branch_selection,
            ignore_current_branch=True
        )

    def on_branch_selection(self, branch):
        try:
            self.git(
                "merge",
                "--log" if self.savvy_settings.get("merge_log") else None,
                branch
            )
        finally:
            util.view.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)


class gs_abort_merge(WindowCommand, GitCommand):

    """
    Reset all files to pre-merge conditions, and abort the merge.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self.git("reset", "--merge")
        util.view.refresh_gitsavvy(self.window.active_view(), refresh_sidebar=True)


class gs_restart_merge_for_file(WindowCommand, GitCommand):

    """
    Reset a single file to pre-merge condition, but do not abort the merge.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        self._conflicts = [
            f.path for f in self.get_working_dir_status().merge_conflicts
        ]

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
