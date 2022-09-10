from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel
from GitSavvy.core.base_commands import GsWindowCommand
from GitSavvy.core.utils import show_panel
from GitSavvy.core.runtime import enqueue_on_worker


__all__ = (
    "gs_merge",
    "gs_abort_merge",
    "gs_restart_merge_for_file",
)


class gs_merge(GsWindowCommand):

    """
    Display a list of branches available to merge against the active branch.
    When selected, perform merge with specified branch.
    """

    def run(self):
        enqueue_on_worker(self.run_async)

    def run_async(self):
        show_branch_panel(
            self.on_branch_selection,
            ignore_current_branch=True
        )

    def on_branch_selection(self, branch):
        try:
            self.git("merge", branch)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_abort_merge(GsWindowCommand):

    """
    Reset all files to pre-merge conditions, and abort the merge.
    """

    def run(self):
        enqueue_on_worker(self.run_async)

    def run_async(self):
        self.git("merge", "--abort")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_restart_merge_for_file(GsWindowCommand):

    """
    Reset a single file to pre-merge condition, but do not abort the merge.
    """

    def run(self):
        paths = self.conflicting_files_()

        def on_done(index):
            fpath = paths[index]
            self.git("checkout", "-m", "--", fpath)

            util.view.refresh_gitsavvy_interfaces(self.window)

        show_panel(self.window, paths, on_done)
