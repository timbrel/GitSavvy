from .log import LogMixin
from .reflog import RefLogMixin
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel
from ..ui__quick_panel import show_panel
from GitSavvy.core.base_commands import GsWindowCommand


__all__ = (
    "gs_reset",
    "gs_reset_branch",
    "gs_reset_reflog",
)


GIT_RESET_MODES = [
    # See analysis at
    # http://stackoverflow.com/questions/34149356/what-exactly-is-the-difference-between-all-the-git-reset-modes/34155307#34155307
    ["--soft", "stage   differences (safe)"],
    ["--mixed", "unstage differences (safe)"],
    ["--hard", "discard uncommitted changes, overwrite working dir (unsafe)"],
    ["--keep", "keep    uncommitted changes, update working dir    (safe)"],
    ["--merge", "discard staged, keep unstaged, update working (abort if unsafe)"]
]
MODES = [mode for mode, _ in GIT_RESET_MODES]


class ResetMixin(GsWindowCommand):

    def do_action(self, commit_hash, **kwargs):
        if not commit_hash:
            return
        self._selected_hash = commit_hash

        use_reset_mode = self.savvy_settings.get("use_reset_mode")
        last_reset_mode_used = \
            self.current_state().get("last_reset_mode_used", use_reset_mode)
        reset_modes = (
            GIT_RESET_MODES
            + (
                [[use_reset_mode, ""]]
                if use_reset_mode and use_reset_mode not in MODES
                else []
            )
        )
        try:
            selected_index = (
                [m for m, _ in reset_modes]
                .index(last_reset_mode_used)
            )
        except ValueError:
            selected_index = -1

        def on_done(index):
            self.on_reset(reset_modes[index][0].strip())

        show_panel(
            self.window,
            reset_modes,
            on_done,
            selected_index=selected_index
        )

    def on_reset(self, reset_mode):
        # Split the reset mode to support multiple args, e.g. "--mixed -N"
        args = reset_mode.split() + [self._selected_hash]

        def do_reset():
            self.update_store({"last_reset_mode_used": reset_mode})
            self.git("reset", *args)
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

        if reset_mode == "--hard":
            util.actions.destructive("perform a hard reset")(do_reset)()
        else:
            do_reset()


class gs_reset(ResetMixin, LogMixin):
    pass


class gs_reset_branch(ResetMixin):
    def run(self, **kwargs):
        show_branch_panel(self.on_branch_selection)

    def on_branch_selection(self, branch):
        self.do_action(branch)


class gs_reset_reflog(ResetMixin, RefLogMixin):
    pass
