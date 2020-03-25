import re

from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..runtime import enqueue_on_worker
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


__all__ = (
    "gs_delete_branch",
)

MYPY = False
if MYPY:
    from typing import Optional


DELETE_UNDO_MESSAGE = """\
GitSavvy: Deleted branch ({0}), in case you want to undo, run:
  $ git branch {0} {1}
"""
EXTRACT_COMMIT = re.compile(r"\(was (.+)\)")


class gs_delete_branch(WindowCommand, GitCommand):
    def run(self, branch=None, force=False):
        # type: (Optional[str], bool) -> None
        self.force = force
        enqueue_on_worker(self.run_impl, branch)

    def run_impl(self, branch):
        # type: (Optional[str]) -> None
        if branch:
            self.on_branch_selection(branch)
        else:
            show_branch_panel(
                self.on_branch_selection,
                local_branches_only=True,
                ignore_current_branch=True,
            )

    def on_branch_selection(self, branch):
        # type: (Optional[str]) -> None
        if not branch:
            return

        self.delete_local_branch(branch)

    @util.actions.destructive(description="delete a local branch")
    def delete_local_branch(self, branch_name):
        # type: (str) -> None
        rv = self.git(
            "branch",
            "-D" if self.force else "-d",
            branch_name
        )
        match = EXTRACT_COMMIT.search(rv.strip())
        if match:
            commit = match.group(1)
            print(DELETE_UNDO_MESSAGE.format(branch_name, commit))
        self.window.status_message(
            "Deleted local branch ({}).".format(branch_name)
            + (" Open Sublime console for undo instructions." if match else "")
        )
        util.view.refresh_gitsavvy_interfaces(self.window)
