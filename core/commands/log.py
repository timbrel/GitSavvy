import re
import sublime
from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ...common import util
from ...common.quick_panel import show_log_panel


class GsLogBase(WindowCommand, GitCommand):

    _limit = 6000

    def run(self, file_path=None):
        self._file_path = file_path
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_log_panel(self.log_generator(), self.do_action, self._limit)

    def log_generator(self):
        skip = 0
        while True:
            logs = self.log(file_path=self._file_path, skip=skip, limit=self._limit)
            if not logs:
                break
            for l in logs:
                yield l
            skip = skip + self._limit

    def do_action(self, commit_hash):
        self.window.run_command("gs_log_action", {
            "commit_hash": commit_hash,
            "file_path": self._file_path
        })


class GsLogCurrentBranchCommand(GsLogBase):
    pass


class GsLogAllBranchesCommand(GsLogBase):

    def log(self, **kwargs):
        return super().log(all_branches=True, **kwargs)


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
        if index == -1:
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
            "For all branches",
            "Filtered by author",
            "Filtered by branch",
        ]
        self.window.show_quick_panel(
            options_array,
            self.on_option_selection,
            flags=sublime.MONOSPACE_FONT
        )

    def on_option_selection(self, index):
        if index == -1:
            return

        if index == 0:
            self.window.run_command("gs_log_current_branch", {"file_path": self._file_path})
        elif index == 1:
            self.window.run_command("gs_log_all_branches", {"file_path": self._file_path})
        elif index == 2:
            self.window.run_command("gs_log_by_author", {"file_path": self._file_path})
        elif index == 3:
            self.window.run_command("gs_log_by_branch", {"file_path": self._file_path})


class GsLogActionCommand(WindowCommand, GitCommand):

    def run(self, commit_hash, file_path=None):
        self._commit_hash = commit_hash
        self._file_path = file_path
        self.actions = [
            ["show_commit", "Show commit"],
            ["checkout_commit", "Checkout commit"],
            ["compare_against", "Compare commit against ..."],
            ["copy_sha", "Copy the full SHA"],
            ["diff_commit", "Diff commit"],
            ["diff_commit_cache", "Diff commit (cached)"]
        ]

        if self._file_path:
            self.actions.insert(1, ["show_file_at_commit", "Show file at commit"])

        self.window.show_quick_panel(
            [a[1] for a in self.actions],
            self.on_action_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.quick_panel_log_idx
        )

    def on_action_selection(self, index):
        if index == -1:
            return

        self.quick_panel_log_idx = index

        action = self.actions[index][0]
        eval("self.{}()".format(action))

    def show_commit(self):
        self.window.run_command("gs_show_commit", {"commit_hash": self._commit_hash})

    def checkout_commit(self):
        self.checkout_ref(self._commit_hash)
        util.view.refresh_gitsavvy(self.view)

    def compare_against(self):
        self.window.run_command("gs_compare_against", {
            "target_commit": self._commit_hash,
            "file_path": self._file_path
        })

    def copy_sha(self):
        sublime.set_clipboard(self.git("rev-parse", self._commit_hash).strip())

    def _diff_commit(self, cache=False):
        self.window.run_command("gs_diff", {
            "in_cached_mode": cache,
            "file_path": self._file_path,
            "current_file": bool(self._file_path),
            "base_commit": self._commit_hash,
            "disable_stage": True
        })

    def diff_commit(self):
        self._diff_commit(cache=False)

    def diff_commit_cache(self):
        self._diff_commit(cache=True)

    def show_file_at_commit(self):
        lang = self.window.active_view().settings().get('syntax')
        self.window.run_command(
            "gs_show_file_at_commit",
            {"commit_hash": self._commit_hash, "filepath": self._file_path, "lang": lang})
