import sublime
from sublime_plugin import WindowCommand

from .base_command import BaseCommand
from ..common import util


class GsLogCommand(WindowCommand, BaseCommand):

    def run(self, filename=None, limit=6000):
        self._pagination = 0
        self._filename = filename
        self._limit = limit
        sublime.set_timeout_async(lambda: self.run_async(), 1)

    def run_async(self):
        log_output = self.git(
            "log",
            "-{}".format(self._limit) if self._limit else None,
            "--skip={}".format(self._pagination) if self._pagination else None,
            '--format=%h%n%H%n%s%n%an%n%at%x00',
            "--" if self._filename else None,
            self._filename
        ).strip("\x00")

        self._entries = []
        self._hashes = []
        for entry in log_output.split("\x00"):
            try:
                short_hash, long_hash, summary, author, datetime = entry.strip("\n").split("\n")
                self._entries.append([
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
                "Skip this set of commits and choose from the next-oldest batch."
            ])

        self.window.show_quick_panel(
            self._entries,
            self.on_selection,
            sublime.MONOSPACE_FONT
        )

    def on_selection(self, index):
        if index == -1:
            return
        if index == self._limit:
            self._pagination += self._limit
            sublime.set_timeout_async(lambda: self.run_async(), 1)

        selected_hash = self._hashes[index]
        self.window.run_command("gs_show_commit", {"commit_hash": selected_hash})


class GsLogCurrentFileCommand(WindowCommand, BaseCommand):

    def run(self):
        self.window.run_command("gs_log", {"filename": self.file_path})
