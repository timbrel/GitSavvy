from copy import deepcopy
import re

from sublime_plugin import WindowCommand
import sublime

from ...common import util
from ..git_command import GitCommand
from ..ui_mixins.quick_panel import PanelActionMixin, PanelCommandMixin, show_log_panel


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


class GsLogCommand(PanelCommandMixin, WindowCommand, GitCommand):
    default_actions = [
        ["gs_log_current_branch", "For current branch"],
        ["gs_log_all_branches", "For all branches"],
        ["gs_log_by_author", "Filtered by author"],
        ["gs_log_by_branch", "Filtered by branch"],
    ]

    def run(self, file_path=None, current_file=False):
        self._file_path = self.file_path if current_file else file_path
        super().run()

    def update_actions(self):
        # deep copy list to avoid duplication via mutable lists
        self.actions = deepcopy(self.default_actions)
        for action in self.actions:
            # append a tuple to pass an argument
            action.append(({"file_path": self._file_path}, ))


class GsLogActionCommand(PanelActionMixin, WindowCommand, GitCommand):
    default_actions = [
        ["show_commit", "Show commit"],
        ["checkout_commit", "Checkout commit"],
        ["compare_against", "Compare commit against ..."],
        ["copy_sha", "Copy the full SHA"],
        ["diff_commit", "Diff commit"],
        ["diff_commit_cache", "Diff commit (cached)"]
    ]

    def run(self, commit_hash, file_path=None):
        self._commit_hash = commit_hash
        self._file_path = file_path
        super().run()

    def update_actions(self):
        super().update_actions()
        if self._file_path:
            self.actions.insert(1, ["show_file_at_commit", "Show file at commit"])

    def show_commit(self):
        self.window.run_command("gs_show_commit", {"commit_hash": self._commit_hash})

    def checkout_commit(self):
        self.checkout_ref(self._commit_hash)
        util.view.refresh_gitsavvy(self.view, refresh_sidebar=True)

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
