import sublime
from sublime_plugin import WindowCommand, TextCommand

from ..git_command import GitCommand
from ...common import util

LOG_GRAPH_TITLE = "GRAPH"


class GsLogGraphCommand(WindowCommand, GitCommand):

    """
    Open a new window displaying an ASCII-graphic representation
    of the repo's branch relationships.
    """

    def run(self):
        repo_path = self.repo_path
        view = self.window.new_file()
        view.settings().set("git_savvy.log_graph_view", True)
        view.settings().set("git_savvy.repo_path", repo_path)
        view.set_syntax_file("Packages/GitSavvy/syntax/graph.tmLanguage")
        view.set_name(LOG_GRAPH_TITLE)
        view.set_scratch(True)
        view.set_read_only(True)
        view.run_command("gs_log_graph_initialize")


class GsLogGraphInitializeCommand(TextCommand, GitCommand):

    def run(self, edit):
        savvy_settings = sublime.load_settings("GitSavvy.sublime-settings")
        args = savvy_settings.get("git_graph_args")
        branch_graph = self.git(*args)
        self.view.run_command("gs_replace_view_text", {"text": branch_graph, "nuke_cursors": True})


class GsLogGraphActionCommand(TextCommand, GitCommand):

    """
    Checkout the commit at the selected line.
    """

    def run(self, edit, action):
        self.action = action
        sublime.set_timeout_async(self.run_async, 0)

    def run_async(self):
        selections = self.view.sel()
        if len(selections) != 1:
            return

        lines = util.view.get_lines_from_regions(self.view, selections)
        if not lines:
            return
        line = lines[0]

        commit_hash = line.strip(" |*")[:7]
        if self.action == "checkout":
            self.checkout_ref(commit_hash)
            util.view.refresh_gitsavvy(self.view)
        elif self.action == "view":
            self.view.window().run_command("gs_show_commit", {"commit_hash": commit_hash})
