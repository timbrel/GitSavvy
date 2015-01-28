import sublime
from sublime_plugin import WindowCommand, TextCommand

from .base_command import BaseCommand

COMMIT_HELP_TEXT = """

## To make a commit, type your commit message and press SUPER-ENTER.
## To cancel the commit, close the window.
"""


class GgCommitCommand(WindowCommand, BaseCommand):

    def run(self, repo_path=None, include_unstaged=False, amend=False):
        repo_path = repo_path or self.repo_path
        view = self.window.new_file()
        view.settings().set("git_gadget.get_long_text_view", True)
        view.settings().set("git_gadget.commit_view.include_unstaged", include_unstaged)
        view.settings().set("git_gadget.commit_view.amend", amend)
        view.settings().set("git_gadget.repo_path", repo_path)
        view.set_scratch(True)
        view.run_command("gg_commit_initialize_view")


class GgCommitInitializeViewCommand(TextCommand, BaseCommand):

    def run(self, edit):
        if self.view.settings().get("git_gadget.commit_view.amend"):
            last_commit_message = self.git("log", "-1", "--pretty=%B")
            initial_text = last_commit_message + COMMIT_HELP_TEXT
        else:
            initial_text = COMMIT_HELP_TEXT

        self.view.replace(edit, sublime.Region(0, 0), initial_text)
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(0, 0))


class GgCommitViewDoCommitCommand(TextCommand, BaseCommand):

    def run(self, edit):
        view_text = self.view.substr(sublime.Region(0, self.view.size()))
        commit_message = view_text.replace(COMMIT_HELP_TEXT, "")

        if self.view.settings().get("git_gadget.commit_view.include_unstaged"):
            self.add_all_tracked_files()

        if self.view.settings().get("git_gadget.commit_view.amend"):
            self.git("commit", "-q", "--amend", "-F", "-", stdin=commit_message)
        else:
            self.git("commit", "-q", "-F", "-", stdin=commit_message)

        self.view.window().focus_view(self.view)
        self.view.window().run_command("close_file")
