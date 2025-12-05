from ...common import util
from GitSavvy.core.base_commands import ask_for_branch, GsWindowCommand
from GitSavvy.core.runtime import on_worker
from GitSavvy.core.ui__quick_panel import show_noop_panel, show_panel


__all__ = (
    "gs_merge",
    "gs_merge_abort",
    "gs_merge_continue",
    "gs_restart_merge_for_file",
)


class gs_merge(GsWindowCommand):

    """
    Display a list of branches available to merge against the active branch.
    When selected, perform merge with specified branch.
    """

    defaults = {
        "branch": ask_for_branch(ignore_current_branch=True, merged=False),
    }

    @on_worker
    def run(self, branch):
        try:
            self.git("merge", branch)
        finally:
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_merge_abort(GsWindowCommand):

    """
    Reset all files to pre-merge conditions, and abort the merge.
    """

    @on_worker
    def run(self):
        self.git("merge", "--abort")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_merge_continue(GsWindowCommand):

    """
    Continue an ongoing merge.  Here for completeness as a user could just commit
    as well.
    """

    @on_worker
    def run(self):
        self.git("merge", "--continue")
        util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)


class gs_restart_merge_for_file(GsWindowCommand):

    """
    Reset a single file to pre-merge condition, but do not abort the merge.
    """

    def run(self):
        paths = self.conflicting_files_()
        if not paths:
            show_noop_panel(
                self.window, "There are no files which have or had merge conflicts."
            )

        def on_done(index):
            fpath = paths[index]
            self.git("checkout", "-m", "--", fpath)

            util.view.refresh_gitsavvy_interfaces(self.window)

        show_panel(self.window, paths, on_done)
