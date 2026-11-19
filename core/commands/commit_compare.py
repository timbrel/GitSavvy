from __future__ import annotations

import sublime
from sublime_plugin import WindowCommand

from ..base_commands import GsTextCommand, GsWindowCommand
from ..exceptions import GitSavvyError
from ..git_command import GitCommand
from ..interfaces.log_graph_prelude import comparison_tips, short_ref
from ..types import FullHash
from ..ui_mixins.quick_panel import PanelActionMixin, show_branch_panel
from ..ui_mixins.input_panel import show_single_line_input_panel


__all__ = (
    "gs_compare_commit",
    "gs_log_graph_focus_comparison",
    "gs_compare_against_reference",
    "gs_compare_against_branch",
    "gs_compare_against",
)


class gs_compare_commit(GsWindowCommand):

    """
    Show a view of all commits diff between branches.
    """

    def run(self, base_commit, target_commit=None, file_path=None, title=None):
        base_commit = base_commit or "HEAD"
        target_commit = target_commit or "HEAD"
        tips = [base_commit, target_commit]
        view = self.window.active_view()
        if view and view.settings().get("git_savvy.log_graph_view"):
            view.run_command("gs_log_graph_focus_comparison", {"tips": tips})
            return

        boundaries = merge_base_boundaries(self, tips)
        if not boundaries:
            return
        branches = tips + boundaries
        self.window.run_command("gs_graph", {
            'all': False,
            'file_path': file_path,
            'branches': branches,
            'follow': base_commit
        })


class gs_log_graph_focus_comparison(GsTextCommand):
    def run(self, edit: sublime.Edit, tips: list[str] | None = None) -> None:
        settings = self.view.settings()
        replace_comparison = tips is not None
        if tips is None:
            tips = list(settings.get("git_savvy.log_graph_view.branches") or [])
            choices = list(dict.fromkeys([*settings.get("git_savvy.log_graph_view.branch_choices", []), *tips]))
            tips = comparison_tips(choices)
            if not tips:
                return
        else:
            choices = tips

        boundaries = merge_base_boundaries(self, tips)
        if not boundaries:
            return

        if replace_comparison:
            settings.erase("git_savvy.log_graph_view.branch_upstreams")
            settings.set("git_savvy.log_graph_view.follow", short_ref(tips[0]))
        settings.set("git_savvy.log_graph_view.branch_choices", choices + boundaries)
        settings.set("git_savvy.log_graph_view.branches", tips + boundaries)
        settings.set("git_savvy.log_graph_view.all_branches", False)
        settings.set("git_savvy.log_graph_view.overview", False)
        self.view.run_command("gs_log_graph_refresh", {"assume_complete_redraw": True})


def merge_base_boundaries(command: GitCommand, tips: list[str]) -> list[str]:
    try:
        bases = command.git_throwing_silently("merge-base", "--octopus", "--all", "--", *tips).splitlines()
    except GitSavvyError as exc:
        if exc.stderr:
            exc.show_error_panel()
            return []
        bases = []
    if not bases:
        sublime.status_message("No common base for {}".format(", ".join(tips)))
    return [command.to_short_hash(FullHash(base)) + "^!" for base in bases]


class gs_compare_against_reference(GsWindowCommand):
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


class gs_compare_against_branch(GsWindowCommand):
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
