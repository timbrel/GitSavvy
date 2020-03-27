import re

import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand, GitSavvyError
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
NOT_MERGED_WARNING = re.compile(r"The branch.*is not fully merged\.")


class gs_delete_branch(WindowCommand, GitCommand):
    def run(self, branch=None, force=False):
        # type: (Optional[str], bool) -> None
        self.force = force
        if branch:
            self.delete_local_branch(branch)
        else:
            enqueue_on_worker(
                show_branch_panel,
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
        if self.force:
            rv = self.git(
                "branch",
                "-D",
                branch_name
            )
        else:
            try:
                rv = self.git(
                    "branch",
                    "-d",
                    branch_name,
                    throw_on_stderr=True,
                    show_status_message_on_stderr=False,
                    show_panel_on_stderr=False,
                )
            except GitSavvyError as e:
                if NOT_MERGED_WARNING.search(e.stderr):
                    self.offer_force_deletion(branch_name)
                    return
                raise GitSavvyError(
                    e.message,
                    cmd=e.cmd,
                    stdout=e.stdout,
                    stderr=e.stderr,
                    show_panel=True,
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

    def offer_force_deletion(self, branch_name):
        # type: (str) -> None

        actions = [
            "Abort, '{}' is not fully merged.".format(branch_name),
            "Delete anyway."
        ]

        def on_action_selection(index):
            if index < 1:
                return

            self.window.run_command("gs_delete_branch", {
                "branch": branch_name,
                "force": True
            })

        self.window.show_quick_panel(
            actions,
            on_action_selection,
            flags=sublime.MONOSPACE_FONT,
        )
