import sublime

from .log import GsLogBase
from ...common import util

PADDING = "                                                "
GIT_RESET_MODES = [
    # See analysis at http://stackoverflow.com/questions/34149356/what-exactly-is-the-difference-between-all-the-git-reset-modes/34155307#34155307
    ["--mixed" + PADDING, "unstage staged, keep unstaged, don't touch working (safe)"],
    ["--soft", "just move HEAD, stage differences (safe)"],
    ["--hard", "discard staged, discard unstaged, update working (unsafe)"],
    ["--merge", "discard staged, keep unstaged, update working (abort if unsafe)"],
    ["--keep", "unstage staged, keep unstaged, update working (abort if unsafe)"]
    # For reference, in case we ever include the (very similar) checkout command
    # ["--checkout", "keep staged, keep unstaged, update working, move branches (abort if unsafe)"]
]


class GsResetCommand(GsLogBase):

    def run(self, commit_hash=None):
        if commit_hash:
            self.do_action(commit_hash)
        else:
            super().run()

    def do_action(self, commit_hash):
        self._selected_hash = commit_hash

        use_reset_mode = sublime.load_settings("GitSavvy.sublime-settings").get("use_reset_mode")
        if use_reset_mode:
            self.on_reset(use_reset_mode)
        else:
            self.window.show_quick_panel(
                GIT_RESET_MODES, self.on_reset_mode_selection, flags=sublime.MONOSPACE_FONT
            )

    def on_reset_mode_selection(self, index):
        if index == -1:
            sublime.set_timeout_async(self.run_async, 1)
        elif 0 <= index < len(GIT_RESET_MODES):
            self.on_reset(GIT_RESET_MODES[index][0].strip())

    def on_reset(self, reset_mode):
        # Split the reset mode to support multiple args, e.g. "--mixed -N"
        args = reset_mode.split() + [self._selected_hash]

        def do_reset():
            self.git("reset", *args)

        if reset_mode == "--hard":
            util.actions.destructive("perform a hard reset")(do_reset)()
        else:
            do_reset()


class GsResetBranch(GsResetCommand):
    def run(self):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        self.all_branches = [b.name_with_remote for b in self.get_branches()]

        if hasattr(self, '_selected_branch') and self._selected_branch in self.all_branches:
            pre_selected_index = self.all_branches.index(self._selected_branch)
        else:
            pre_selected_index = self.all_branches.index(self.get_current_branch_name())

        self.window.show_quick_panel(
            self.all_branches,
            self.on_branch_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=pre_selected_index
        )

    def on_branch_selection(self, index):
        if index == -1:
            return
        self._selected_branch = self.all_branches[index]
        self.do_action(self._selected_branch)


class GsResetReflogCommand(GsResetCommand):
    def run_async(self):
        log_output = self.git(
            "reflog",
            "-{}".format(self._limit) if self._limit else None,
            "--skip={}".format(self._skip) if self._skip else None,
            '--format=%h%n%H%n%s%n%gs%n%gd%n%an%n%at%x00'
        ).strip("\x00")

        commit_list = []
        self._hashes = []
        for entry in log_output.split("\x00"):
            try:
                short_hash, long_hash, summary, reflog_name, reflog_selector, author, datetime = (
                    entry.strip("\n").split("\n"))

                commit_list.append([
                    reflog_selector + " " + reflog_name,
                    short_hash + " " + summary,
                    author + ", " + util.dates.fuzzy(datetime)
                ])
                self._hashes.append(long_hash)

            except ValueError:
                # Empty line - less expensive to catch the exception once than
                # to check truthiness of entry.strip() each time.
                pass

        self.display_commits(commit_list)
