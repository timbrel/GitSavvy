import re
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util


class GsLogBase(WindowCommand, GitCommand):
    _limit = 6000

    def run(self, file_path=None):
        self._skip = 0
        self._file_path = file_path
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        logs = self.log(file_path=self._file_path, limit=self._limit, skip=self._skip)
        self._hashes = [l.long_hash for l in logs]
        self.display_commits(self.render_commits(logs))

    def render_commits(self, logs):
        commit_list = []
        for l in logs:
            commit_list.append([
                l.short_hash + " " + l.summary,
                l.author + ", " + util.dates.fuzzy(l.datetime)
            ])
        return commit_list

    def display_commits(self, commit_list):
        if len(commit_list) >= self._limit:
            commit_list.append([
                ">>> NEXT {} COMMITS >>>".format(self._limit),
                "Skip this set of commits and choose from the next-oldest batch."
            ])
        self.window.show_quick_panel(
            commit_list,
            lambda index: sublime.set_timeout_async(lambda: self.on_commit_selection(index), 10),
            flags=sublime.MONOSPACE_FONT | sublime.KEEP_OPEN_ON_FOCUS_LOST,
            on_highlight=self.on_commit_highlight
        )

    def on_commit_highlight(self, index):
        sublime.set_timeout_async(lambda: self.on_commit_highlight_async(index))

    def on_commit_highlight_async(self, index):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        show_more = savvy_settings.get("log_show_more_commit_info")
        if not show_more:
            return
        self.window.run_command("gs_show_commit_info", {"commit_hash": self._hashes[index]})

    def on_commit_selection(self, index):
        self.window.run_command("hide_panel", {"panel": "output.show_commit_info"})
        if index == -1:
            return
        if index == self._limit:
            self._skip += self._limit
            sublime.set_timeout_async(self.run_async, 1)
            return
        self._selected_commit = self._hashes[index]
        self.do_action(self._selected_commit)

    def do_action(self, commit_hash):
        self.window.run_command("gs_log_action", {
            "commit_hash": commit_hash,
            "file_path": self._file_path
        })


class GsLogCurrentBranchCommand(GsLogBase):
    pass


class GsLogByAuthorCommand(GsLogBase):

    """
    Open a quick panel containing all committers for the active
    repository, ordered by most commits, Git name, and email.
    Once selected, display a quick panel with all commits made
    by the specified author.
    """

    def run_async(self):
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
            self.on_author_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=(list(line[2] for line in self._entries)).index(email)
        )

    def on_author_selection(self, index):
        if index == -1:
            return
        self._selected_author = self._entries[index][3]
        super().run_async()

    def log(self, **kwargs):
        return super().log(author=self._selected_author, **kwargs)


class GsLogByBranchCommand(GsLogBase):

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
        super().run_async()

    def log(self, **kwargs):
        return super().log(branch=self._selected_branch, **kwargs)


class GsLogCommand(WindowCommand, GitCommand):
    def run(self, file_path=None, current_file=False):
        self._file_path = self.file_path if current_file else file_path
        options_array = [
            "For current branch",
            "Filtered by author",
            "Filtered by branch",
        ]
        self.window.show_quick_panel(
            options_array,
            self.on_option_selection,
            flags=sublime.MONOSPACE_FONT
        )

    def on_option_selection(self, index):
        if index < 0:
            return

        if index == 0:
            self.window.run_command("gs_log_current_branch", {"file_path": self._file_path})
        elif index == 1:
            self.window.run_command("gs_log_by_author", {"file_path": self._file_path})
        elif index == 2:
            self.window.run_command("gs_log_by_branch", {"file_path": self._file_path})


class GsLogActionCommand(WindowCommand, GitCommand):

    def run(self, commit_hash, file_path=None):
        self._commit_hash = commit_hash
        self._file_path = file_path
        self.actions = [
                "Show commit",
                "Checkout commit",
                "Compare commit against ...",
                "Copy the full SHA",
                "Diff commit",
                "Diff commit (cached)"
        ]

        if self._file_path:
            self.actions.append("Show file at commit")

        self.window.show_quick_panel(
            self.actions,
            self.on_action_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.quick_panel_log_idx
        )

    def on_action_selection(self, index):
        if index == -1:
            return

        self.quick_panel_log_idx = index

        action = self.actions[index]
        if action == "Show commit":
            self.window.run_command("gs_show_commit", {"commit_hash": self._commit_hash})

        elif action == "Checkout commit":
            self.checkout_ref(self._commit_hash)
            util.view.refresh_gitsavvy(self.view)

        elif action == "Compare commit against ...":
            self.window.run_command("gs_compare_against", {
                "target_commit": self._commit_hash,
                "file_path": self._file_path
            })

        elif action == "Copy the full SHA":
            sublime.set_clipboard(self._commit_hash)

        elif "Diff commit" in action:
            in_cached_mode = "(cached)" in action
            self.window.run_command("gs_diff", {
                "in_cached_mode": in_cached_mode,
                "file_path": self._file_path,
                "current_file": bool(self._file_path),
                "base_commit": self._commit_hash,
                "disable_stage": True
            })

        elif action == "Show file at commit":
            lang = self.window.active_view().settings().get('syntax')
            self.window.run_command(
                "gs_show_file_at_commit",
                {"commit_hash": self._commit_hash, "filepath": self._file_path, "lang": lang})
