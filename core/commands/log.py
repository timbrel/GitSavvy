from copy import deepcopy
import re

from sublime_plugin import WindowCommand
import sublime

from ...common import util
from ..git_command import GitCommand
from ..ui_mixins.quick_panel import PanelActionMixin, PanelCommandMixin
from ..ui_mixins.quick_panel import show_log_panel, show_branch_panel


class LogMixin(object):
    """
    Display git log in a quick panel for given file and branch. Upon selection
    of a commit, displays an "action menu" via the ``GsLogActionCommand``.

    Supports paginated fetching of log (defaults to 6000 entries per "page").

    This mixin can be used with both ``WindowCommand`` and ``TextCommand``,
    but the subclass must also inherit fro GitCommand (for the `git()` method)
    """

    selected_index = 0

    def run(self, *args, file_path=None, **kwargs):
        sublime.set_timeout_async(lambda: self.run_async(file_path=file_path, **kwargs), 0)

    def run_async(self, file_path=None, **kwargs):
        follow = self.savvy_settings.get("log_follow_rename") if file_path else False
        show_log_panel(
            self.log_generator(file_path=file_path, follow=follow, **kwargs),
            lambda commit: self.on_done(commit, file_path=file_path, **kwargs),
            selected_index=self.selected_index,
            on_highlight=lambda commit: self.on_highlight(commit, file_path=file_path)
        )

    def on_done(self, commit, **kwargs):
        sublime.active_window().run_command("hide_panel", {"panel": "output.show_commit_info"})
        if commit:
            self.do_action(commit, **kwargs)

    def on_highlight(self, commit, file_path=None):
        if not commit:
            return
        if not self.savvy_settings.get("log_show_more_commit_info", True):
            return
        if hasattr(self, 'window'):
            window = self.window
        else:
            window = self.view.window()
        window.run_command(
            "gs_show_commit_info", {"commit_hash": commit, "file_path": file_path})

    def do_action(self, commit_hash, **kwargs):
        if hasattr(self, 'window'):
            window = self.window
        else:
            window = self.view.window()
        window.run_command("gs_log_action", {
            "commit_hash": commit_hash,
            "file_path": kwargs.get("file_path")
        })


class GsLogCurrentBranchCommand(LogMixin, WindowCommand, GitCommand):
    pass


class GsLogAllBranchesCommand(LogMixin, WindowCommand, GitCommand):

    def log(self, **kwargs):
        return super().log(all_branches=True, **kwargs)


class GsLogByAuthorCommand(LogMixin, WindowCommand, GitCommand):

    """
    Open a quick panel containing all committers for the active
    repository, ordered by most commits, Git name, and email.
    Once selected, display a quick panel with all commits made
    by the specified author.
    """

    def run_async(self, **kwargs):
        email = self.git("config", "user.email").strip()
        self._entries = []

        commiter_str = self.git("shortlog", "-sne", "HEAD")
        for line in commiter_str.split('\n'):
            m = re.search(r'\s*(\d*)\s*(.*)\s<(.*)>', line)
            if m is None:
                continue
            commit_count, author_name, author_email = m.groups()
            author_text = "{} <{}>".format(author_name, author_email)
            self._entries.append((commit_count, author_name, author_email, author_text))

        try:
            selected_index = (list(line[2] for line in self._entries)).index(email)
        except ValueError:
            selected_index = 0

        self.window.show_quick_panel(
            [entry[3] for entry in self._entries],
            lambda index: self.on_author_selection(index, **kwargs),
            flags=sublime.MONOSPACE_FONT,
            selected_index=selected_index
        )

    def on_author_selection(self, index, **kwargs):
        if index == -1:
            return
        self._selected_author = self._entries[index][3]
        super().run_async(**kwargs)

    def log(self, **kwargs):
        return super().log(author=self._selected_author, **kwargs)


class GsLogByBranchCommand(LogMixin, WindowCommand, GitCommand):

    def run_async(self, **kwargs):
        show_branch_panel(lambda branch: self.on_branch_selection(branch, **kwargs))

    def on_branch_selection(self, branch, **kwargs):
        if branch:
            super().run_async(branch=branch, **kwargs)


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
        ["cherry_pick", "Cherry-pick commit"],
        ["revert_commit", "Revert commit"],
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
            self.actions.insert(2, ["blame_file_atcommit", "Blame file at commit"])
            self.actions.insert(3, ["checkout_file_at_commit", "Checkout file at commit"])

    def show_commit(self):
        self.window.run_command("gs_show_commit", {"commit_hash": self._commit_hash})

    def checkout_commit(self):
        self.checkout_ref(self._commit_hash)

    def cherry_pick(self):
        self.git("cherry-pick", self._commit_hash)

    def revert_commit(self):
        self.window.run_command("gs_revert_commit", {
            "commit_hash": self._commit_hash
        })

    def compare_against(self):
        self.window.run_command("gs_compare_against", {
            "base_commit": self._commit_hash,
            "file_path": self._file_path
        })

    def copy_sha(self):
        sublime.set_clipboard(self.git("rev-parse", self._commit_hash).strip())

    def _diff_commit(self, cache=False):
        self.window.run_command("gs_diff", {
            "in_cached_mode": cache,
            "file_path": self._file_path,
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

    def blame_file_atcommit(self):
        self.window.run_command(
            "gs_blame",
            {"commit_hash": self._commit_hash, "file_path": self._file_path})

    def checkout_file_at_commit(self):
        self.checkout_ref(self._commit_hash, fpath=self._file_path)
