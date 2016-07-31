import sublime
from sublime_plugin import TextCommand, WindowCommand
import os
import re

from ..git_command import GitCommand
from ...common import util
from ...common import ui
from .log import GsLogBranchCommand


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
COMMIT_LINE = re.compile("[%s][ /_\|\-.]*([a-z0-9]{3,})" % COMMIT_NODE_CHAR_OPTIONS)


class GsCompareCommitCommand(TextCommand, GitCommand):

    """
    Show a view of all commits diff between branches.
    """

    def run(self, edit, base_commit, target_commit=None, file_path=None, title=None):
        self.base_commit = base_commit
        self.target_commit = target_commit or "HEAD"
        self._file_path = file_path
        self.title = title or "COMMIT COMPARISON"
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        view = util.view.get_scratch_view(self, "compare_commit", read_only=True)

        view.settings().set("git_savvy.compare_commit_view.base_commit", self.base_commit)
        view.settings().set("git_savvy.compare_commit_view.target_commit", self.target_commit)
        view.settings().set("git_savvy.repo_path", self.repo_path)
        view.settings().set("git_savvy.compare_commit_view.file_path", self._file_path)
        view.settings().set("word_wrap", False)
        view.set_syntax_file("Packages/GitSavvy/syntax/graph.sublime-syntax")
        view.set_name(self.title)
        view.sel().clear()
        view.run_command("gs_compare_commit_refresh")


class GsCompareCommitActionCommand(TextCommand, GitCommand):

    """
    Checkout the commit at the selected line.
    """

    def run(self, edit):
        self.actions = [
            "Show commit",
            "Checkout commit",
            "Cherry-pick commit"
         ]

        self.selections = self.view.sel()

        lines = util.view.get_lines_from_regions(self.view, self.selections)
        line = lines[0]

        m = COMMIT_LINE.search(line)
        self.commit_hash = m.group(1) if m else ""

        if not len(self.selections) == 1:
            sublime.status_message("You can only do actions on one commit at a time.")
            return

        self.view.window().show_quick_panel(
            self.actions,
            self.on_select_action,
            selected_index=self.quick_panel_branch_diff_history_idx,
            flags=sublime.MONOSPACE_FONT
        )

    def on_select_action(self, index):
        if index == -1:
            return
        self.quick_panel_branch_diff_history_idx = index

        # Show commit
        if index == 0:
            self.view.window().run_command("gs_show_commit", {"commit_hash": self.commit_hash})

        # Checkout commit
        if index == 1:
            self.checkout_ref(self.commit_hash)
            util.view.refresh_gitsavvy(self.view)

        # Cherry-pick  commit
        if index == 2:
            self.view.window().run_command("gs_cherry_pick", {"target_hash": self.commit_hash})


class GsCompareCommitRefreshCommand(TextCommand, GitCommand):

    """
    Refresh view of all commits diff between branches.
    """

    def run(self, edit):
        diff_contents = self.get_commit_branch_string()
        self.view.run_command("gs_replace_view_text", {"text": diff_contents})

    def get_commit_branch_string(self):
        base_commit = self.view.settings().get("git_savvy.compare_commit_view.base_commit")
        target_commit = self.view.settings().get("git_savvy.compare_commit_view.target_commit")
        file_path = self.view.settings().get("git_savvy.compare_commit_view.file_path")
        repo_path = self.view.settings().get("git_savvy.repo_path")

        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        args = savvy_settings.get("git_graph_args")
        if file_path:
            file_path = os.path.realpath(file_path)[len(repo_path)+1:]
            file_args = ["--", file_path]
            diff_contents = "File: {}\n\n".format(file_path)
        else:
            file_args = []
            diff_contents = ""

        diff_contents += "Commits on {} and not on {}\n".format(target_commit, base_commit)
        args.append("{}..{}".format(base_commit, target_commit))
        diff_contents += self.git(*(args + file_args))
        diff_contents = diff_contents.replace("*", COMMIT_NODE_CHAR)
        diff_contents += "\n\nCommits on {} and not on {}\n".format(base_commit, target_commit)
        args[-1] = "{}..{}".format(target_commit, base_commit)
        diff_contents += self.git(*(args + file_args))
        diff_contents = diff_contents.replace("*", COMMIT_NODE_CHAR)
        return diff_contents


class GsCompareCommitShowDiffCommand(TextCommand, GitCommand):

    """
    Refresh view of all commits diff between branches.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        base_commit = self.view.settings().get("git_savvy.compare_commit_view.base_commit")
        target_commit = self.view.settings().get("git_savvy.compare_commit_view.target_commit")
        file_path = self.view.settings().get("git_savvy.compare_commit_view.file_path")
        self.view.window().run_command("gs_diff", {
            "base_commit": base_commit,
            "target_commit": target_commit,
            "file_path": file_path,
            "disable_stage": True,
            "title": "DIFF: {}..{}".format(base_commit, target_commit)
        })


class GsCompareAgainstCommand(WindowCommand, GitCommand):
    def run(self, target_commit=None, file_path=None):
        self._file_path = file_path
        self._target_commit = target_commit
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        options_array = [
            "Branch",
            "Reference"
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
            self.window.run_command("gs_compare_against_branch", {
                "target_commit": self._target_commit,
                "file_path": self._file_path
            })

        if index == 1:
            self.window.run_command("gs_compare_against_reference", {
                "target_commit": self._target_commit,
                "file_path": self._file_path
            })


class GsCompareAgainstReferenceCommand(WindowCommand, GitCommand):
    def run(self, target_commit=None, file_path=None):
        self._file_path = file_path
        self._target_commit = target_commit
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        self.window.show_input_panel("Ref:", "", self.show_diff, None, self.on_cancel)

    def show_diff(self, ref):
        self.window.run_command("gs_compare_commit", {
            "file_path": self._file_path,
            "base_commit": ref,
            "target_commit": self._target_commit
        })

    def on_cancel(self):
        self.window.run_command("gs_compare_against", {
            "target_commit": self._target_commit,
            "file_path": self._file_path
        })


class GsCompareAgainstBranchCommand(GsLogBranchCommand):
    """
    Compare a given commit against a selected branch or selected ref
    """
    def run(self, target_commit=None, file_path=None):
        self._file_path = file_path
        self._target_commit = target_commit
        sublime.set_timeout_async(self.run_async)

    def on_branch_selection(self, index):
        if index < 0:
            self.window.run_command("gs_compare_against", {
                "target_commit": self._target_commit,
                "file_path": self._file_path
            })
            return
        self._selected_branch = self.all_branches[index]
        self.window.run_command("gs_compare_commit", {
            "file_path": self._file_path,
            "base_commit": self._selected_branch,
            "target_commit": self._target_commit
        })


class GsCompareCurrentFileAgainstCommand(WindowCommand, GitCommand):
    def run(self, target_commit=None, file_path=None):
        if not file_path:
            file_path = self.file_path
        self.window.run_command("gs_compare_against", {
            "target_commit": target_commit,
            "file_path": file_path
        })
