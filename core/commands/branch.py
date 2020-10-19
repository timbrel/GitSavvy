import re

from ..git_command import GitCommand, GitSavvyError
from ...common import util
from GitSavvy.core.base_commands import ask_for_local_branch, GsWindowCommand
from GitSavvy.core.utils import noop, show_actions_panel


__all__ = (
    "gs_delete_branch",
)


DELETE_UNDO_MESSAGE = """\
GitSavvy: Deleted branch ({0}), in case you want to undo, run:
  $ git branch {0} {1}
"""
EXTRACT_COMMIT = re.compile(r"\(was (.+)\)")
NOT_MERGED_WARNING = re.compile(r"The branch.*is not fully merged\.")


class gs_delete_branch(GsWindowCommand, GitCommand):
    defaults = {
        "branch": ask_for_local_branch,
    }

    @util.actions.destructive(description="delete a local branch")
    def run(self, branch, force=False):
        # type: (str, bool) -> None
        if force:
            rv = self.git("branch", "-D", branch)
        else:
            try:
                rv = self.git_throwing_silently("branch", "-d", branch)
            except GitSavvyError as e:
                if NOT_MERGED_WARNING.search(e.stderr):
                    self.offer_force_deletion(branch)
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
            print(DELETE_UNDO_MESSAGE.format(branch, commit))
        self.window.status_message(
            "Deleted local branch ({}).".format(branch)
            + (" Open Sublime console for undo instructions." if match else "")
        )
        util.view.refresh_gitsavvy_interfaces(self.window)

    def offer_force_deletion(self, branch_name):
        # type: (str) -> None
        show_actions_panel(self.window, [
            noop("Abort, '{}' is not fully merged.".format(branch_name)),
            (
                "Delete anyway.",
                lambda: self.window.run_command("gs_delete_branch", {
                    "branch": branch_name,
                    "force": True
                })
            )
        ])
