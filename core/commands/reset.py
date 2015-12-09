import sublime

from .log import GsLogCommand
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


class GsResetCommand(GsLogCommand):

    def on_hash_selection(self, index):
        if index == -1:
            return

        if index == self._limit:
            self._pagination += self._limit
            sublime.set_timeout_async(lambda: self.run_async(), 1)
            return

        self._selected_hash = self._hashes[index]

        use_reset_mode = sublime.load_settings("GitSavvy.sublime-settings").get("use_reset_mode")

        if use_reset_mode:
            self.on_reset(use_reset_mode)
        else:
            self.window.show_quick_panel(
                GIT_RESET_MODES, self.on_reset_mode_selection, flags=sublime.MONOSPACE_FONT
            )

    def on_reset_mode_selection(self, index):
        if 0 <= index < len(GIT_RESET_MODES):
            self.on_reset(GIT_RESET_MODES[index][0].strip())

    def on_reset(self, reset_mode):
        # Split the reset mode to support multiple args, e.g. "--mixed -N"
        args = reset_mode.split() + [self._selected_hash]
        do_reset = lambda: self.git("reset", *args)

        if reset_mode == "--hard":
            util.actions.destructive("perform a hard reset")(do_reset)()
        else:
            do_reset()


class GsResetReflogCommand(GsResetCommand):

    def run_async(self):
        log_output = self.git(
            "reflog",
            "-{}".format(self._limit) if self._limit else None,
            "--skip={}".format(self._pagination) if self._pagination else None,
            '--format=%h%n%H%n%s%n%gs%n%gd%n%an%n%at%x00'
        ).strip("\x00")

        self._entries = []
        self._hashes = []
        for entry in log_output.split("\x00"):
            try:
                short_hash, long_hash, summary, reflog_name, reflog_selector, author, datetime = (
                    entry.strip("\n").split("\n"))

                self._entries.append([
                    reflog_selector + " " + reflog_name,
                    short_hash + " " + summary,
                    author + ", " + util.dates.fuzzy(datetime)
                ])
                self._hashes.append(long_hash)

            except ValueError:
                # Empty line - less expensive to catch the exception once than
                # to check truthiness of entry.strip() each time.
                pass

        if not len(self._entries) < self._limit:
            self._entries.append([
                ">>> NEXT {} COMMITS >>>".format(self._limit),
                "Skip this set of reflog entries and choose from the next-oldest batch."
            ])

        self.window.show_quick_panel(
            self._entries,
            self.on_hash_selection,
            flags=sublime.MONOSPACE_FONT
        )
