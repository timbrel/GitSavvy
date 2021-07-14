import sublime
from sublime_plugin import WindowCommand
from ..git_command import GitCommand
from .log import LogMixin
from .reflog import RefLogMixin
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel
from GitSavvy.core import store


__all__ = (
    "gs_reset",
    "gs_reset_branch",
    "gs_reset_reflog",
)


GIT_RESET_MODES = [
    # See analysis at
    # http://stackoverflow.com/questions/34149356/what-exactly-is-the-difference-between-all-the-git-reset-modes/34155307#34155307
    ["--mixed", "unstage staged, keep unstaged, don't touch working (safe)"],
    ["--soft", "just move HEAD, stage differences (safe)"],
    ["--hard", "discard staged, discard unstaged, update working (unsafe)"],
    ["--merge", "discard staged, keep unstaged, update working (abort if unsafe)"],
    ["--keep", "unstage staged, keep unstaged, update working (abort if unsafe)"]
    # For reference, in case we ever include the (very similar) checkout command
    # ["--checkout", "keep staged, keep unstaged, update working, move branches (abort if unsafe)"]
]
MODES = [mode for mode, _ in GIT_RESET_MODES]


class ResetMixin(GitCommand, WindowCommand):

    def do_action(self, commit_hash, **kwargs):
        if not commit_hash:
            return
        self._selected_hash = commit_hash

        use_reset_mode = self.savvy_settings.get("use_reset_mode")
        last_reset_mode_used = (
            store.current_state(self.repo_path)
                 .get("last_reset_mode_used", use_reset_mode)
        )
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
                .index(last_reset_mode_used)  # type: ignore[arg-type]
            )
        except ValueError:
            selected_index = -1

        def on_done(index):
            if index == -1:
                return
            self.on_reset(reset_modes[index][0].strip())

        self.window.show_quick_panel(
            reset_modes,
            on_done,
            flags=sublime.MONOSPACE_FONT,
            selected_index=selected_index
        )

    def on_reset(self, reset_mode):
        # Split the reset mode to support multiple args, e.g. "--mixed -N"
        args = reset_mode.split() + [self._selected_hash]

        def do_reset():
            store.update_state(self.repo_path, {"last_reset_mode_used": reset_mode})
            self.git("reset", *args)
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

        if reset_mode == "--hard":
            util.actions.destructive("perform a hard reset")(do_reset)()
        else:
            do_reset()


class gs_reset(ResetMixin, LogMixin, WindowCommand):
    pass


class gs_reset_branch(ResetMixin, WindowCommand):
    def run(self, **kwargs):
        show_branch_panel(self.on_branch_selection)

    def on_branch_selection(self, branch):
        self.do_action(branch)


class gs_reset_reflog(ResetMixin, RefLogMixin, WindowCommand):
    pass
