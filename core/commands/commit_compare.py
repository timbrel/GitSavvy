import sublime
from sublime_plugin import TextCommand, WindowCommand
import re

from ..git_command import GitCommand
from ...common import util


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"
COMMIT_LINE = re.compile("[%s][ /_\|\-.]*([a-z0-9]{3,})" % COMMIT_NODE_CHAR_OPTIONS)


class GsCompareCommitCommand(WindowCommand, GitCommand):

    """
    Show a view of all commits diff between branches.
    """

    def run(self, base_commit, target_commit=None, file_path=None, title=None):
        self.base_commit = base_commit
        self.target_commit = target_commit or "HEAD"
        self._file_path = file_path
        self.title = title or "COMMIT COMPARISON"
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        # need to get repo_path before the new view is created.
        repo_path = self.repo_path
        view = util.view.get_scratch_view(self, "compare_commit", read_only=True)
        view.settings().set("git_savvy.compare_commit_view.target_commit", self.target_commit)
        view.settings().set("git_savvy.compare_commit_view.base_commit", self.base_commit)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.settings().set("git_savvy.file_path", self._file_path)
        view.settings().set("git_savvy.git_graph_args", self.get_graph_args())
        view.settings().set("word_wrap", False)
        view.set_syntax_file("Packages/GitSavvy/syntax/graph.sublime-syntax")
        view.set_name(self.title)
        view.sel().clear()
        view.run_command("gs_compare_commit_refresh")
        view.run_command("gs_log_graph_navigate")

    def get_graph_args(self):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        args = savvy_settings.get("git_graph_args")
        if self._file_path:
            file_path = self.get_rel_path(self._file_path)
            args = args + ["--", file_path]
        return args


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
        file_path = self.file_path
        if file_path:
            diff_contents = "File: {}\n\n".format(file_path)
        else:
            diff_contents = ""
        diff_contents += "Commits on {} and not on {}\n".format(base_commit, target_commit)
        args = self.view.settings().get("git_savvy.git_graph_args")
        args.insert(1, "{}..{}".format(target_commit, base_commit))
        diff_contents += self.git(*args)
        diff_contents = diff_contents.replace("*", COMMIT_NODE_CHAR)
        diff_contents += "\n\nCommits on {} and not on {}\n".format(target_commit, base_commit)
        args[1] = "{}..{}".format(base_commit, target_commit)
        diff_contents += self.git(*args)
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
        file_path = self.file_path
        self.view.window().run_command("gs_diff", {
            "base_commit": base_commit,
            "target_commit": target_commit,
            "file_path": file_path,
            "disable_stage": True,
            "title": "DIFF: {}..{}".format(base_commit, target_commit)
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


class GsCompareAgainstBranchCommand(WindowCommand, GitCommand):
    def run(self, target_commit=None, file_path=None):
        self._file_path = file_path
        self._target_commit = target_commit
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
        if index == -1:
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


class GsCompareAgainstCommand(WindowCommand, GitCommand):
    def run(self, target_commit=None, file_path=None, current_file=False):
        self._file_path = self.file_path if current_file else file_path
        self._target_commit = target_commit
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        options_array = [
            "Branch",
            "Reference"
        ]

        self.window.show_quick_panel(
            options_array,
            self.on_option_selection,
            flags=sublime.MONOSPACE_FONT,
            selected_index=self.quick_panel_compare_against_idx
        )

    def on_option_selection(self, index):
        if index == -1:
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
