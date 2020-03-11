import sublime
from sublime_plugin import WindowCommand
from ..git_command import GitCommand
from .log import LogMixin
from .reflog import RefLogMixin
from ...common import util
from ..ui_mixins.quick_panel import show_branch_panel


PADDING = "                                                "
GIT_RESET_MODES = [
    # See analysis at
    # http://stackoverflow.com/questions/34149356/what-exactly-is-the-difference-between-all-the-git-reset-modes/34155307#34155307
    ["--mixed" + PADDING, "unstage staged, keep unstaged, don't touch working (safe)"],
    ["--soft", "just move HEAD, stage differences (safe)"],
    ["--hard", "discard staged, discard unstaged, update working (unsafe)"],
    ["--merge", "discard staged, keep unstaged, update working (abort if unsafe)"],
    ["--keep", "unstage staged, keep unstaged, update working (abort if unsafe)"]
    # For reference, in case we ever include the (very similar) checkout command
    # ["--checkout", "keep staged, keep unstaged, update working, move branches (abort if unsafe)"]
]


class ResetMixin(object):

    def do_action(self, commit_hash, **kwargs):
        if not commit_hash:
            return
        self._selected_hash = commit_hash

        use_reset_mode = self.savvy_settings.get("use_reset_mode")
        if use_reset_mode:
            self.on_reset(use_reset_mode)
        else:
            self.window.show_quick_panel(
                GIT_RESET_MODES,
                self.on_reset_mode_selection,
                flags=sublime.MONOSPACE_FONT
            )

    def on_reset_mode_selection(self, index):
        if index == -1:
            return
            sublime.set_timeout_async(self.run_async, 100)
        self.on_reset(GIT_RESET_MODES[index][0].strip())

    def on_reset(self, reset_mode):
        # Split the reset mode to support multiple args, e.g. "--mixed -N"
        args = reset_mode.split() + [self._selected_hash]

        def do_reset():
            self.git("reset", *args)
            util.view.refresh_gitsavvy_interfaces(self.window, refresh_sidebar=True)

        if reset_mode == "--hard":
            util.actions.destructive("perform a hard reset")(do_reset)()
        else:
            do_reset()


class GsResetCommand(ResetMixin, LogMixin, WindowCommand, GitCommand):

    pass


class GsResetBranch(ResetMixin, LogMixin, WindowCommand, GitCommand):

    def run_async(self, **kwargs):
        show_branch_panel(self.on_branch_selection)

    def on_branch_selection(self, branch):
        if branch:
            self.do_action(branch)


class GsResetReflogCommand(ResetMixin, RefLogMixin, WindowCommand, GitCommand):

    pass
