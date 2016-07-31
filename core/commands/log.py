import re
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


class GsLogCommand(WindowCommand, GitCommand):
    cherry_branch = None

    def run(self, filename=None, limit=6000, author=None, log_current_file=False, target_hash=None, branch=None):
        self._pagination = 0
        self._filename = filename
        self._limit = limit
        self._author = author
        self._log_current_file = log_current_file
        self._target_hash = target_hash
        self._branch = branch
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
            self._filename,
            self._branch if self._branch else None
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

        try:
            pre_selected_index = self._hashes.index(self._selected_hash) if hasattr(self, '_selected_hash') else 0
        except ValueError:
            pre_selected_index = 0

        # _on_hash_selection has to be called by set_timeout_async
        # otherwise, on_highlight_commit_async will be called after
        # _on_hash_selection is executed.
        self.window.show_quick_panel(
            self._entries,
            lambda index: sublime.set_timeout_async(lambda: self._on_hash_selection(index), 10),
            flags=sublime.MONOSPACE_FONT | sublime.KEEP_OPEN_ON_FOCUS_LOST,
            selected_index=pre_selected_index,
            on_highlight=self.on_highlight_commit
        )

    def on_highlight_commit(self, index):
        sublime.set_timeout_async(lambda: self.on_highlight_commit_async(index))

    def on_highlight_commit_async(self, index):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        show_more = savvy_settings.get("log_show_more_commit_info")
        show_full = savvy_settings.get("show_full_commit_info")
        if not show_more:
            return
        commit_hash = "%s" % self._hashes[index]
        text = self.git("show", commit_hash, "--no-color", "--format=fuller", "--quiet" if not show_full else None)
        output_view = self.window.create_output_panel("show_commit_info")
        output_view.set_read_only(False)
        output_view.run_command("gs_replace_view_text", {"text": text, "nuke_cursors": True})
        output_view.set_syntax_file("Packages/GitSavvy/syntax/show_commit.sublime-syntax")
        output_view.set_read_only(True)
        self.window.run_command("show_panel", {"panel": "output.show_commit_info"})

    def _on_hash_selection(self, index):
        self.window.run_command("hide_panel", {"panel": "output.show_commit_info"})
        self.on_hash_selection(index)

    def on_hash_selection(self, index):
        options_array = [
                "Show commit",
                "Checkout commit",
                "Compare commit against ...",
                "Copy the full SHA",
                "Diff commit",
                "Diff commit (cached)"
        ]

        if self._log_current_file:
            options_array.append("Show file at commit")

        if index == -1:
            return
        if index == self._limit:
            self._pagination += self._limit
            sublime.set_timeout_async(self.run_async, 1)
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
            sublime.set_timeout_async(self.run_async, 1)
            return

        self.quick_panel_log_idx = index

        if index == 0:
            self.window.run_command("gs_show_commit", {"commit_hash": self._selected_hash})

        if index == 1:
            self.checkout_ref(self._selected_hash)
            util.view.refresh_gitsavvy(self.view)

        if index == 2:
            self.window.run_command("gs_compare_against", {
                "target_commit": self._selected_hash,
                "file_path": self._filename
            })

        if index == 3:
            sublime.set_clipboard(self._selected_hash)

        if index in [4, 5]:
            in_cached_mode = index == 5
            self.window.run_command("gs_diff", {
                "in_cached_mode": in_cached_mode,
                "file_path": self._filename,
                "current_file": bool(self._filename),
                "base_commit": self._selected_hash
            })

        if index == 6:
            lang = self.window.active_view().settings().get('syntax')
            self.window.run_command(
                "gs_show_file_at_commit",
                {"commit_hash": self._selected_hash, "filepath": self._filename, "lang": lang})


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


class GsLogBranchCommand(WindowCommand, GitCommand):

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
        if index < 0:
            return
        self._selected_branch = self.all_branches[index]
        self.window.run_command("gs_log", {"branch": self._selected_branch})


class GsCompareAgainstCommand(WindowCommand, GitCommand):
    def run(self, target_commit=None, file_path=None):
        self._file_path = file_path
        self._target_commit = target_commit
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        options_array = [
            "Any Reference",
            "Branch"
        ]

        self.window.show_quick_panel(
            options_array,
            self.on_select_against,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.quick_panel_compare_against_idx
        )

    def on_select_against(self, index):
        if index < 0:
            return

        self.quick_panel_compare_against_idx = index

        if index == 0:
            self.window.run_command("gs_compare_against_reference", {
                "target_commit": self._target_commit,
                "file_path": self._file_path,
                "from_panel": True
            })

        if index == 1:
            self.window.run_command("gs_compare_against_branch", {
                "target_commit": self._target_commit,
                "file_path": self._file_path,
                "from_panel": True
            })


class GsCompareAgainstReferenceCommand(WindowCommand, GitCommand):
    def run(self, target_commit=None, file_path=None, from_panel=False):
        self._file_path = file_path
        self._target_commit = target_commit
        self.from_panel = from_panel
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        self.window.show_input_panel("Ref:", "", self.show_diff, None, self.on_cancel)

    def show_diff(self, ref):
        self.window.run_command("gs_diff", {
            "file_path": self._file_path,
            "current_file": bool(self._file_path),
            "base_commit": ref,
            "target_commit": self._target_commit,
            "disable_stage": True
        })

    def on_cancel(self):
        if self.from_panel:
            self.window.run_command("gs_compare_against", {
                "target_commit": self._target_commit,
                "file_path": self._file_path
            })


class GsCompareAgainstBranchCommand(GsLogBranchCommand):
    """
    Compare a given commit against a selected branch or selected ref
    """
    def run(self, target_commit=None, file_path=None, from_panel=False):
        self._file_path = file_path
        self._target_commit = target_commit
        self.from_panel = from_panel
        sublime.set_timeout_async(self.run_async)

    def on_branch_selection(self, index):
        if index < 0:
            if self.from_panel:
                self.window.run_command("gs_compare_against", {
                    "target_commit": self._target_commit,
                    "file_path": self._file_path
                })
            return
        self._selected_branch = self.all_branches[index]
        self.window.run_command("gs_diff", {
            "file_path": self._file_path,
            "current_file": bool(self._file_path),
            "base_commit": self._selected_branch,
            "target_commit": self._target_commit,
            "disable_stage": True
        })
