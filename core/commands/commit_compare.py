from sublime_plugin import WindowCommand

from ..git_command import GitCommand
from ..ui_mixins.quick_panel import PanelActionMixin, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel


__all__ = (
    "gs_compare_commit",
    "gs_compare_against_reference",
    "gs_compare_against_branch",
    "gs_compare_against",
)


class gs_compare_commit(WindowCommand, GitCommand):

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


class gs_compare_against_reference(WindowCommand, GitCommand):
    def run(self, base_commit=None, target_commit=None, file_path=None, target_hints=None):
        self._file_path = file_path
        self._base_commit = base_commit
        self._target_commit = target_commit
        self._target_hints = target_hints
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
            "file_path": self._file_path,
            "target_hints": self._target_hints,
        })


class gs_compare_against_branch(WindowCommand, GitCommand):
    def run(self, base_commit=None, target_commit=None, file_path=None, target_hints=None):
        self._file_path = file_path
        self._base_commit = base_commit
        self._target_commit = target_commit
        self._target_hints = target_hints
        show_branch_panel(self.on_branch_selection, on_cancel=self.recurse)

    def on_branch_selection(self, branch):
        self.window.run_command("gs_compare_commit", {
            "file_path": self._file_path,
            "base_commit": self._base_commit if self._base_commit else branch,
            "target_commit": self._target_commit if self._target_commit else branch
        })

    def recurse(self):
        self.window.run_command("gs_compare_against", {
            "base_commit": self._base_commit,
            "target_commit": self._target_commit,
            "file_path": self._file_path,
            "target_hints": self._target_hints,
        })


class gs_compare_against(PanelActionMixin, WindowCommand):
    default_actions = [
        ["compare_against_branch", "Select branch..."],
        ["compare_against_reference", "Enter reference..."],
    ]

    def run(self, base_commit=None, target_commit=None, file_path=None, current_file=False, target_hints=None):
        self._file_path = self.file_path if current_file else file_path
        self._base_commit = base_commit
        self._target_commit = target_commit
        self._target_hints = target_hints or []
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
        self.actions = [
            ["compare_against_target", target, (target,)]
            for target in self._target_hints
        ] + self.actions

    def compare_against_branch(self):
        self.window.run_command("gs_compare_against_branch", {
            "base_commit": self._base_commit,
            "target_commit": self._target_commit,
            "file_path": self._file_path,
            "target_hints": self._target_hints,
        })

    def compare_against_reference(self):
        self.window.run_command("gs_compare_against_reference", {
            "base_commit": self._base_commit,
            "target_commit": self._target_commit,
            "file_path": self._file_path,
            "target_hints": self._target_hints,
        })

    def compare_against_target(self, target):
        self.window.run_command("gs_compare_commit", {
            "base_commit": self._base_commit,
            "target_commit": target,
            "file_path": self._file_path
        })
