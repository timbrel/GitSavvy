import sublime
from sublime_plugin import WindowCommand
import re

from ..git_command import GitCommand
from ..ui_mixins.quick_panel import PanelActionMixin, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
COMMIT_LINE = re.compile(r"[%s][ /_\|\-.]*([a-z0-9]{3,})" % COMMIT_NODE_CHAR_OPTIONS)


class GsCompareCommitCommand(WindowCommand, GitCommand):

    """
    Show a view of all commits diff between branches.
    """

    def run(self, base_commit, target_commit=None, file_path=None, title=None):
        base_commit = base_commit or "HEAD"
        target_commit = target_commit or "HEAD"
        merge_bases = self.git('merge-base', base_commit, target_commit, '-a').strip().splitlines()
        if not merge_bases:
            self.window.status_message("No common base for {} and {}".format(base_commit, target_commit))
            return

        branches = (
            [base_commit, target_commit]
            + ['{}^!'.format(base) for base in map(self.get_short_hash, merge_bases)]
        )
        self.window.run_command("gs_graph", {
            'all': False,
            'file_path': file_path,
            'branches': branches,
            'follow': base_commit
        })


class GsCompareAgainstReferenceCommand(WindowCommand, GitCommand):
    def run(self, base_commit=None, target_commit=None, file_path=None):
        self._file_path = file_path
        self._base_commit = base_commit
        self._target_commit = target_commit
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_single_line_input_panel("Ref:", "", self.show_diff, None, self.on_cancel)

    def show_diff(self, ref):
        self.window.run_command("gs_compare_commit", {
            "file_path": self._file_path,
            "base_commit": self._base_commit if self._base_commit else ref,
            "target_commit": self._target_commit if self._target_commit else ref
        })

    def on_cancel(self):
        self.window.run_command("gs_compare_against", {
            "base_commit": self._base_commit,
            "target_commit": self._target_commit,
            "file_path": self._file_path
        })


class GsCompareAgainstBranchCommand(WindowCommand, GitCommand):
    def run(self, base_commit=None, target_commit=None, file_path=None):
        self._file_path = file_path
        self._base_commit = base_commit
        self._target_commit = target_commit
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        show_branch_panel(self.on_branch_selection)

    def on_branch_selection(self, branch):
        if branch:
            self.window.run_command("gs_compare_commit", {
                "file_path": self._file_path,
                "base_commit": self._base_commit if self._base_commit else branch,
                "target_commit": self._target_commit if self._target_commit else branch
            })
        else:
            self.window.run_command("gs_compare_against", {
                "base_commit": self._base_commit,
                "target_commit": self._target_commit,
                "file_path": self._file_path
            })


class GsCompareAgainstCommand(PanelActionMixin, WindowCommand, GitCommand):
    default_actions = [
        ["compare_against_branch", "Branch"],
        ["compare_against_reference", "Reference"],
    ]

    def run(self, base_commit=None, target_commit=None, file_path=None, current_file=False):
        self._file_path = self.file_path if current_file else file_path
        self._base_commit = base_commit
        self._target_commit = target_commit
        if base_commit and target_commit:
            self.window.run_command("gs_compare_commit", {
                "base_commit": self._base_commit,
                "target_commit": self._target_commit,
                "file_path": self._file_path
            })
            return
        super().run()

    def update_actions(self):
        super().update_actions()
        if self._target_commit != "HEAD":
            self.actions = [["compare_against_head", "HEAD"]] + self.actions

    def compare_against_branch(self):
        self.window.run_command("gs_compare_against_branch", {
            "base_commit": self._base_commit,
            "target_commit": self._target_commit,
            "file_path": self._file_path
        })

    def compare_against_reference(self):
        self.window.run_command("gs_compare_against_reference", {
            "base_commit": self._base_commit,
            "target_commit": self._target_commit,
            "file_path": self._file_path
        })

    def compare_against_head(self):
        self.window.run_command("gs_compare_commit", {
            "base_commit": self._base_commit,
            "target_commit": self._target_commit,
            "file_path": self._file_path
        })
