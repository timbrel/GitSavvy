import re
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


class GsLogCommand(WindowCommand, GitCommand):
    cherry_branch = None

    def run(self, filename=None, limit=6000, author=None, log_current_file=False):
        self._pagination = 0
        self._filename = filename
        self._limit = limit
        self._author = author
        self._log_current_file = log_current_file
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        log_output = self.git(
            "log",
            "-{}".format(self._limit) if self._limit else None,
            "--skip={}".format(self._pagination) if self._pagination else None,
            "--author={}".format(self._author) if self._author else None,
            '--format=%h%n%H%n%s%n%an%n%at%x00',
            '--cherry' if self.cherry_branch else None,
            '..{}'.format(self.cherry_branch) if self.cherry_branch else None,
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
            self.on_hash_selection,
            flags=sublime.MONOSPACE_FONT
        )

    def on_hash_selection(self, index):
        options_array = [
                "Show commit",
                "Compare commit against working directory",
                "Compare commit against index"
        ]

        if self._log_current_file:
            options_array.append("Show file at commit")

        if index == -1:
            return
        if index == self._limit:
            self._pagination += self._limit
            sublime.set_timeout_async(lambda: self.run_async(), 1)
            return

        self._selected_hash = self._hashes[index]

        self.window.show_quick_panel(
            options_array,
            self.on_output_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.quick_panel_log_idx
        )

    def on_output_selection(self, index):
        if index == -1:
            return

        self.quick_panel_log_idx = index
        if index == 0:
            self.window.run_command("gs_show_commit", {"commit_hash": self._selected_hash})

        if index in [1, 2]:
            self.window.run_command("gs_diff", {
                "in_cached_mode": index == 2,
                "file_path": self._filename,
                "current_file": bool(self._filename),
                "base_commit": self._selected_hash
            })

        if index == 3:
            self.window.run_command(
                "gs_show_file_at_commit",
                {"commit_hash": self._selected_hash, "filepath": self._filename})


class GsLogCurrentFileCommand(WindowCommand, GitCommand):

    def run(self):
        self.window.run_command("gs_log", {"filename": self.file_path, "log_current_file": True})


class GsLogByAuthorCommand(WindowCommand, GitCommand):

    """
    Open a quick panel containing all committers for the active
    repository, ordered by most commits, Git name, and email.
    Once selected, display a quick panel with all commits made
    by the specified author.
    """

    def run(self):
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        name = self.git("config", "user.name").strip()
        email = self.git("config", "user.email").strip()
        self._entries = []

        commiter_str = self.git("shortlog", "-sne", "HEAD")
        for line in commiter_str.split('\n'):
            m = re.search('\s*(\d*)\s*(.*)\s<(.*)>', line)
            if m is None:
                continue
            commit_count, author_name, author_email = m.groups()
            author_text = "{} <{}>".format(author_name, author_email)
            self._entries.append((commit_count, author_name, author_email, author_text))

        self.window.show_quick_panel(
            [entry[3] for entry in self._entries],
            self.on_entered,
            flags=sublime.MONOSPACE_FONT,
            selected_index=(list(line[2] for line in self._entries)).index(email)
        )

    def on_entered(self, index):
        if index == -1:
            return

        author_text = self._entries[index][3]
        self.window.run_command("gs_log", {"author": author_text})
