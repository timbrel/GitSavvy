import sublime
from sublime_plugin import WindowCommand, TextCommand

from .base_command import BaseCommand

COMMIT_HELP_TEXT = """

## To make a commit, type your commit message and press SUPER-ENTER.
## To cancel the commit, close the window.
"""


class GgCommitCommand(WindowCommand, BaseCommand):

    def run(self, repo_path=None):
        repo_path = repo_path or self.repo_path
        view = self.window.new_file()
        view.settings().set("git_gadget.get_long_text_view", True)
        view.settings().set("git_gadget.repo_path", repo_path)
        view.set_scratch(True)
        view.run_command("gg_commit_initialize_view")


class GgCommitInitializeViewCommand(TextCommand, BaseCommand):

    def run(self, edit):
        self.view.replace(edit, sublime.Region(0, 0), COMMIT_HELP_TEXT)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(0, 0))


class GgCommitViewDoCommitCommand(TextCommand, BaseCommand):

    def run(self, edit):
        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        commit_message = view_text.replace(COMMIT_HELP_TEXT, "")
        self.git("commit", "-q", "-F", "-", stdin=commit_message)
        self.view.window().focus_view(self.view)
        self.view.window().run_command("close_file")
