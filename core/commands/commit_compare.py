import sublime
from sublime_plugin import TextCommand

from ..git_command import GitCommand
from ...common import util
from ...common import ui


COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"


class GsCompareCommitCommand(TextCommand, GitCommand):

    """
    Show a view of all commits diff between branches.
    """

    def run(self, edit, base_commit, target_commit=None, title="COMMIT COMPARISON"):
        sublime.set_timeout_async(lambda: self.run_async())
        self.base_commit = base_commit
        self.target_commit = target_commit
        self.title = title

    def run_async(self):
        view = util.view.get_scratch_view(self, "compare_commit", read_only=True)

        view.settings().set("git_savvy.compare_commit_view.base_commit", self.base_commit)
        view.settings().set("git_savvy.compare_commit_view.target_commit", self.target_commit)
        view.settings().set("git_savvy.repo_path", self.repo_path)
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
        self.commit_hash = line.strip(" /_\|" + COMMIT_NODE_CHAR_OPTIONS).split(' ')[0]

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

        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        args = savvy_settings.get("git_graph_args")
        diff_contents = "Commits on {} and not on {}\n".format(target_commit, base_commit)
        args.append("{}..{}".format(base_commit, target_commit))
        diff_contents += self.git(*args)
        diff_contents = diff_contents.replace("*", COMMIT_NODE_CHAR)
        diff_contents += "\n\nCommits on {} and not on {}\n".format(base_commit, target_commit)
        args.pop()
        args.append("{}..{}".format(target_commit, base_commit))
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
        self.view.window().run_command("gs_diff", {
            "base_commit": base_commit,
            "target_commit": target_commit,
            "title": "DIFF: {}..{}".format(base_commit, target_commit)
        })
