import sublime
from sublime_plugin import TextCommand

from ..git_command import GitCommand
from ...common import util
from ...common import ui

COMMIT_NODE_CHAR = "●"
COMMIT_NODE_CHAR_OPTIONS = "●*"


class GsBranchesDiffCommitHistoryCommand(TextCommand, GitCommand):

    """
    Show a view of all commits diff between branches.
    """

    def run(self, edit):
        sublime.set_timeout_async(self.run_async)

    def run_async(self):
        self.interface = ui.get_interface(self.view.id())
        selection, line = self.interface.get_selection_line()
        if not line:
            return

        segments = line.strip("▸ ").split(" ")
        branch_name = segments[1]

        local_region = self.view.get_regions("git_savvy_interface.branch_list")[0]
        if local_region.contains(selection):
            self.show_commits(branch_name)
            return

        remotes = self.get_remotes()
        for remote_name in remotes:
            remote_region = self.view.get_regions("git_savvy_interface.branch_list_" + remote_name)
            if remote_region and remote_region[0].contains(selection):
                self.show_commits(branch_name, remote=remote_name)
                return

    def show_commits(self, branch_name, remote=None):
        view = util.view.get_scratch_view(self, "branch_commit_history", read_only=True)

        view.settings().set("active_branch_name", self.get_current_branch_name())
        view.settings().set("comparison_branch_name", remote + "/" + branch_name if remote else branch_name)

        view.settings().set("git_savvy.repo_path", self.repo_path)
        view.settings().set("word_wrap", False)
        view.set_syntax_file("Packages/GitSavvy/syntax/graph.sublime-syntax")
        view.set_name("BRANCH COMMIT COMPARISON")
        view.sel().clear()
        view.run_command("gs_branches_diff_commit_history_refresh")


class GsBranchesDiffCommitHistoryRefreshCommand(TextCommand, GitCommand):

    """
    Refresh view of all commits diff between branches.
    """

    def run(self, edit):
        diff_contents = self.get_commit_branch_string()
        self.view.run_command("gs_replace_view_text", {"text": diff_contents})

    def get_commit_branch_string(self):
        branchA = self.view.settings().get("comparison_branch_name")
        branchB = self.view.settings().get("active_branch_name")

        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        args = savvy_settings.get("git_graph_args")
        diff_contents = "Commits on {} and not on {}\n".format(branchA, branchB)
        args.append("{}..{}".format(branchB, branchA))
        diff_contents += self.git(*args)
        diff_contents = diff_contents.replace("*", COMMIT_NODE_CHAR)
        diff_contents += "\n\nCommits on {} and not on {}\n".format(branchB, branchA)
        args.pop()
        args.append("{}..{}".format(branchA, branchB))
        diff_contents += self.git(*args)
        diff_contents = diff_contents.replace("*", COMMIT_NODE_CHAR)
        return diff_contents


class GsBranchesDiffCommitHistoryActionCommand(TextCommand, GitCommand):

    """
    Checkout the commit at the selected line.
    """

    def run(self, edit):
        self.actions = [
            "Show commit",
            "Checkout commit",
            "Cherry-pick commit",
            "Refresh (r)",
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

        # Refresh
        if index == 3:
            util.view.refresh_gitsavvy(self.view)
