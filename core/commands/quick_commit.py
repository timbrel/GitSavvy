from ..base_commands import GsWindowCommand
from ..runtime import on_worker
from ..interfaces import util as interface_util
from ..ui_mixins.input_panel import show_single_line_input_panel


__all__ = (
    "gs_quick_commit",
    "gs_quick_stage_current_file_commit",
)


COMMIT_MSG_PROMPT = "Commit message:"


class gs_quick_commit(GsWindowCommand):

    """
    Present the user with a input panel where they can enter a commit message.
    Once provided, perform a commit with that message.
    """

    def run(self):
        show_single_line_input_panel(
            COMMIT_MSG_PROMPT,
            "",
            self.on_done
        )

    @on_worker
    def on_done(self, commit_message):
        self.window.status_message("Commiting...")
        self.git("commit", "-q", "-F", "-", stdin=commit_message)
        self.window.status_message("Committed successfully.")
        interface_util.refresh_gitsavvy(self.window.active_view())


class gs_quick_stage_current_file_commit(GsWindowCommand):

    """
    Present the user with a input panel where they can enter a commit message.
    Once provided, stage the current file and perform a commit with the
    provided message.
    """

    def run(self):
        show_single_line_input_panel(
            COMMIT_MSG_PROMPT,
            "",
            self.on_done
        )

    @on_worker
    def on_done(self, commit_message):
        self.window.status_message("Commiting...")
        self.git("add", "--", self.file_path)
        self.git("commit", "-q", "-F", "-", stdin=commit_message)
        self.window.status_message("Committed successfully.")
        interface_util.refresh_gitsavvy(self.window.active_view())
