import sublime
from sublime_plugin import WindowCommand, TextCommand

from .base_command import BaseCommand

LOG_GRAPH_TITLE = "LOG GRAPH"


class GgLogGraphCommand(WindowCommand, BaseCommand):

    def run(self):
        repo_path = self.repo_path
        view = self.window.new_file()
        view.settings().set("git_gadget.log_graph_view", True)
        view.settings().set("git_gadget.repo_path", repo_path)
        view.set_name(LOG_GRAPH_TITLE)
        view.set_scratch(True)
        view.set_read_only(True)
        view.run_command("gg_log_graph_initialize")


class GgLogGraphInitializeCommand(TextCommand, BaseCommand):

    def run(self, edit):
        branch_graph = self.git("log", "--oneline", "--graph", "--decorate")
        self.view.set_read_only(False)
        self.view.replace(edit, sublime.Region(0, self.view.size()), branch_graph)
        self.view.set_read_only(True)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(0, 0))
