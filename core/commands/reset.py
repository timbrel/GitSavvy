import sublime

from .log import GsLogCommand
from ...common import util


class GsResetCommand(GsLogCommand):

    def on_hash_selection(self, index):
        if index == -1:
            return
        if index == self._limit:
            self._pagination += self._limit
            sublime.set_timeout_async(lambda: self.run_async(), 1)
            return

        self.git("reset", "--mixed", self._hashes[index])


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
